"""Authoritative routing for KOFAD's three independent Telegram bot accounts.

official = KOFAD IMPEX public assistant + separately opted-in account recovery
customer = linked customer commerce and transactional notices
staff = permission-scoped staff intelligence, notifications and reports

Never confuse a bot username with an authorization credential.
"""
import re

from django.conf import settings


BOT_KINDS = ("official", "customer", "staff")
WEBHOOK_PATHS = {
    "official": "/telegram/webhook/",
    "customer": "/telegram/customer/webhook/",
    "staff": "/telegram/staff/webhook/",
}


def bot_token(kind):
    if kind == "official":
        return settings.TELEGRAM_BOT_TOKEN
    if kind == "customer":
        return getattr(settings, "TELEGRAM_CUSTOMER_BOT_TOKEN", "")
    if kind == "staff":
        return getattr(settings, "TELEGRAM_STAFF_BOT_TOKEN", "")
    return ""


def bot_secret(kind):
    if kind == "official":
        return settings.TELEGRAM_WEBHOOK_SECRET
    if kind == "customer":
        return getattr(settings, "TELEGRAM_CUSTOMER_WEBHOOK_SECRET", "")
    if kind == "staff":
        return getattr(settings, "TELEGRAM_STAFF_WEBHOOK_SECRET", "")
    return ""


def bot_username(kind):
    if kind == "official":
        return "KOFADIMPEXBOT"
    if kind == "customer":
        name = getattr(settings, "TELEGRAM_CUSTOMER_BOT_USERNAME", "")
    elif kind == "staff":
        name = getattr(settings, "TELEGRAM_STAFF_BOT_USERNAME", "")
    else:
        return ""
    name = str(name).strip().lstrip("@")
    return name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name) else ""


def bot_link(kind):
    username = bot_username(kind)
    return "https://t.me/" + username if username else ""


def bot_enabled(kind):
    if kind not in BOT_KINDS or not settings.TELEGRAM_BOT_ENABLED:
        return False
    if kind == "customer" and not getattr(settings, "TELEGRAM_CUSTOMER_BOT_ENABLED", False):
        return False
    if kind == "staff" and not getattr(settings, "TELEGRAM_STAFF_BOT_ENABLED", False):
        return False
    return bool(bot_token(kind) and bot_secret(kind))


def bot_for_account(kind, purpose="service"):
    """Password recovery is intentionally isolated to the official bot."""
    if purpose == "security":
        return "official"
    return kind if kind in {"customer", "staff"} else None
