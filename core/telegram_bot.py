"""Official KOFAD Telegram assistant: public commands only until verified account links ship.

Never treat a Telegram username, chat ID, or natural-language assertion as proof of
a KOFAD staff/customer identity. Sensitive tools require a separately authenticated
account binding, current roles and branch-scoped permission checks.
"""
from django.conf import settings

MARKET = "https://market.kofadimpex.com"
STAFF = "https://staff.kofadimpex.com"
COMPANY = "https://kofadimpex.com"

WELCOME = (
    "Welcome to KOFAD IMPEX, your official business assistant.\n\n"
    "I can help you find KOFAD services, products, account sign-in, order tracking "
    "and official support.\n\n"
    "Use /help to see available commands. Private account information, business "
    "analytics and alerts require secure KOFAD account verification, which is not "
    "yet available in this Telegram integration.\n\n"
    "Never share passwords, PINs, OTP codes or card details in this chat."
)

HELP = (
    "KOFAD IMPEX — assistant menu\n\n"
    "/start — welcome\n"
    "/help — available commands\n"
    "/shop — browse products\n"
    "/orders — sign in to view your orders\n"
    "/track — secure order tracking\n"
    "/account — customer account\n"
    "/staff — staff portal\n"
    "/analysis — business insights (verification required; coming later)\n"
    "/alerts — notifications (opt-in setup coming later)\n"
    "/otp — security and verification guidance\n"
    "/support — contact KOFAD\n"
    "/privacy — privacy information\n"
    "/ping — check service status"
)


def command_reply(raw_text):
    """Bounded, public-only replies. No database, private records or privileged tools."""
    if not isinstance(raw_text, str):
        return HELP
    text = raw_text.strip()[:2000]
    head = text.split(maxsplit=1)[0].lower() if text else ""
    # Telegram sends commands like /start@KOFADIMPEXBOT in some contexts.
    command = head.split("@", 1)[0].lstrip("/")
    if command == "start":
        return WELCOME
    if command in {"help", "menu"}:
        return HELP
    if command in {"shop", "products", "catalog"}:
        return "Browse KOFAD products and current listed prices:\n" + MARKET + "/market/"
    if command in {"orders", "track", "tracking", "receipts"}:
        return (
            "For your privacy, sign in to your KOFAD customer account to see "
            "your own orders, delivery progress and receipts:\n"
            + MARKET + "/market/orders/\n\n"
            "Please don't send your password, verification code or payment PIN here."
        )
    if command in {"account", "login", "verify", "connect"}:
        return (
            "Customer account: " + MARKET + "/market/account/\n"
            "Staff account: " + STAFF + "/\n\n"
            "Telegram account linking is not yet enabled. Signing in on the website "
            "does not automatically connect your Telegram identity."
        )
    if command in {"staff", "employee"}:
        return (
            "Staff sign-in: " + STAFF + "/\n"
            "Staff reports and operations remain protected by your KOFAD roles and branch permissions."
        )
    if command in {"analysis", "reports", "finance", "sales", "inventory"}:
        return (
            "Private business analysis will require a verified KOFAD staff identity "
            "and permission for the requested report and branch. This Telegram "
            "feature is not enabled yet.\nStaff portal: " + STAFF + "/"
        )
    if command in {"alerts", "notifications", "subscribe", "unsubscribe", "stop"}:
        return (
            "Automatic Telegram alerts are not active yet. They will require your "
            "opt-in and verified account link; you'll be able to unsubscribe."
        )
    if command in {"otp", "code", "verification"}:
        return (
            "KOFAD will never ask you to paste passwords, login OTPs, PINs or "
            "payment details into this chat. Telegram-based code delivery is not "
            "enabled yet. Use the official sign-in flow."
        )
    if command in {"support", "human", "contact"}:
        return "Contact KOFAD through our official website:\n" + COMPANY + "/contact/"
    if command == "privacy":
        return "KOFAD privacy information:\n" + COMPANY + "/privacy/"
    if command == "ping":
        return "KOFAD Telegram assistant is responding. Use /help for available commands."
    return (
        "I can help with KOFAD services and navigation. Verified business AI chat "
        "is being prepared; it isn't active yet.\n\n" + HELP
    )
