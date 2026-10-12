"""Fast KOFAD-to-Telegram identity linking via signed Telegram Mini App data.

No KOFAD password ever enters a Telegram message. The website must separately
hold an authenticated KOFAD session (and staff MFA, if configured). The Mini
App must present Telegram-signed, recent, bot-specific user information.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Access, TelegramIdentity
from .telegram_channels import bot_token
from .telegram_integration import credential_stamp, linked_identity


class TelegramMiniAppError(ValueError):
    """Public-safe error for invalid Mini App authentication."""


def verify_miniapp_data(bot_kind, raw, *, now=None):
    """Verify Telegram's first-party Mini App HMAC (not initDataUnsafe)."""
    if not isinstance(raw, str) or not 1 <= len(raw) <= 4096:
        raise TelegramMiniAppError("Open this page using the Sign in button inside the KOFAD Telegram bot.")
    token = bot_token(bot_kind)
    if not token:
        raise TelegramMiniAppError("This KOFAD Telegram bot is not configured.")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=24)
        supplied = dict(pairs)
        if len(pairs) != len(supplied) or not supplied.get("hash"):
            raise ValueError("Duplicated or missing Telegram field.")
        auth_date = int(supplied.get("auth_date", "0"))
        timestamp = int(now if now is not None else time.time())
        if auth_date > timestamp + 30 or timestamp - auth_date > 300:
            raise ValueError("Old or future Telegram authentication.")
        checksum = supplied.pop("hash")
        if len(checksum) != 64:
            raise ValueError("Invalid Telegram checksum.")
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(supplied.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(checksum, expected):
            raise ValueError("Mini App signature did not match.")
        user = json.loads(supplied.get("user", "{}"))
        if (not isinstance(user, dict) or type(user.get("id")) is not int
                or user["id"] <= 0 or user.get("is_bot")):
            raise ValueError("No Telegram user in signed data.")
        return user
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise TelegramMiniAppError(
            "Telegram could not verify this sign-in. Reopen the KOFAD bot and tap Sign in again."
        ) from exc


def link_miniapp_identity(kind, owner_id, bot_kind, telegram_user_id, *, alerts=False,
                          marketing=False, recovery=False):
    """Pair authenticated KOFAD account and Telegram-signed identity atomically."""
    if kind not in {"staff", "customer"} or bot_kind not in {kind, "official"}:
        raise TelegramMiniAppError("The selected KOFAD bot cannot access this account.")
    stamp = credential_stamp(kind, owner_id)
    if not stamp:
        raise TelegramMiniAppError("Sign in to an active KOFAD account first.")
    if type(telegram_user_id) is not int or telegram_user_id <= 0:
        raise TelegramMiniAppError("Invalid Telegram user.")
    with transaction.atomic():
        # Never silently steal a Telegram connection belonging to another account.
        conflict = TelegramIdentity.objects.filter(
            bot_kind=bot_kind, kind=kind, telegram_user_id=telegram_user_id,
        ).exclude(owner_id=owner_id).exists()
        if conflict:
            raise TelegramMiniAppError(
                "This Telegram account is linked to another KOFAD account of this type."
            )
        existing = TelegramIdentity.objects.select_for_update().filter(
            bot_kind=bot_kind, kind=kind, owner_id=owner_id,
        ).first()
        if existing and existing.telegram_user_id != telegram_user_id:
            raise TelegramMiniAppError(
                "This KOFAD account already uses a different Telegram identity. "
                "Disconnect it from KOFAD account security before replacing it."
            )
        if existing is None:
            try:
                existing = TelegramIdentity.objects.create(
                    kind=kind, bot_kind=bot_kind, owner_id=owner_id,
                    telegram_user_id=telegram_user_id, chat_id=telegram_user_id,
                    auth_stamp=stamp, verified_at=timezone.now(),
                )
            except IntegrityError as exc:
                raise TelegramMiniAppError("This Telegram connection needs manual review.") from exc
        else:
            existing.auth_stamp = stamp
            existing.verified_at = timezone.now()
            existing.revoked_at = None
        existing.notifications_enabled = bool(alerts and bot_kind != "official")
        existing.marketing_opt_in = bool(marketing and kind == "customer" and bot_kind == "customer"
                                         and existing.notifications_enabled)
        if kind == "staff":
            from django.contrib.auth.models import User
            user = User.objects.filter(pk=owner_id, is_active=True).first()
            access = Access.objects.filter(user_id=owner_id).first()
            if not user or not access or access.force_password_change or access.must_change_password:
                raise TelegramMiniAppError("Complete your KOFAD staff security requirements first.")
            reports = user.is_superuser or any(user.has_perm("core." + p) for p in (
                "manage_company", "view_reports", "operate_finance"
            ))
            inventory = user.is_superuser or any(user.has_perm("core." + p) for p in (
                "manage_company", "operate_inventory"
            ))
            existing.closing_opt_in = bool(existing.notifications_enabled and reports)
            existing.critical_opt_in = bool(existing.notifications_enabled and reports)
            existing.stock_opt_in = bool(existing.notifications_enabled and inventory)
        else:
            existing.closing_opt_in = False
            existing.critical_opt_in = False
            existing.stock_opt_in = False
        # Recovery is a separate explicit choice; only the official bot sends
        # recovery codes to this cryptographically verified, linked chat.
        existing.recovery_opt_in = bool(recovery and bot_kind == "official")
        existing.save()
    return linked_identity(kind, owner_id, bot_kind=bot_kind)
