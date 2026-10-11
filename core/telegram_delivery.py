"""Reliable bounded Telegram sender; neither tokens nor message bodies are logged."""
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Access, TelegramOutbound


def _still_authorized(notice):
    identity = notice.identity
    from .telegram_integration import linked_identity
    if linked_identity(identity.kind, identity.owner_id) is None:
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
    if identity.kind == "staff":
        from django.contrib.auth.models import User
        u = User.objects.filter(pk=identity.owner_id, is_active=True).first()
        if not u:
            return False
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
        from marketplace.models import CustomerAccount
        return CustomerAccount.objects.filter(pk=identity.owner_id, active=True).exists()
    return False


def deliver_pending(limit=20):
    if not (settings.TELEGRAM_BOT_ENABLED and settings.TELEGRAM_BOT_TOKEN
            and (settings.TELEGRAM_NOTIFICATIONS_ENABLED or settings.TELEGRAM_RECOVERY_ENABLED)):
        return 0
    delivered = 0
    for _ in range(min(max(int(limit), 0), 100)):
        with transaction.atomic():
            notice = TelegramOutbound.objects.select_for_update(skip_locked=True).filter(
                status="queued",
            ).select_related("identity").order_by("pk").first()
            if not notice:
                break
            if ((notice.category == "recovery" and not settings.TELEGRAM_RECOVERY_ENABLED)
                    or (notice.category != "recovery" and not settings.TELEGRAM_NOTIFICATIONS_ENABLED)
                    or not _still_authorized(notice)):
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
            body = notice.body
        try:
            response = requests.post(
                "https://api.telegram.org/bot" + settings.TELEGRAM_BOT_TOKEN + "/sendMessage",
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
