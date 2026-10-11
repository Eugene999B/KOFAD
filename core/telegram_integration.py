"""Verified KOFAD–Telegram pairing and opt-in notification queue."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.conf import settings
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import TelegramIdentity, TelegramLinkChallenge, TelegramOutbound

BOT = "https://t.me/KOFADIMPEXBOT"


def credential_stamp(kind, owner_id):
    """Invalidate all linked Telegram access when the KOFAD credential changes."""
    if kind == "staff":
        from django.contrib.auth.models import User
        from .models import Access
        account = User.objects.filter(pk=owner_id, is_active=True).first()
        if not account:
            return ""
        access = Access.objects.filter(user=account).first()
        version = access.session_version if access else 0
        return _digest("identity", f"staff:{owner_id}:{account.password}:{version}")
    if kind == "customer":
        from marketplace.models import CustomerAccount
        account = CustomerAccount.objects.filter(pk=owner_id, active=True).first()
        if not account:
            return ""
        material = "customer:" + str(owner_id) + ":" + account.password_hash + ":" + (account.phone or "")
        return _digest("identity", material)
    return ""


def _digest(label, value):
    return salted_hmac("kofad-telegram-" + label, value, algorithm="sha256").hexdigest()


def begin_link(kind, owner_id):
    """Password re-authenticated website endpoint only. Link remains pending."""
    if kind not in {"staff", "customer"}:
        raise ValueError("Invalid account type")
    raw = secrets.token_urlsafe(24)
    now = timezone.now()
    with transaction.atomic():
        TelegramLinkChallenge.objects.filter(
            kind=kind, owner_id=owner_id, used_at__isnull=True
        ).delete()
        TelegramLinkChallenge.objects.create(
            kind=kind, owner_id=owner_id,
            token_digest=_digest("link", raw),
            expires_at=now + timedelta(minutes=10),
        )
    return BOT + "?start=link_" + raw


def accept_token(token, user_id, chat_id):
    """Telegram endpoint: do not link until authenticated website confirms code."""
    if not token or len(token) > 64 or not all(c.isalnum() or c in "_-" for c in token):
        return "Invalid or expired account-link request."
    now = timezone.now()
    with transaction.atomic():
        challenge = TelegramLinkChallenge.objects.select_for_update().filter(
            token_digest=_digest("link", token), used_at__isnull=True,
            expires_at__gt=now,
        ).first()
        if not challenge:
            return "This link is invalid or expired. Start a new request in your KOFAD account."
        if challenge.telegram_user_id and challenge.telegram_user_id != user_id:
            return "This link has already been opened by another Telegram account."
        code = f"{secrets.randbelow(1000000):06d}"
        challenge.telegram_user_id = user_id
        challenge.chat_id = chat_id
        challenge.confirmation_digest = _digest(str(challenge.pk) + ":confirm", code)
        challenge.attempts = 0
        challenge.save(update_fields=["telegram_user_id", "chat_id", "confirmation_digest", "attempts"])
    return (
        "KOFAD secure account connection\n\n"
        "Return to the KOFAD website where you are signed in and enter this code:\n"
        + code
        + "\n\nExpires in 10 minutes. Do not share this code. "
        "Your account is NOT connected until you confirm on the website."
    )


def confirm_link(kind, owner_id, code):
    """Finish pairing only from existing authenticated website session."""
    now = timezone.now()
    with transaction.atomic():
        challenge = TelegramLinkChallenge.objects.select_for_update().filter(
            kind=kind, owner_id=owner_id, used_at__isnull=True, expires_at__gt=now,
            chat_id__isnull=False, telegram_user_id__isnull=False,
        ).order_by("-pk").first()
        if not challenge or challenge.attempts >= 5:
            return False
        challenge.attempts += 1
        challenge.save(update_fields=["attempts"])
        if not isinstance(code, str) or len(code.strip()) != 6 or not code.strip().isdigit():
            return False
        expected = _digest(str(challenge.pk) + ":confirm", code.strip())
        if not hmac.compare_digest(challenge.confirmation_digest, expected):
            return False
        # Never steal a Telegram account linked to some other KOFAD identity.
        if TelegramIdentity.objects.filter(telegram_user_id=challenge.telegram_user_id).exclude(
            kind=kind, owner_id=owner_id
        ).exists():
            return False
        # A replaced link invalidates the previous link and its unsent notices.
        existing = TelegramIdentity.objects.select_for_update().filter(kind=kind, owner_id=owner_id).first()
        if existing:
            existing.delete()
        try:
            TelegramIdentity.objects.create(
                kind=kind, owner_id=owner_id, auth_stamp=credential_stamp(kind, owner_id),
                telegram_user_id=challenge.telegram_user_id,
                chat_id=challenge.chat_id, verified_at=now,
            )
        except IntegrityError:
            return False
        challenge.used_at = now
        challenge.save(update_fields=["used_at"])
        return True


def linked_identity(kind, owner_id):
    identity = TelegramIdentity.objects.filter(
        kind=kind, owner_id=owner_id, revoked_at__isnull=True,
    ).first()
    if not identity:
        return None
    latest = credential_stamp(kind, owner_id)
    return identity if latest and hmac.compare_digest(identity.auth_stamp, latest) else None


def queue_notice(identity, category, source_key, body, branch=None):
    """No messages without a linked opted-in account."""
    if not identity or identity.revoked_at or not identity.notifications_enabled:
        return False
    if category == "marketing" and not identity.marketing_opt_in:
        return False
    if category == "closing" and not identity.closing_opt_in:
        return False
    if category == "critical" and not identity.critical_opt_in:
        return False
    if len(body) > 3900 or not body:
        return False
    _, created = TelegramOutbound.objects.get_or_create(
        source_key=source_key[:180],
        defaults={"identity": identity, "category": category, "body": body, "branch": branch},
    )
    return created


def queue_order_update(order, event):
    identity = linked_identity("customer", order.customer_id)
    if not identity or not order.customer.active or not order.customer.verified_at:
        return False
    label = dict(order.STATUSES).get(event, event.replace("_", " ").title())
    body = (
        f"KOFAD order {order.public_reference}\n"
        f"Update: {label}\n"
        "Sign in to your KOFAD Market account to verify details:\n"
        "https://market.kofadimpex.com/market/orders/\n"
        "Never share payment PINs or OTPs in Telegram."
    )
    return queue_notice(identity, "order", f"tg:order:{order.pk}:{event}", body)


def queue_closing_update(closing):
    from decimal import Decimal, InvalidOperation

    from django.contrib.auth.models import User
    from .models import Access

    def money(value):
        try:
            return Decimal(str(value or 0))
        except (InvalidOperation, ValueError, TypeError):
            return Decimal("0")

    # Only verified closings; a submitted but unverified closing is not authoritative.
    if not closing.verified_by_id:
        return 0
    variance = money((closing.counted or {}).get("cash")) - money((closing.expected or {}).get("cash"))
    from .models import Company
    company = Company.objects.first()
    threshold = max(money(getattr(company, "closing_tolerance", 0)),
                    money(getattr(settings, "NOTIFICATION_CRITICAL_VARIANCE_GHS", 500)))
    critical = abs(variance) > 0 and abs(variance) >= threshold
    n = closing.summary or {}
    body = (
        f"KOFAD VERIFIED DAILY CLOSING\n"
        f"Branch: {closing.branch.name}\nDate: {closing.date:%d %b %Y}\n"
        f"Sales: GHS {money(n.get('sales_total')):,.2f}\n"
        f"Expenses: GHS {money(n.get('expenses_total')):,.2f}\n"
        f"Collections: GHS {money(n.get('debt_collections')):,.2f}\n"
        f"Cash variance: GHS {variance:+,.2f}\n"
        + ("CRITICAL: Investigate the cash variance.\n" if critical else "")
        + "\nNumbers are from KOFAD's closing record, NOT a profit calculation.\n"
        "Review details in your authorized staff portal."
    )
    count = 0
    users = User.objects.filter(is_active=True)
    for user in users:
        if not (user.is_superuser or user.has_perm("core.manage_company") or user.has_perm("core.view_reports")
                or user.has_perm("core.operate_finance")):
            continue
        access = Access.objects.filter(user=user).first()
        if not access or not (user.is_superuser or access.branches.filter(pk=closing.branch_id).exists()):
            continue
        identity = linked_identity("staff", user.pk)
        if not identity:
            continue
        category = "critical" if critical and identity.critical_opt_in else "closing"
        key = f"tg:closing:{closing.pk}:{category}:{user.pk}"
        count += int(queue_notice(identity, category, key, body, branch=closing.branch))
    return count
