from django.core.management.base import BaseCommand

from core.telegram_delivery import deliver_pending


class Command(BaseCommand):
    help = "Deliver queued opted-in KOFAD Telegram notifications once"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        count = deliver_pending(options["limit"])
        self.stdout.write(f"Telegram notices confirmed sent: {count}")
