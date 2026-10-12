from django.core.management.base import BaseCommand
from hubtel_direct.service import reconcile_due


class Command(BaseCommand):
    help = "Reconcile already-submitted Hubtel Direct operations; never retransmit."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        count = reconcile_due(limit=max(1, min(options["limit"], 100)))
        self.stdout.write(f"Checked {count} saved Hubtel Direct references.")
