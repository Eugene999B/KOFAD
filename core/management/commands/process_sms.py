import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.automations import run_scheduled_automations
from core.sms.service import recover_stale, sync_delivery_reports
from core.whatsapp_delivery import process_whatsapp_queue, recover_stale_whatsapp


class Command(BaseCommand):
    help = "Run communication automations and Arkesel delivery tracking."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        delivery_updates = 0
        last_automation = 0.0
        last_delivery_sync = 0.0
        last_recovery = None

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
                    process_whatsapp_queue()
                except Exception as exc:
                    self.stderr.write(f"SMS delivery-status check failed safely: {exc}")
                last_delivery_sync = now

            # These records are only stale after five minutes. Avoid querying
            # for them every second while preserving five-second receipt polling.
            if last_recovery is None or now - last_recovery >= 30:
                recover_stale()
                recover_stale_whatsapp()
                last_recovery = now

            if not options["loop"]:
                break
            time.sleep(5)

        self.stdout.write(
            f"Communication automation complete; delivery tracking updated {delivery_updates} SMS record(s)."
        )
