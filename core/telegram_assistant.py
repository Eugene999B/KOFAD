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
    orders = OnlineOrder.objects.filter(customer=customer).order_by("-created_at")
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
        + MARKET + f"/market/products/{p.product_id}/"
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

    # A typed command is an explicit instruction from a PREVIOUSLY VERIFIED
    # KOFAD-to-Telegram account link, not evidence based on a phone number.
    if command in {"alerts_on", "notify_on", "subscribe", "promos_on", "promos_off"}:
        if bot_kind == "official":
            return "Security recovery consent must be confirmed with your KOFAD password on the official website."
        if not identity or not _account(identity) or identity.kind != bot_kind:
            return "First connect your KOFAD account securely using /link."
        if bot_kind == "customer":
            identity.notifications_enabled = command != "promos_off" or identity.notifications_enabled
            if command == "promos_on":
                identity.marketing_opt_in = True
            elif command == "promos_off":
                identity.marketing_opt_in = False
            identity.save(update_fields=["notifications_enabled", "marketing_opt_in"])
            return (
                "Customer order alerts: " + ("ON" if identity.notifications_enabled else "OFF")
                + "\nOptional promotions: " + ("ON" if identity.marketing_opt_in else "OFF")
                + "\nUse /promos_off, /alerts_off or website preferences to change consent."
            )
        staff = _account(identity)
        from .models import Access
        access = Access.objects.filter(user=staff).first()
        if not access or access.force_password_change or access.must_change_password:
            return "Complete your required KOFAD staff security steps before enabling alerts."
        management = staff.is_superuser or any(staff.has_perm("core." + perm) for perm in (
            "manage_company", "operate_finance", "view_reports"))
        inventory = staff.is_superuser or any(staff.has_perm("core." + perm) for perm in (
            "manage_company", "operate_inventory"))
        identity.notifications_enabled = True
        identity.closing_opt_in = bool(management)
        identity.critical_opt_in = bool(management)
        identity.stock_opt_in = bool(inventory)
        identity.save(update_fields=[
            "notifications_enabled", "closing_opt_in", "critical_opt_in", "stock_opt_in",
        ])
        return (
            "Staff notices enabled for your CURRENT permissions and branches. "
            "Daily closing, critical variances, and inventory alerts are sent only "
            "when your authorized responsibilities apply. Use /alerts_off to stop."
        )
    if command in {"alerts_off", "notify_off"}:
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
        return "Optional Telegram notifications paused for this bot. Recovery on the Official Bot is separate."
    if command in {"mybranches", "branches", "locations"} and bot_kind == "staff":
        if not identity or identity.kind != "staff" or not _account(identity):
            return "Verify your KOFAD staff account using /link."
        from .telegram_intelligence import _branches
        branches = _branches(_account(identity))
        if not branches.exists():
            return "You do not have authorized management branches in Telegram."
        return "Authorized reporting branches:\n" + "\n".join(
            "• " + b.name for b in branches.order_by("name")[:30]
        )
    if command in {"myalerts", "subscriptions"}:
        if not identity:
            return "No verified KOFAD account connected. Use /link."
        if bot_kind == "official":
            return (
                "Official bot security recovery consent: "
                + ("ON" if identity.recovery_opt_in else "OFF")
                + "\nRecovery settings require confirmation on the KOFAD website."
            )
        return (
            "Telegram alerts: " + ("ON" if identity.notifications_enabled else "OFF")
            + ("\nMarketing: " + ("ON" if identity.marketing_opt_in else "OFF")
               if bot_kind == "customer" else
               "\nClosing: " + ("ON" if identity.closing_opt_in else "OFF")
               + "\nCritical: " + ("ON" if identity.critical_opt_in else "OFF")
               + "\nInventory: " + ("ON" if identity.stock_opt_in else "OFF"))
            + "\nUse /alerts_on or /alerts_off, and your KOFAD website preferences."
        )

    if command in {"login", "signin", "authenticate"}:
        return ("Tap the secure Sign in button below. Sign in using your normal "
                "KOFAD website username, phone or verified email, with Google "
                "or staff MFA as configured. NEVER send passwords in bot chats.")

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
                "/stop — pause optional notifications\n"
                "/myalerts — security recovery consent status"
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
        if command in {"start", "help", "menu"} and not identity:
            return ("KOFAD Customer Assistant\n"
                    "Tap the Sign in button to access your KOFAD account.\n"
                    "/shop — public catalog\n/search PRODUCT — published items\n"
                    "/login — secure sign-in\n/support — customer support\n"
                    "Never send passwords or payment PINs in chat.")
        if command in {"start", "help", "menu"}:
            return (
                "KOFAD — Customer Shopping Assistant\n\n"
                "/search PRODUCT — find published KOFAD products\n"
                "/shop — browse catalog\n"
                "/orders — check your own orders (secure link required)\n"
                "/track REFERENCE — track an order in your account\n"
                "/link — connect your KOFAD customer account\n"
                "/alerts_on — enable private order updates\n"
                "/promos_on — opt in to occasional promotions\n"
                "/promos_off — opt out of promotions\n"
                "/myalerts — see your subscriptions\n"
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
            return "Manage subscriptions with /alerts_on, /alerts_off, /promos_on, /promos_off or at: " + market_url
        if command in {"support", "contact", "human"}:
            return "Customer service: " + MARKET + "/market/messages/"
        if command in {"returns", "refunds"}:
            return "Review your returns securely in your customer account: " + MARKET + "/market/account/returns/"
        if command in {"checkout", "cart", "pay"}:
            return "Review your cart and complete payment safely on KOFAD Market: " + MARKET + "/market/cart/"
        if command in {"otp", "password", "recover", "recovery", "security"}:
            return "For account recovery, use the official KOFAD bot:\n" + bot_link("official")
        if command in {"analysis", "reports", "finance", "closing", "staff", "sales"}:
            return "Management information is available only through the separate authorized KOFAD Staff Bot."
        if command == "ping":
            return "KOFAD Customer Bot is responding."
        return "KOFAD Customer Assistant: try /search, /orders or /help."

    # Staff operations bot: no OTP delivery/recovery or customer order records.
    if command in {"start", "help", "menu"}:
        if identity and identity.kind == "staff" and _account(identity):
            from .telegram_intelligence import staff_menu
            return staff_menu(_account(identity))
        return ("KOFAD Staff Bot — sign in to unlock the tools your real "
                "KOFAD role permits. Tap Sign in below or type /login. "
                "Your password and any MFA code are entered only on KOFAD's website.")
    if command in {"tasks", "priorities", "mytasks", "inventory", "orders_due"}:
        from .telegram_intelligence import account_tasks, _role
        if not identity or identity.kind != "staff" or not _account(identity):
            return "Use /login to verify your KOFAD staff account before viewing tasks."
        user = _account(identity)
        roles = _role(user)
        if command == "inventory" and not roles["inventory"]:
            return "Your KOFAD role does not include inventory access."
        if command == "orders_due" and not roles["sales"]:
            return "Your KOFAD role does not include sales operations."
        return account_tasks(user)
    if command in {"start", "help", "menu"}:
        return (
            "KOFAD — Staff & Management Assistant\n\n"
            "/link — securely connect existing KOFAD staff account\n"
            "/analysis — verified closing performance, within your own access scope\n"
            "/today, /week, /month — select reporting period\n"
            "/mybranches — see authorized reporting branches\n"
            "/alerts_on — subscribe to notices for your permissions\n"
            "/myalerts — review subscriptions\n"
            "/reports — authorized management summary\n"
            "/alerts — notice subscriptions\n"
            "/status — verify current staff link\n"
            "/stop — pause notifications\n"
            "Passwords and OTPs are handled only by the Official Bot."
        )
    if command in {"link", "connect"}:
        return "Connect your signed-in KOFAD staff account at:\n" + staff_url
    if command in {"analysis", "reports", "closing", "sales", "finance", "weekly", "monthly", "today", "week", "month"} or (
        any(k in plain for k in ("sales today", "business performance", "weekly sales", "closing report"))
    ):
        if not settings.TELEGRAM_BUSINESS_ANALYTICS_ENABLED:
            return "KOFAD Telegram business analysis is not enabled yet."
        if identity and identity.kind == "staff":
            staff = _account(identity)
            if staff:
                return _staff_analysis(staff, 30 if ("month" in plain or command in {"month", "monthly"}) else 1 if command == "today" else 7)
        return "Business analysis requires a connected staff account with current report permissions."
    if command in {"alerts", "notifications"}:
        return "Use /alerts_on, /alerts_off, /myalerts, or website preferences: " + staff_url
    if command in {"otp", "password", "recover", "recovery", "security"}:
        return "Recover credentials only through the KOFAD Official Bot:\n" + bot_link("official")
    if command in {"orders", "track", "order"}:
        return "Customer order tracking is handled by the separate KOFAD Customer Bot."
    if command in {"support", "contact"}:
        return "Staff help and account support: " + STAFF
    if command == "ping":
        return "KOFAD Staff Bot is responding."
    return "KOFAD Staff Assistant: send /help for allowed commands."
