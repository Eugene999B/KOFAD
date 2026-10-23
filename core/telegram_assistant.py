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


def _staff_analysis(user, days=7, offset=0):
    from .models import Closing
    branches = _authorized_branches(user)
    if not branches.exists():
        return "Your KOFAD account is not authorized to view management reports."
    today = timezone.localdate() - timedelta(days=offset)
    closings = Closing.objects.filter(
        branch__in=branches, verified_by__isnull=False,
        date__gte=today - timedelta(days=days - 1), date__lte=today,
    ).select_related("branch").order_by("-date")[:2001]
    totals = {"sales": Decimal("0"), "expenses": Decimal("0"), "collections": Decimal("0")}
    count, most_recent, capped = 0, None, False
    for item in closings:
        if count >= 2000:
            capped = True
            break
        n = item.summary or {}
        totals["sales"] += _money(n.get("sales_total"))
        totals["expenses"] += _money(n.get("expenses_total"))
        totals["collections"] += _money(n.get("debt_collections"))
        count += 1
        if most_recent is None:
            most_recent = item
    return (
        f"KOFAD VERIFIED CLOSING INTELLIGENCE — {days} day(s) ending {today:%d %b %Y}\n"
        f"Authorized business locations: {branches.count()}\n"
        f"Verified closings reviewed: {count}\n"
        f"Recorded sales: GHS {totals['sales']:,.2f}\n"
        f"Expenses: GHS {totals['expenses']:,.2f}\n"
        f"Debt collections: GHS {totals['collections']:,.2f}\n"
        + (f"Latest verified closing: {most_recent.branch.name}, {most_recent.date:%d %b %Y}\n" if most_recent else "")
        + ("\nWARNING: Report reached its 2,000-closing safety cap; totals may be incomplete." if capped else "")
        + "\nThese figures are closing-record totals, not a profit or cash balance calculation. "
        "Incomplete/unverified closings are excluded. Reopen the staff dashboard for details."
    )


def _staff_compare(user, days=7):
    """Compare two equally sized periods of verified, role-scoped closing data."""
    from .models import Closing

    branches = _authorized_branches(user)
    if not branches.exists():
        return "Your KOFAD account is not authorized to view management reports."
    today = timezone.localdate()
    current_start = today - timedelta(days=days - 1)
    previous_start = current_start - timedelta(days=days)
    # Only verified data is authoritative; bound report size and disclose caps.
    rows = Closing.objects.filter(
        branch__in=branches, verified_by__isnull=False,
        date__gte=previous_start, date__lte=today,
    ).order_by("-date").values_list("date", "summary")[:3001]
    totals = [Decimal("0"), Decimal("0")]
    count = [0, 0]
    scanned = 0
    capped = False
    for date, summary in rows:
        scanned += 1
        if scanned > 3000:
            capped = True
            break
        period = 1 if date < current_start else 0
        totals[period] += _money((summary or {}).get("sales_total"))
        count[period] += 1
    prior, current = totals[1], totals[0]
    if not any(count):
        return ("No verified closing records were found for these periods "
                "within your authorized KOFAD branches.")
    if prior:
        percent = (current - prior) / abs(prior) * 100
        trend = f"{percent:+.1f}% compared with the previous period"
    else:
        trend = "percentage change unavailable because the prior total is zero"
    end_prev = current_start - timedelta(days=1)
    return (
        f"KOFAD VERIFIED SALES COMPARISON — {days}-day periods\n"
        f"Current: {current_start:%d %b}–{today:%d %b %Y} | "
        f"{count[0]} verified closings | GHS {current:,.2f}\n"
        f"Previous: {previous_start:%d %b}–{end_prev:%d %b %Y} | "
        f"{count[1]} verified closings | GHS {prior:,.2f}\n"
        f"Change: {trend}.\n"
        + ("WARNING: The 3,000-record safety cap was reached, so totals are incomplete.\n"
           if capped else "")
        + "Figures reflect recorded verified closing sales only, not "
        "profit, actual bank deposits or a complete accounting statement. "
        "Missing or unverified closings affect comparability."
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



def _conversational_command(text, bot_kind):
    """Map ordinary questions onto verified, deterministic KOFAD operations.

    Never infer authorization from wording: all mapped operations still use
    the existing Telegram identity and live Django permission checks.
    """
    import re

    q = " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())
    if re.search(r"\b(?:which|what) (?:ai )?(?:model|api|provider|engine)\b|"
                 r"\b(?:powered by|which llm)\b", q):
        return "aboutai"
    if re.search(r"\b(?:who are you|your name|introduce yourself)\b", q):
        return "introduce"
    if re.fullmatch(r"(?:hi|hello|hey|good morning|good evening|good afternoon)(?: there| team| everyone| bot)?", q):
        return "introduce"
    if any(x in q for x in ("what can you do", "how can you help", "show me your features",
                            "what are your capabilities", "help me use this bot")):
        return "menu"
    if any(x in q for x in ("sign in", "log in", "connect my account", "link my account")):
        return "login"
    if bot_kind == "staff":
        if any(x in q for x in ("why are sales", "what caused", "risk brief", "deep analysis",
                                "decision brief", "operational insight", "what should we do")):
            return "insights"
        if (any(x in q for x in ("compare", "versus", " vs ", "growth", "trend"))
                and any(x in q for x in ("sales", "revenue", "performance", "closing"))):
            if any(x in q for x in ("month", "30 day")):
                return "compare_month"
            return "compare_week"
        if any(x in q for x in ("my role", "my permissions", "my access rights",
                                "what am i allowed", "what is my job")):
            return "myrole"
        if any(x in q for x in ("my tasks", "to do", "to-do", "my priorities",
                                "what needs attention", "work pending")):
            return "tasks"
        if any(x in q for x in ("my branches", "my locations", "assigned branches",
                                "assigned locations", "where can i work")):
            return "branches"
        if any(x in q for x in ("low stock", "out of stock", "inventory level",
                                "stock alerts", "inventory problems")):
            return "inventory"
        if any(x in q for x in ("pending orders", "orders due", "outstanding orders",
                                "orders need attention")):
            return "orders_due"
        if any(x in q for x in ("my notifications", "my alerts", "notification settings")):
            return "myalerts"
        if any(x in q for x in ("sales", "closing", "performance", "report", "revenue")):
            if any(x in q for x in ("last month", "this month", "past month", "30 days")):
                return "month"
            if any(x in q for x in ("yesterday", "previous day")):
                return "yesterday"
            if any(x in q for x in ("today", "this morning", "current day")):
                return "today"
            return "week"
    if bot_kind == "customer":
        if any(x in q for x in ("track my", "where is my order", "where are my items",
                                "order status", "my purchases", "my orders")):
            return "orders"
        if any(x in q for x in ("pay for", "payment", "mobile money", "checkout")):
            return "pay"
        if any(x in q for x in ("return an item", "my refund", "refund policy")):
            return "returns"
        if any(x in q for x in ("find a product", "what products", "shop online")):
            return "shop"
    return ""


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
    if not stripped.startswith("/"):
        command = _conversational_command(stripped, bot_kind) or command
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

    if command == "aboutai":
        return ("I'm KOFAD's digital assistant. I can't disclose internal "
                "model or API configuration. I can explain public services "
                "and help with verified, permission-scoped KOFAD operations.")
    if command == "introduce":
        if bot_kind == "staff" and identity and identity.kind == "staff":
            staff = _account(identity)
            if staff:
                from .telegram_intelligence import staff_menu
                return "Hello! I'm your KOFAD Staff Assistant.\n\n" + staff_menu(staff)
        if bot_kind == "customer":
            return ("Hello! I'm the KOFAD Customer Bot. I can help you browse "
                    "products, track orders after secure linking, and learn "
                    "about delivery and safe checkout. Send /menu for options.")
        return ("Hello! I'm the official KOFAD IMPEX assistant. I can guide "
                "you to shopping, staff tools or secure account recovery. "
                "Send /menu to explore.")
    if bot_kind == "staff" and command == "myrole":
        if not identity or identity.kind != "staff" or not _account(identity):
            return "First use /login to verify your KOFAD staff account."
        from .telegram_intelligence import staff_menu
        return staff_menu(_account(identity))

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
        from .telegram_intelligence import staff_welcome
        from .telegram_integration import queue_notice
        queue_notice(identity, "welcome", f"tg:welcome:{identity.pk}",
                     staff_welcome(staff))
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

    if command in {"ask", "ai", "question"}:
        # Deliberately opt-in to external AI with /ask. Never send private KOFAD
        # chat/session/customer/staff/account info to an external free provider.
        import re
        import hashlib
        from marketplace.kofad_ai import answer, eligible_for_external_ai, free_configured, reserve_free_request
        public_question = tail.strip()
        if not public_question:
            return (
                "Ask me about KOFAD public products and services using "
                "/ask followed by a question. The question may be processed "
                "by an optional external AI provider. Never send passwords, OTPs or private records."
            )
        if bot_kind == "staff" and re.search(
            r"(?i)\b(?:closing|salaries|payroll|profit|financial|"
            r"branch sales|daily revenue|staff personal|ledger|cash variance)\b",
            public_question,
        ):
            return (
                "Private business figures are never sent to external AI. "
                "Use /today, /week, /month or /tasks for reports permitted "
                "by your KOFAD role."
            )
        # /ask is explicit consent for a PUBLIC question only. Each bot user
        # has a small allowance shared with the web's free daily budget.
        # Hash Telegram IDs so cache keys are not directly identifying.
        ai_permitted = False
        if free_configured() and eligible_for_external_ai(public_question):
            user_hash = hashlib.sha256(
                f"{settings.SECRET_KEY}:{bot_kind}:{user_id}".encode()
            ).hexdigest()[:20]
            ai_permitted = reserve_free_request(
                "telegram", user_hash, timezone.now().strftime("%Y%m%d"),
            )
        result, source = answer(
            public_question[:601], customer=None, ai_allowed=ai_permitted,
        )
        note = ("Free AI unavailable or at its daily limit; this is local guidance."
                if source == "local" and not ai_permitted else
                "Public information only; confirm important details with KOFAD.")
        return result[:1600] + "\n\n" + note

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
        from marketplace.kofad_ai import answer
        result, _ = answer(stripped[:600], ai_allowed=False)
        return result

    # Staff operations bot: no OTP delivery/recovery or customer order records.
    if command in {"start", "help", "menu"}:
        if identity and identity.kind == "staff" and _account(identity):
            from .telegram_intelligence import staff_menu
            return staff_menu(_account(identity))
        return ("KOFAD Staff Bot — sign in to unlock the tools your real "
                "KOFAD role permits. Tap Sign in below or type /login. "
                "Your password and any MFA code are entered only on KOFAD's website.")
    if command in {"tasks", "priorities", "mytasks", "inventory", "orders_due", "insights", "decisions"}:
        from .telegram_intelligence import account_tasks, _role
        if not identity or identity.kind != "staff" or not _account(identity):
            return "Use /login to verify your KOFAD staff account before viewing tasks."
        user = _account(identity)
        roles = _role(user)
        if command == "inventory" and not roles["inventory"]:
            return "Your KOFAD role does not include inventory access."
        if command == "orders_due" and not roles["sales"]:
            return "Your KOFAD role does not include sales operations."
        if command in {"insights", "decisions"}:
            from .staff_decisions import decision_brief
            return decision_brief(user)[:3900]
        if command == "inventory":
            from .telegram_intelligence import inventory_brief
            return inventory_brief(user)
        if command == "orders_due":
            from .telegram_intelligence import sales_work_brief
            return sales_work_brief(user)
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
    if command == "yesterday":
        if not settings.TELEGRAM_BUSINESS_ANALYTICS_ENABLED:
            return "KOFAD Telegram business analysis is not enabled yet."
        if not identity or identity.kind != "staff":
            return "Use /login to verify your KOFAD reporting access."
        staff = _account(identity)
        return _staff_analysis(staff, 1, offset=1) if staff else (
            "Your verified staff account is no longer available."
        )
    if command in {"compare", "compare_week", "compare_month", "trend"}:
        if not settings.TELEGRAM_BUSINESS_ANALYTICS_ENABLED:
            return "KOFAD Telegram business analysis is not enabled yet."
        if not identity or identity.kind != "staff":
            return "Use /login to verify your KOFAD reporting access."
        staff = _account(identity)
        if staff is None:
            return "Your verified staff account is no longer available."
        return _staff_compare(
            staff, days=30 if command == "compare_month" or "month" in plain else 7,
        )
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
    # Natural-language fallback shares the website's permission-scoped, read-only
    # data tools. It never sends staff records to OpenRouter or Gemini.
    if identity and identity.kind == "staff":
        staff = _account(identity)
        if staff:
            from .staff_ai import staff_reply
            result, _ = staff_reply(staff, stripped[:600])
            return result[:2000]
    return ("I can help with your authorized KOFAD work. First link and verify "
            "your account with /login, then ask about priorities, inventory, "
            "sales trends and assigned branches.")
