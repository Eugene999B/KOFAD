import time

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from core.automations import run_scheduled_automations
from core.sms.service import process_one, recover_stale, sync_delivery_reports


class Command(BaseCommand):
    help = "Run SMS automations and delivery tracking. Use --loop in a separate Railway worker."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        legacy_processed = 0
        delivery_updates = 0
        last_automation = 0.0
        last_delivery_sync = 0.0

        while True:
            close_old_connections()
            now = time.monotonic()

            if now - last_automation >= 60:
                try:
                    run_scheduled_automations()
                except Exception as exc:
                    self.stderr.write(f"Communication automation check failed safely: {exc}")
                last_automation = now

            if now - last_delivery_sync >= 5:
                try:
                    delivery_updates += sync_delivery_reports()
                except Exception as exc:
                    self.stderr.write(f"SMS delivery-status check failed safely: {exc}")
                last_delivery_sync = now

            try:
                recover_stale()
                # Compatibility drain only: new sends never enter a queued state.
                found_legacy = process_one()
            except ValidationError as exc:
                raise CommandError("; ".join(exc.messages))

            if found_legacy:
                legacy_processed += 1

            if not options["loop"]:
                if not found_legacy or legacy_processed >= options["limit"]:
                    break
            if not found_legacy:
                time.sleep(1)

        self.stdout.write(
            f"Delivery tracking updated {delivery_updates} SMS record(s); "
            f"processed {legacy_processed} legacy pending record(s)."
        )
