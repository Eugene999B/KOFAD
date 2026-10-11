"""Register/check the official Telegram webhook without printing API credentials."""
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Configure the KOFAD Telegram webhook using private environment credentials"

    def add_arguments(self, parser):
        parser.add_argument("--status", action="store_true", help="Display sanitized webhook status")
        parser.add_argument("--remove", action="store_true", help="Delete the configured webhook")

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise CommandError("TELEGRAM_BOT_TOKEN must be set in the deployment secrets.")
        if options["status"] and options["remove"]:
            raise CommandError("Choose --status or --remove, not both.")
        method = "getWebhookInfo" if options["status"] else "deleteWebhook" if options["remove"] else "setWebhook"
        payload = {}
        if method == "setWebhook":
            if not settings.TELEGRAM_BOT_ENABLED or not settings.TELEGRAM_WEBHOOK_SECRET:
                raise CommandError("Enable TELEGRAM_BOT_ENABLED and set TELEGRAM_WEBHOOK_SECRET first.")
            base = settings.TELEGRAM_WEBHOOK_BASE_URL.rstrip("/")
            parsed = urlparse(base)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
                raise CommandError("TELEGRAM_WEBHOOK_BASE_URL must be an HTTPS origin only.")
            payload = {
                "url": base + "/telegram/webhook/",
                "secret_token": settings.TELEGRAM_WEBHOOK_SECRET,
                "allowed_updates": ["message"],
                "drop_pending_updates": False,
            }
        # The provider API needs the bot token in the URL. Never echo request URLs
        # or raw HTTP exceptions, which can contain that secret.
        try:
            response = requests.post(
                "https://api.telegram.org/bot" + token + "/" + method,
                json=payload,
                timeout=10,
            )
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CommandError("Unable to contact Telegram API securely.") from None
        if response.status_code != 200 or not data.get("ok"):
            raise CommandError("Telegram rejected the webhook request; inspect configuration privately.")
        if options["status"]:
            info = data.get("result") or {}
            self.stdout.write("Webhook configured: " + ("yes" if info.get("url") else "no"))
            self.stdout.write("Pending update count: " + str(int(info.get("pending_update_count", 0))))
            if info.get("last_error_date"):
                self.stdout.write("Telegram reports a previous delivery error. Review privately.")
        elif options["remove"]:
            self.stdout.write(self.style.SUCCESS("Telegram webhook removed."))
        else:
            self.stdout.write(self.style.SUCCESS("Telegram webhook registered."))
