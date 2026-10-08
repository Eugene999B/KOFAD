"""One-shot email delivery runner for a future private Railway cron service.

Example: python manage.py send_email_notices --limit 25
Credentials and sending must be enabled in Railway first.
"""
from django.core.management.base import BaseCommand
from core.email_identity import deliver_pending, delivery_ready
from django.conf import settings


class Command(BaseCommand):
    help = "Deliver queued, opted-in KOFAD email notices (one run, no daemon)"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        if not delivery_ready() or not settings.KOFAD_EMAIL_NOTIFICATIONS_ENABLED:
            self.stdout.write("Email delivery disabled until provider setup is complete.")
            return
        count = deliver_pending(limit=max(1, min(options["limit"], 100)))
        self.stdout.write(f"Email notices delivered: {count}")
