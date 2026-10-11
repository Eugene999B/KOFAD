"""Permission-scoped Telegram conversations without external model providers."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from .telegram_bot import COMPANY, HELP, MARKET, STAFF, command_reply
from .telegram_integration import accept_token, linked_identity, credential_stamp


def _money(value):
    try:
        return Decimal(str(value or 0))
    except (ValueError, TypeError, InvalidOperation):
        return Decimal("0")


def _account(identity):
    if identity.kind == "staff":
        return User.objects.filter(pk=identity.owner_id, is_active=True).first()
    if identity.kind == "customer":
        from marketplace.models import CustomerAccount
        return CustomerAccount.objects.filter(pk=identity.owner_id, active=True).first()
    return None


def _authorized_branches(user):
    from .models import Access, Branch
    if not (user.is_superuser or any(user.has_perm("core." + p) for p in (
            "manage_company", "view_reports", "operate_finance"))):
        return Branch.objects.none()
    access = Access.objects.filter(user=user).first()
    if not access or access.must_change_password or access.force_password_change:
        return Branch.objects.none()
    if user.is_superuser:
        return Branch.objects.filter(active=True)
    return access.branches.filter(active=True)


def _staff_analysis(user, days=7):
    from .models import Closing
    branches = _authorized_branches(user)
    if not branches.exists():
        return "Your KOFAD account is not authorized to view management reports."
    today = timezone.localdate()
    closings = Closing.objects.filter(
        branch__in=branches, verified_by__isnull=False,
        date__gte=today - timedelta(days=days - 1), date__lte=today,
    ).select_related("branch").order_by("-date")[:200]
    totals = {"sales": Decimal("0"), "expenses": Decimal("0"), "collections": Decimal("0")}
    count, most_recent = 0, None
    for item in closings:
        n = item.summary or {}
        totals["sales"] += _money(n.get("sales_total"))
        totals["expenses"] += _money(n.get("expenses_total"))
        totals["collections"] += _money(n.get("debt_collections"))
        count += 1
        if most_recent is None:
            most_recent = item
    return (
        f"KOFAD VERIFIED CLOSING INTELLIGENCE — last {days} days\n"
        f"Authorized business locations: {branches.count()}\n"
        f"Verified closings reviewed: {count}\n"
        f"Recorded sales: GHS {totals['sales']:,.2f}\n"
        f"Expenses: GHS {totals['expenses']:,.2f}\n"
        f"Debt collections: GHS {totals['collections']:,.2f}\n"
        + (f"Latest verified closing: {most_recent.branch.name}, {most_recent.date:%d %b %Y}\n" if most_recent else "")
        + "\nThese figures are closing-record totals, not a profit or cash balance calculation. "
        "Incomplete/unverified closings are excluded. Reopen the staff dashboard for details."
    )


def _customer_orders(customer, requested_reference=""):
    from marketplace.models import OnlineOrder
    orders = OnlineOrder.objects.filter(customer=customer).order_by("-pk")
    if requested_reference:
        requested = requested_reference.strip().upper()[:32]
        orders = orders.filter(public_reference__iexact=requested)
    else:
        orders = orders[:5]
    rows = list(orders)
    if not rows:
        return "No matching order was found in your verified KOFAD account."
    chunks = [f"{o.public_reference}: {o.get_status_display()} (payment: {o.get_payment_status_display()})"
              for o in rows]
    return "Your KOFAD orders:\n" + "\n".join(chunks) + (
        "\nFull delivery details and receipts:\n" + MARKET + "/market/orders/"
    )


def _product_search(query):
    from django.db.models import Q
    from marketplace.models import MarketListing
    query = query.strip()[:80]
    if len(query) < 2:
        return "Type /search followed by at least two letters from the product name."
    matches = MarketListing.objects.filter(enabled=True, product__active=True).filter(
        Q(title__icontains=query) | Q(product__name__icontains=query),
    ).select_related("product")[:5]
    items = [
        f"{p.display_name[:80]} — GHS {p.market_price:.2f} / {p.selling_label[:35]}\n"
        + MARKET + f"/market/products/{p.pk}/"
        for p in matches
    ]
    return ("\n\n".join(items) if items else "No matching published products were found.") + (
        "\nPrices and stock must be confirmed at checkout."
    )


def reply_for(text, chat_id, user_id, bot_kind="official"):
    """Separate commands and identity authority across three Telegram bots.

    Official bot is the ONLY bot authorized for password-recovery guidance.
    Customer bot NEVER exposes staff intelligence; staff bot NEVER exposes a
    customer's orders. No Telegram phone or handle proves KOFAD account access.
    """
    import hmac

    from .telegram_channels import BOT_KINDS, bot_link
    from .models import TelegramIdentity

    if bot_kind not in BOT_KINDS:
        return "Unknown KOFAD service."
    stripped = (text if isinstance(text, str) else "").strip()[:2000]
    if not stripped:
        return "Send /help to see available KOFAD commands."
    if stripped.startswith("/start link_"):
        if not settings.TELEGRAM_ACCOUNT_LINK_ENABLED:
            return "Secure Telegram account linking is temporarily unavailable."
        token = stripped.split("link_", 1)[1].split(maxsplit=1)[0]
        return accept_token(token, user_id, chat_id, bot_kind=bot_kind)

    head, _, tail = stripped.partition(" ")
    command = head.lower().split("@", 1)[0].lstrip("/")
    plain = stripped.casefold()
    identity = None
    if settings.TELEGRAM_ACCOUNT_LINK_ENABLED:
        identity = TelegramIdentity.objects.filter(
            bot_kind=bot_kind, telegram_user_id=user_id, chat_id=chat_id,
            revoked_at__isnull=True,
        ).first()
        if identity:
            stamp = credential_stamp(identity.kind, identity.owner_id)
            if not stamp or not hmac.compare_digest(identity.auth_stamp, stamp):
                identity = None

    staff_url = STAFF + "/integrations/telegram/"
    market_url = MARKET + "/market/integrations/telegram/"
    official_staff_url = STAFF + "/integrations/telegram/security/"
    official_market_url = MARKET + "/market/integrations/telegram/security/"
    staff_bot = bot_link("staff") or staff_url
    customer_bot = bot_link("customer") or market_url

    if command in {"stop", "unsubscribe"}:
        if identity:
            identity.notifications_enabled = False
            identity.marketing_opt_in = False
            identity.closing_opt_in = False
            identity.critical_opt_in = False
            identity.stock_opt_in = False
            identity.save(update_fields=[
                "notifications_enabled", "marketing_opt_in", "closing_opt_in",
                "critical_opt_in", "stock_opt_in",
            ])
        return "KOFAD notifications paused for this bot. You can manage consent at the signed-in KOFAD website."

    if command in {"status", "me"}:
        if identity and _account(identity):
            return ("Verified KOFAD " + identity.kind + " account connected to " + bot_kind
                    + " bot.\nNotifications: "
                    + ("enabled" if identity.notifications_enabled else "disabled"))
        return "This Telegram bot is not linked to a KOFAD account. Use /link."

    if bot_kind == "official":
        # This bot's separate link can be enabled for safe password recovery,
        # but it does NOT expose staff finance, customer orders, or messaging.
        if command in {"start", "help", "menu"}:
            return (
                "KOFAD IMPEX — Official Assistant & Security\n\n"
                "/otp — secure verification and account recovery\n"
                "/link — connect your account to official security bot\n"
                "/shop — visit the KOFAD customer bot\n"
                "/staff — visit the private KOFAD staff bot\n"
                "/support — official customer service\n"
                "/status — check this bot's verified identity\n"
                "/stop — pause optional notifications"
            )
        if command in {"link", "connect"}:
            return (
                "To enable recovery on this OFFICIAL bot, first sign in and verify "
                "your existing account at the appropriate website:\n"
                f"Customer: {official_market_url}\nStaff: {official_staff_url}\n"
                "Then opt in to security recovery. A Telegram username or phone number alone is not sufficient."
            )
        if command in {"otp", "password", "recover", "recovery", "verify", "security"}:
            return (
                "KOFAD secure recovery: use your verified and previously connected "
                "OFFICIAL bot account; never send an OTP or password in a chat.\n"
                f"Customer recovery: {MARKET}/market/integrations/telegram/recover/\n"
                f"Staff recovery: {STAFF}/integrations/telegram/recover/\n"
                f"Alternative customer SMS/email: {MARKET}/market/account/password-reset/\n"
                "Existing staff recovery: " + STAFF + "/forgot-password/"
            )
        if command in {"shop", "orders", "track", "catalog", "products"}:
            return "Shopping and private order updates are handled by the KOFAD Customer Bot:\n" + customer_bot
        if command in {"staff", "analysis", "reports", "finance", "closing", "sales", "alerts"}:
            return "Authorized staff intelligence and notifications are handled by the KOFAD Staff Bot:\n" + staff_bot
        if command in {"support", "contact", "human"}:
            return "Official customer support: " + COMPANY + "/contact/"
        if command in {"privacy"}:
            return COMPANY + "/privacy/"
        if command == "ping":
            return "KOFAD Official Bot is responding."
        return "I'm KOFAD's public assistant and security directory. Send /help for the available services."

    if bot_kind == "customer":
        if command in {"start", "help", "menu"}:
            return (
                "KOFAD — Customer Shopping Assistant\n\n"
                "/search PRODUCT — find published KOFAD products\n"
                "/shop — browse catalog\n"
                "/orders — check your own orders (secure link required)\n"
                "/track REFERENCE — track an order in your account\n"
                "/link — connect your KOFAD customer account\n"
                "/alerts — messaging preferences\n"
                "/support — contact the store\n"
                "/stop — pause notifications\n"
                "Account security and recovery are handled by the Official Bot."
            )
        if command in {"link", "connect"}:
            return "Connect your signed-in customer account at:\n" + market_url
        if command in {"shop", "products", "catalog"}:
            return "KOFAD Market catalog: " + MARKET + "/market/"
        if command in {"search", "find"}:
            return _product_search(tail)
        if command in {"orders", "order", "track"} or "where is my order" in plain:
            if identity and identity.kind == "customer":
                customer = _account(identity)
                if customer:
                    return _customer_orders(
                        customer,
                        tail if command in {"order", "track"} else "",
                    )
            return "Connect your verified customer account with /link to see your private order status."
        if command in {"alerts", "notifications"}:
            return "Update your KOFAD customer alert preferences: " + market_url
        if command in {"support", "contact", "human"}:
            return "Customer service: " + MARKET + "/market/messages/"
        if command in {"otp", "password", "recover", "recovery", "security"}:
            return "For account recovery, use the official KOFAD bot:\n" + bot_link("official")
        if command in {"analysis", "reports", "finance", "closing", "staff", "sales"}:
            return "Management information is available only through the separate authorized KOFAD Staff Bot."
        if command == "ping":
            return "KOFAD Customer Bot is responding."
        return "KOFAD Customer Assistant: try /search, /orders or /help."

    # Staff operations bot: no OTP delivery/recovery or customer order records.
    if command in {"start", "help", "menu"}:
        return (
            "KOFAD — Staff & Management Assistant\n\n"
            "/link — securely connect existing KOFAD staff account\n"
            "/analysis — verified closing performance, within your own access scope\n"
            "/reports — authorized management summary\n"
            "/alerts — notice subscriptions\n"
            "/status — verify current staff link\n"
            "/stop — pause notifications\n"
            "Passwords and OTPs are handled only by the Official Bot."
        )
    if command in {"link", "connect"}:
        return "Connect your signed-in KOFAD staff account at:\n" + staff_url
    if command in {"analysis", "reports", "closing", "sales", "finance", "weekly", "monthly"} or (
        any(k in plain for k in ("sales today", "business performance", "weekly sales", "closing report"))
    ):
        if not settings.TELEGRAM_BUSINESS_ANALYTICS_ENABLED:
            return "KOFAD Telegram business analysis is not enabled yet."
        if identity and identity.kind == "staff":
            staff = _account(identity)
            if staff:
                return _staff_analysis(staff, 30 if ("month" in plain or command == "monthly") else 7)
        return "Business analysis requires a connected staff account with current report permissions."
    if command in {"alerts", "notifications"}:
        return "Manage business and closing alerts through your KOFAD staff account: " + staff_url
    if command in {"otp", "password", "recover", "recovery", "security"}:
        return "Recover credentials only through the KOFAD Official Bot:\n" + bot_link("official")
    if command in {"orders", "track", "order"}:
        return "Customer order tracking is handled by the separate KOFAD Customer Bot."
    if command in {"support", "contact"}:
        return "Staff help and account support: " + STAFF
    if command == "ping":
        return "KOFAD Staff Bot is responding."
    return "KOFAD Staff Assistant: send /help for allowed commands."
