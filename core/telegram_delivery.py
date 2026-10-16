"""Reliable bounded Telegram sender; neither tokens nor message bodies are logged."""
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Access, TelegramOutbound
from .telegram_channels import BOT_KINDS, bot_can_send, bot_token


def _still_authorized(notice):
    identity = notice.identity
    # Always re-read opt-in and revocation flags, even if a queued object was
    # prefetched before the recipient changed notification preferences.
    identity.refresh_from_db()
    from .telegram_integration import linked_identity
    current = linked_identity(identity.kind, identity.owner_id, bot_kind=identity.bot_kind)
    if current is None or current.pk != identity.pk:
        # An old or replaced Telegram chat must never receive queued messages.
        return False
    expected_bot = {
        "recovery": "official", "order": "customer", "task": "staff", "welcome": "staff",
        "marketing": "customer", "closing": "staff",
        "digest": "staff", "critical": "staff", "stock": "staff",
    }.get(notice.category)
    if not expected_bot or identity.bot_kind != expected_bot:
        return False
    if identity.revoked_at or (not identity.notifications_enabled and notice.category != "recovery"):
        return False
    if notice.category == "recovery" and not identity.recovery_opt_in:
        return False
    if notice.category == "marketing" and not identity.marketing_opt_in:
        return False
    if notice.category in {"closing", "digest"} and not identity.closing_opt_in:
        return False
    if notice.category == "critical" and not identity.critical_opt_in:
        return False
    if notice.category == "stock" and not identity.stock_opt_in:
        return False
    if notice.category == "closing" and notice.source_key.startswith("tg:review:"):
        # A closing can be approved after this reminder was queued. Never send
        # stale "action needed" messages to staff once approval is recorded.
        from .models import Closing
        reference = notice.source_key.split(":")
        if len(reference) != 4 or not Closing.objects.filter(
            pk=reference[2], branch_id=notice.branch_id,
            verified_by__isnull=True,
        ).exists():
            return False
    if identity.kind == "staff":
        from django.contrib.auth.models import User
        u = User.objects.filter(pk=identity.owner_id, is_active=True).first()
        if not u:
            return False
        if notice.category == "welcome":
            from .telegram_intelligence import staff_welcome
            # A role/branch change invalidates a pending onboarding notice.
            return notice.body == staff_welcome(u)
        if notice.category == "task":
            from .telegram_intelligence import account_tasks
            # Permission revocation or completed tasks must invalidate pending
            # reports; do not send a stale stored role briefing.
            return bool(notice.body == account_tasks(u) and
                        notice.body.startswith("KOFAD OPERATIONAL PRIORITIES"))
        if notice.category == "stock":
            return (bool(u.is_superuser or u.has_perm("core.operate_inventory")
                         or u.has_perm("core.manage_company")) and
                    (u.is_superuser or bool(notice.branch_id and
                    Access.objects.filter(user=u, branches=notice.branch).exists())))
        if notice.category in {"closing", "critical", "digest"}:
            authorized = bool(u.is_superuser or u.has_perm("core.manage_company")
                              or u.has_perm("core.view_reports") or u.has_perm("core.operate_finance"))
            return authorized and (u.is_superuser or bool(
                (Access.objects.filter(user=u, branches=notice.branch).exists()
                 if notice.branch_id else Access.objects.filter(user=u, branches__active=True).exists())
            ))
        return True
    if identity.kind == "customer":
        from marketplace.models import CustomerAccount, OnlineOrder
        if not CustomerAccount.objects.filter(
            pk=identity.owner_id, active=True, verified_at__isnull=False,
        ).exists():
            return False
        if notice.category == "order" and notice.source_key.startswith("tg:order:"):
            # For real order-event notices, never send stale "paid" or
            # "awaiting payment" text after the financial state changes.
            # Other explicit linked-account order notices keep their existing
            # opt-in/credential checks and are not subject to this format.
            parts = notice.source_key.split(":")
            if len(parts) != 4 or parts[0:2] != ["tg", "order"]:
                return False
            order = OnlineOrder.objects.filter(
                pk=parts[2], customer_id=identity.owner_id,
            ).first()
            if not order:
                return False
            event = parts[3]
            if event == "paid":
                return order.payment_status == "paid"
            if event == "awaiting_payment":
                return (order.status == "awaiting_payment"
                        and order.payment_status not in {"paid", "refunded"})
            return order.status == event and order.payment_status == "paid"
        return True
    return False


def deliver_pending(limit=20):
    if not (settings.TELEGRAM_BOT_ENABLED and
            (settings.TELEGRAM_NOTIFICATIONS_ENABLED or settings.TELEGRAM_RECOVERY_ENABLED)):
        return 0
    delivered = 0
    for _ in range(min(max(int(limit), 0), 100)):
        with transaction.atomic():
            active_bots = [kind for kind in BOT_KINDS if bot_can_send(kind)]
            pending = TelegramOutbound.objects.filter(status="queued", identity__bot_kind__in=active_bots)
            if not settings.TELEGRAM_NOTIFICATIONS_ENABLED:
                pending = pending.filter(category="recovery")
            if not settings.TELEGRAM_RECOVERY_ENABLED:
                pending = pending.exclude(category="recovery")
            notice = pending.select_for_update(skip_locked=True).select_related("identity").order_by("pk").first()
            if not notice:
                break
            if not _still_authorized(notice):
                notice.status = "suppressed"
                notice.save(update_fields=["status"])
                continue
            # Reserve first: a timeout after remote acceptance must never
            # automatically cause a duplicate critical report or OTP.
            notice.status = "unknown"
            notice.attempts += 1
            notice.save(update_fields=["status", "attempts"])
            notice_id = notice.pk
            chat_id = notice.identity.chat_id
            recipient_bot_token = bot_token(notice.identity.bot_kind)
            body = notice.body
        try:
            response = requests.post(
                "https://api.telegram.org/bot" + recipient_bot_token + "/sendMessage",
                json={"chat_id": chat_id, "text": body, "disable_web_page_preview": True},
                timeout=12,
            )
            ok = response.status_code == 200 and response.json().get("ok") is True
        except (requests.RequestException, ValueError):
            ok = False
        if ok:
            TelegramOutbound.objects.filter(pk=notice_id, status="unknown").update(
                status="sent", sent_at=timezone.now(), last_error="",
                **({"body": ""} if notice.category == "recovery" else {}),
            )
            delivered += 1
        else:
            TelegramOutbound.objects.filter(pk=notice_id, status="unknown").update(
                last_error="Submission uncertain or rejected; requires manual review",
                **({"body": ""} if notice.category == "recovery" else {}),
            )
    return delivered



def purge_old_telegram():
    """Bound storage of webhook replay IDs, expired codes and sent notices."""
    from datetime import timedelta
    from .models import TelegramLinkChallenge, TelegramRecoveryChallenge, TelegramUpdate

    now = timezone.now()
    TelegramUpdate.objects.filter(received_at__lt=now - timedelta(days=30)).delete()
    TelegramLinkChallenge.objects.filter(expires_at__lt=now - timedelta(days=2)).delete()
    TelegramRecoveryChallenge.objects.filter(expires_at__lt=now - timedelta(days=2)).delete()
    TelegramOutbound.objects.filter(created_at__lt=now - timedelta(days=90)).exclude(
        status="queued",
    ).delete()
