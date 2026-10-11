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


def reply_for(text, chat_id, user_id):
    """Resolve account dynamically: roles/revocation are always checked per message."""
    stripped = text.strip()
    if stripped.startswith("/start link_"):
        token = stripped.split("link_", 1)[1].split(maxsplit=1)[0]
        return accept_token(token, user_id, chat_id)
    if not settings.TELEGRAM_ACCOUNT_LINK_ENABLED:
        return command_reply(text)
    from .models import TelegramIdentity
    identity = TelegramIdentity.objects.filter(
        telegram_user_id=user_id, chat_id=chat_id, revoked_at__isnull=True,
    ).first()
    if identity and identity.auth_stamp != credential_stamp(identity.kind, identity.owner_id):
        identity = None
    head, _, tail = stripped.partition(" ")
    command = head.lower().split("@", 1)[0].lstrip("/")
    plain = stripped.casefold()
    if command in {"start", "help", "menu"}:
        return (
            "KOFAD official assistant\n"
            + ("Account verified: " + identity.kind + "\n" if identity and _account(identity)
               else "Account not connected.\n")
            + HELP
            + "\n\nUse /link to connect your account, or /stop to pause alerts."
        )
    if command in {"link", "connect"}:
        return (
            "For your security, initiate account linking from the KOFAD website after signing in:\n"
            "Staff: " + STAFF + "/integrations/telegram/\n"
            "Customer: " + MARKET + "/market/integrations/telegram/\n"
            "Telegram usernames and phone numbers alone do not prove account ownership."
        )
    if command in {"stop", "unsubscribe"}:
        if identity:
            identity.notifications_enabled = False
            identity.marketing_opt_in = False
            identity.closing_opt_in = False
            identity.critical_opt_in = False
            identity.save(update_fields=["notifications_enabled", "marketing_opt_in",
                                         "closing_opt_in", "critical_opt_in"])
        return "Telegram notifications paused. Manage preferences in your signed-in KOFAD account."
    if command in {"status", "me"}:
        if identity and _account(identity):
            return (
                f"Linked account: {identity.kind}\n"
                f"Telegram alerts: {'on' if identity.notifications_enabled else 'off'}\n"
                "Manage consent and unlink: "
                + (STAFF + "/integrations/telegram/" if identity.kind == "staff"
                   else MARKET + "/market/integrations/telegram/")
            )
        return "Your Telegram account isn't linked yet. Use /link for the official setup."
    if command in {"search", "find"}:
        return _product_search(tail)
    if command in {"track", "orders", "order"} or "where is my order" in plain:
        if identity and identity.kind == "customer":
            customer = _account(identity)
            if customer:
                return _customer_orders(customer, tail if command in {"track", "order"} else "")
        return "Order tracking requires your linked customer account. Use /link first."
    if command in {"analysis", "reports", "closing", "sales", "finance"} or (
        any(k in plain for k in ("sales today", "business performance", "weekly sales", "closing report"))
    ):
        if not settings.TELEGRAM_BUSINESS_ANALYTICS_ENABLED:
            return "Private business analysis is not enabled in Telegram yet."
        if identity and identity.kind == "staff":
            staff = _account(identity)
            if staff:
                return _staff_analysis(staff, 30 if ("month" in plain or command == "monthly") else 7)
        return "Financial analysis requires a verified, authorized KOFAD staff account."
    if command in {"alerts", "notifications"}:
        return "Manage Telegram alert preferences after signing in to your KOFAD account. Use /link first."
    if command in {"recovery", "password", "otp"}:
        return (
            "Password recovery and verification are handled through the official KOFAD "
            "website. Never share passwords or OTPs in Telegram.\n"
            "Customer Telegram recovery (if enabled): " + MARKET + "/market/integrations/telegram/recover/\n"
            "Staff Telegram recovery (if enabled): " + STAFF + "/integrations/telegram/recover/\n"
            "Existing customer SMS/email: " + MARKET + "/market/account/password-reset/"
        )
    if command in {"support", "contact", "human"} or "speak to someone" in plain:
        return "Contact the KOFAD team: " + COMPANY + "/contact/"
    if command in {"shop", "products", "catalog"} or "browse products" in plain:
        return "Browse products: " + MARKET + "/market/"
    return command_reply(text)
