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

from .telegram_channels import BOT_KINDS, bot_for_account, bot_link

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


def begin_link(kind, owner_id, bot_kind=None, proposed_phone=''):
    """Password re-authenticated website endpoint only. Link remains pending."""
    if kind not in {"staff", "customer"}:
        raise ValueError("Invalid account type")
    bot_kind = bot_kind or bot_for_account(kind)
    if bot_kind not in BOT_KINDS or (bot_kind != "official" and bot_kind != kind):
        raise ValueError("This bot cannot link that KOFAD account type.")
    base = bot_link(bot_kind)
    if not base:
        raise ValueError("The Telegram bot username has not been configured.")
    # A linked Telegram chat does not require a phone. For staff who choose to
    # add a recovery number, require an authentic contact share from that chat.
    if proposed_phone:
        if kind != "staff":
            raise ValueError("Customer phone changes belong in KOFAD account security.")
        from .identity import normalize_ghana_phone
        proposed_phone = normalize_ghana_phone(proposed_phone)
    raw = secrets.token_urlsafe(24)
    now = timezone.now()
    with transaction.atomic():
        TelegramLinkChallenge.objects.filter(
            kind=kind, owner_id=owner_id, bot_kind=bot_kind, used_at__isnull=True
        ).delete()
        TelegramLinkChallenge.objects.create(
            kind=kind, owner_id=owner_id, bot_kind=bot_kind,
            proposed_phone=proposed_phone,
            token_digest=_digest("link", raw),
            expires_at=now + timedelta(minutes=10),
        )
    return base + "?start=link_" + raw


def accept_token(token, user_id, chat_id, bot_kind):
    """Telegram endpoint: do not link until authenticated website confirms code."""
    if bot_kind not in BOT_KINDS or not token or len(token) > 64 or not all(c.isalnum() or c in "_-" for c in token):
        return "Invalid or expired account-link request."
    now = timezone.now()
    with transaction.atomic():
        challenge = TelegramLinkChallenge.objects.select_for_update().filter(
            token_digest=_digest("link", token), bot_kind=bot_kind, used_at__isnull=True,
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
        + (
            "\n\nTo verify the optional mobile number you entered on the website, "
            "send /sharephone and tap 'Share my Telegram number'. "
            "Only the phone actually shared from YOUR Telegram account will be accepted."
            if challenge.proposed_phone else
            "\n\nNo existing mobile number is required to finish connecting this account."
        )
    )


def confirm_link(kind, owner_id, code, bot_kind=None):
    """Finish pairing only from existing authenticated website session."""
    bot_kind = bot_kind or bot_for_account(kind)
    now = timezone.now()
    with transaction.atomic():
        challenge = TelegramLinkChallenge.objects.select_for_update().filter(
            kind=kind, owner_id=owner_id, bot_kind=bot_kind, used_at__isnull=True, expires_at__gt=now,
            chat_id__isnull=False, telegram_user_id__isnull=False,
        ).order_by("-pk").first()
        if not challenge or challenge.attempts >= 5:
            return False
        if challenge.proposed_phone and not challenge.phone_verified_at:
            return False
        challenge.attempts += 1
        challenge.save(update_fields=["attempts"])
        if not isinstance(code, str) or len(code.strip()) != 6 or not code.strip().isdigit():
            return False
        expected = _digest(str(challenge.pk) + ":confirm", code.strip())
        if not hmac.compare_digest(challenge.confirmation_digest, expected):
            return False
        # Never steal a Telegram account linked to some other KOFAD identity.
        if TelegramIdentity.objects.filter(bot_kind=bot_kind, kind=kind, telegram_user_id=challenge.telegram_user_id).exclude(
            kind=kind, owner_id=owner_id
        ).exists():
            return False
        # A replaced link invalidates the previous link and its unsent notices.
        existing = TelegramIdentity.objects.select_for_update().filter(kind=kind, owner_id=owner_id, bot_kind=bot_kind).first()
        if existing:
            existing.delete()
        try:
            TelegramIdentity.objects.create(
                kind=kind, owner_id=owner_id, bot_kind=bot_kind, auth_stamp=credential_stamp(kind, owner_id),
                telegram_user_id=challenge.telegram_user_id,
                chat_id=challenge.chat_id, verified_at=now,
            )
        except IntegrityError:
            return False
        if kind == "staff" and challenge.proposed_phone and challenge.phone_verified_at:
            from .models import Access
            from django.contrib.auth.models import User
            if not User.objects.filter(pk=owner_id, is_active=True).exists():
                return False
            access, _ = Access.objects.get_or_create(user_id=owner_id)
            access.recovery_phone = challenge.proposed_phone
            access.save(update_fields=["recovery_phone"])
        challenge.used_at = now
        challenge.save(update_fields=["used_at"])
        return True



def awaiting_phone_contact(bot_kind, telegram_user_id, chat_id):
    """Only a KOFAD-password-authenticated, Telegram-accepted pairing may ask for contact."""
    if not settings.TELEGRAM_ACCOUNT_LINK_ENABLED:
        return False
    return TelegramLinkChallenge.objects.filter(
        bot_kind=bot_kind, kind="staff",
        telegram_user_id=telegram_user_id, chat_id=chat_id,
        proposed_phone__gt="", phone_verified_at__isnull=True,
        used_at__isnull=True, expires_at__gt=timezone.now(),
    ).exists()


def accept_telegram_contact(bot_kind, telegram_user_id, chat_id, contact):
    """A claimed text number or someone else's forwarded contact is never proof."""
    if not settings.TELEGRAM_ACCOUNT_LINK_ENABLED:
        return "Phone verification is currently disabled."
    if (not isinstance(contact, dict) or type(contact.get("user_id")) is not int
            or contact["user_id"] != telegram_user_id):
        return "Only your OWN Telegram contact, shared using the bot button, can verify the number."
    from .identity import normalize_ghana_phone
    from django.core.exceptions import ValidationError
    try:
        phone = normalize_ghana_phone(contact.get("phone_number", ""))
    except ValidationError:
        return "That is not a valid Ghana phone number. Enter your registered Ghana number on KOFAD."
    with transaction.atomic():
        challenge = TelegramLinkChallenge.objects.select_for_update().filter(
            bot_kind=bot_kind, kind="staff",
            telegram_user_id=telegram_user_id, chat_id=chat_id,
            proposed_phone__gt="", used_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).order_by("-created_at").first()
        if not challenge:
            return "Start a new account pairing from your signed-in KOFAD staff portal first."
        if not hmac.compare_digest(phone, challenge.proposed_phone):
            return (
                "The number shared by your Telegram account differs from the one "
                "you entered on the KOFAD website. Correct the website number and restart pairing."
            )
        challenge.phone_verified_at = timezone.now()
        challenge.save(update_fields=["phone_verified_at"])
    return (
        "Your Telegram phone has been confirmed for this KOFAD staff link. "
        "Return to your KOFAD website and enter the six-digit pairing code "
        "already sent in this chat. No SMS is required."
    )


def linked_identity(kind, owner_id, bot_kind=None):
    bot_kind = bot_kind or bot_for_account(kind)
    identity = TelegramIdentity.objects.filter(
        kind=kind, owner_id=owner_id, bot_kind=bot_kind, revoked_at__isnull=True,
    ).first()
    if not identity:
        return None
    latest = credential_stamp(kind, owner_id)
    return identity if latest and hmac.compare_digest(identity.auth_stamp, latest) else None


def queue_notice(identity, category, source_key, body, branch=None):
    """No messages without a linked opted-in account."""
    if not identity or identity.revoked_at or not identity.notifications_enabled:
        return False
    allowed_bot = {
        "recovery": "official", "order": "customer", "task": "staff",
        "marketing": "customer", "closing": "staff",
        "digest": "staff", "critical": "staff", "stock": "staff",
    }.get(category)
    if not allowed_bot or identity.bot_kind != allowed_bot:
        return False
    if category == "marketing" and not identity.marketing_opt_in:
        return False
    if category in {"closing", "digest"} and not identity.closing_opt_in:
        return False
    if category == "critical" and not identity.critical_opt_in:
        return False
    if category == "stock" and not identity.stock_opt_in:
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



def queue_periodic_digest(now=None):
    """Weekly and monthly reports, deterministic and source-key deduplicated."""
    if not settings.TELEGRAM_NOTIFICATIONS_ENABLED:
        return 0
    from datetime import timedelta
    from decimal import Decimal, InvalidOperation

    from django.contrib.auth.models import User
    from .models import Access, Branch, Closing

    now = timezone.localtime(now or timezone.now())
    # Same scheduling convention as KOFAD's management email reviews.
    if now.hour != 8:
        return 0
    today = now.date()
    periods = []
    if today.weekday() == 0:
        periods.append(("weekly", today - timedelta(days=7), today - timedelta(days=1)))
    if today.day == 1:
        periods.append(("monthly", (today.replace(day=1) - timedelta(days=1)).replace(day=1),
                        today - timedelta(days=1)))
    if not periods:
        return 0

    def money(value):
        try:
            return Decimal(str(value or 0))
        except (ValueError, TypeError, InvalidOperation):
            return Decimal("0")

    sent = 0
    for user in User.objects.filter(is_active=True):
        identity = linked_identity("staff", user.pk)
        if not identity or not identity.notifications_enabled or not identity.closing_opt_in:
            continue
        if not (user.is_superuser or any(user.has_perm("core." + p) for p in (
                "manage_company", "view_reports", "operate_finance"))):
            continue
        access = Access.objects.filter(user=user).first()
        if not access:
            continue
        branches = Branch.objects.filter(active=True) if user.is_superuser else access.branches.filter(active=True)
        if not branches.exists():
            continue
        for label, first, last in periods:
            rows = Closing.objects.filter(
                branch__in=branches, verified_by__isnull=False, date__gte=first,
                date__lte=last,
            ).values_list("summary", flat=True)[:3000]
            totals = {"sales": Decimal("0"), "expenses": Decimal("0"), "collections": Decimal("0")}
            count = 0
            for item in rows:
                item = item if isinstance(item, dict) else {}
                totals["sales"] += money(item.get("sales_total"))
                totals["expenses"] += money(item.get("expenses_total"))
                totals["collections"] += money(item.get("debt_collections"))
                count += 1
            body = (
                f"KOFAD {label.upper()} VERIFIED CLOSING REVIEW\n"
                f"Period: {first:%d %b %Y} – {last:%d %b %Y}\n"
                f"Locations in your access scope: {branches.count()}\n"
                f"Closings examined: {count}\n"
                f"Recorded sales: GHS {totals['sales']:,.2f}\n"
                f"Expenses: GHS {totals['expenses']:,.2f}\n"
                f"Debt collections: GHS {totals['collections']:,.2f}\n\n"
                "This is a verified-closing summary, not net profit. "
                "Check your staff dashboard for complete financial analysis."
            )
            sent += int(queue_notice(identity, "digest",
                                     f"tg:digest:{label}:{first.isoformat()}:{user.pk}", body))
    return sent



def queue_inventory_alerts(now=None):
    """Deduplicated once-daily low/out-of-stock notices to opted-in staff only."""
    if not settings.TELEGRAM_NOTIFICATIONS_ENABLED:
        return 0
    from django.contrib.auth.models import User
    from .models import Access, Branch, Product, Stock

    now = timezone.localtime(now or timezone.now())
    if now.hour != 8:
        return 0
    today = now.date()
    count = 0
    for branch in Branch.objects.filter(active=True):
        stock = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
        products = Product.objects.filter(active=True)
        out_count = 0
        low_count = 0
        for p in products.iterator():
            qty = stock.get(p.pk, 0)
            if qty <= 0:
                out_count += 1
            elif qty <= p.reorder_level:
                low_count += 1
        if not (out_count or low_count):
            continue
        body = (
            f"KOFAD INVENTORY ALERT\n"
            f"Branch: {branch.name}\n"
            f"Out of stock: {out_count} product(s)\n"
            f"At or below reorder level: {low_count} product(s)\n"
            "Sign into your authorized KOFAD inventory workspace to review, "
            "approve and execute any restock. This message does not authorize purchases."
        )
        for user in User.objects.filter(is_active=True):
            if not (user.is_superuser or user.has_perm("core.operate_inventory")
                    or user.has_perm("core.manage_company")):
                continue
            access = Access.objects.filter(user=user).first()
            if not access or not (user.is_superuser or access.branches.filter(pk=branch.pk).exists()):
                continue
            identity = linked_identity("staff", user.pk)
            if identity:
                count += int(queue_notice(
                    identity, "stock", f"tg:stock:{branch.pk}:{today.isoformat()}:{user.pk}",
                    body, branch=branch,
                ))
    return count



def queue_pending_closing_reviews(now=None):
    """One actionable notice for each unverified prior-day closing, not generic spam.

    Queue only verified KOFAD staff identities with reporting permission and
    opt-in. A separate delivery check suppresses notices already resolved.
    """
    if not settings.TELEGRAM_NOTIFICATIONS_ENABLED:
        return 0
    from datetime import timedelta
    from django.contrib.auth.models import User
    from .models import Access, Closing

    now = timezone.localtime(now or timezone.now())
    if now.hour != 8:
        return 0
    target_date = now.date() - timedelta(days=1)
    count = 0
    for closing in Closing.objects.filter(
        date=target_date, verified_by__isnull=True,
    ).select_related("branch")[:500]:
        for user in User.objects.filter(is_active=True):
            if not (user.is_superuser or any(user.has_perm("core." + perm) for perm in (
                "manage_company", "view_reports", "operate_finance"
            ))):
                continue
            access = Access.objects.filter(user=user).first()
            if not access or access.force_password_change or access.must_change_password:
                continue
            if not (user.is_superuser or access.branches.filter(pk=closing.branch_id).exists()):
                continue
            identity = linked_identity("staff", user.pk)
            if not identity:
                continue
            body = (
                "KOFAD CLOSING REQUIRES REVIEW\n"
                f"Branch: {closing.branch.name}\n"
                f"Closing date: {closing.date:%d %b %Y}\n"
                "This closing has not yet been verified. If you are responsible, "
                "check the record in your KOFAD staff portal and complete the normal approval process.\n"
                "https://staff.kofadimpex.com/workspace/"
            )
            count += int(queue_notice(
                identity, "closing", f"tg:review:{closing.pk}:{user.pk}", body,
                branch=closing.branch,
            ))
    return count



def queue_weekly_customer_showcase(now=None):
    """Consent-only weekly message with actual published FEATURED products.

    No guessed discounts, unapproved offers, or arbitrary phone-number lookup.
    Send only to already linked, verified and marketing-opted-in customers.
    Deduplicate by ISO week and customer ID.
    """
    if not settings.TELEGRAM_NOTIFICATIONS_ENABLED:
        return 0
    from marketplace.models import MarketListing
    from .models import TelegramIdentity

    now = timezone.localtime(now or timezone.now())
    if now.weekday() != 0 or now.hour != 10:
        return 0
    published = MarketListing.objects.filter(
        enabled=True, featured=True, product__active=True,
    ).select_related("product").order_by("sort_order", "product__name")[:3]
    rows = []
    for listing in published:
        price = listing.market_price
        if not price or price <= 0:
            continue
        rows.append(
            f"{listing.display_name[:80]} — GHS {price:,.2f} / {listing.selling_label[:25]}\n"
            f"https://market.kofadimpex.com/market/products/{listing.product_id}/"
        )
    if not rows:
        return 0
    body = (
        "KOFAD MARKET — FEATURED THIS WEEK\n\n"
        + "\n\n".join(rows)
        + "\n\nPrices and availability can change. Check the live listing before ordering.\n"
        "You opted in to KOFAD Telegram promotions. Reply /promos_off at any time."
    )
    iso_year, iso_week, _ = now.date().isocalendar()
    count = 0
    for identity in TelegramIdentity.objects.filter(
        bot_kind="customer", kind="customer", revoked_at__isnull=True,
        notifications_enabled=True, marketing_opt_in=True,
    ).iterator(chunk_size=200):
        if linked_identity("customer", identity.owner_id, bot_kind="customer") is None:
            continue
        from marketplace.models import CustomerAccount
        if not CustomerAccount.objects.filter(
            pk=identity.owner_id, active=True, verified_at__isnull=False,
        ).exists():
            continue
        count += int(queue_notice(
            identity, "marketing",
            f"tg:featured:{iso_year}-W{iso_week:02d}:{identity.owner_id}",
            body,
        ))
    return count
