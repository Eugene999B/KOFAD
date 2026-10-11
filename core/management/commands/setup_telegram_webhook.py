"""Manage one of KOFAD's THREE Telegram webhooks without revealing credentials."""
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.telegram_channels import (
    BOT_KINDS, WEBHOOK_PATHS, bot_enabled, bot_secret, bot_token,
)


class Command(BaseCommand):
    help = "Safely register, inspect, or remove a single KOFAD Telegram bot webhook."

    def add_arguments(self, parser):
        parser.add_argument("--bot", choices=BOT_KINDS, default="official",
                            help="official (also security/OTP), customer, or staff")
        parser.add_argument("--status", action="store_true", help="Check this bot's webhook status")
        parser.add_argument("--remove", action="store_true", help="Remove only this bot's webhook")

    def handle(self, *args, **options):
        kind = options["bot"]
        token = bot_token(kind)
        if not token:
            raise CommandError("Selected Telegram bot API token is not configured in this service.")
        if options["status"] and options["remove"]:
            raise CommandError("Choose only --status OR --remove.")
        method = (
            "getWebhookInfo" if options["status"]
            else "deleteWebhook" if options["remove"]
            else "setWebhook"
        )
        payload = {}
        if method == "setWebhook":
            if not bot_enabled(kind):
                raise CommandError("This Telegram bot is disabled or its secret is missing.")
            base = settings.TELEGRAM_WEBHOOK_BASE_URL.rstrip("/")
            parsed = urlparse(base)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in {"", "/"}):
                raise CommandError("The Telegram webhook base must be an HTTPS origin.")
            payload = {
                "url": base + WEBHOOK_PATHS[kind],
                "secret_token": bot_secret(kind),
                "allowed_updates": ["message"],
                "drop_pending_updates": False,
            }
        try:
            response = requests.post(
                "https://api.telegram.org/bot" + token + "/" + method,
                json=payload,
                timeout=10,
            )
            data = response.json()
        except (requests.RequestException, ValueError):
            raise CommandError("Telegram API is unavailable or returned invalid data.") from None
        if response.status_code != 200 or not data.get("ok"):
            raise CommandError("Telegram rejected this webhook operation. Check private service configuration.")
        if options["status"]:
            info = data.get("result") or {}
            self.stdout.write(kind + " bot webhook: " + ("configured" if info.get("url") else "not configured"))
            self.stdout.write("Pending updates: " + str(int(info.get("pending_update_count", 0))))
            if info.get("last_error_date"):
                self.stdout.write("Telegram has reported a delivery error; check delivery status.")
        elif options["remove"]:
            self.stdout.write(self.style.SUCCESS(kind + " bot webhook removed."))
        else:
            self.stdout.write(self.style.SUCCESS(kind + " bot webhook registered."))
