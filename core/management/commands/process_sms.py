import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.automations import run_scheduled_automations
from core.sms.service import recover_stale, sync_delivery_reports
from core.whatsapp_delivery import process_whatsapp_queue, recover_stale_whatsapp
from marketplace.notifications import process_order_sms


class Command(BaseCommand):
    help = "Run communication automations and Arkesel delivery tracking."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        delivery_updates = 0
        last_automation = 0.0
        last_payment_check = 0.0
        last_delivery_sync = 0.0
        last_recovery = None
        last_security_cleanup = 0.0

        while True:
            close_old_connections()
            now = time.monotonic()

            if now - last_payment_check >= 5:
                try:
                    from marketplace.hubtel import reconcile_due
                    reconcile_due()
                except Exception:
                    self.stderr.write("Hubtel reconciliation check failed; will retry.")
                try:
                    from core.pos_paystack import reconcile_due as reconcile_pos_paystack
                    reconcile_pos_paystack()
                except Exception:
                    self.stderr.write("Paystack POS reconciliation check failed; will retry.")
                try:
                    from core.pos_hubtel import reconcile_due as reconcile_pos_hubtel
                    reconcile_pos_hubtel()
                except Exception:
                    self.stderr.write("Hubtel POS reconciliation check failed; will retry.")
                try:
                    from marketplace.paystack_reconciliation import reconcile_due as reconcile_market_paystack
                    reconcile_market_paystack()
                except Exception:
                    self.stderr.write("Paystack checkout reconciliation failed safely; will retry.")
                last_payment_check = now

            if now - last_automation >= 60:
                # Reuse the existing communications worker: no extra Railway
                # service, cron deployment or container charges for email.
                try:
                    from core.email_identity import deliver_pending
                    deliver_pending(limit=15)
                except Exception:
                    self.stderr.write("Email notification queue check failed safely; will retry.")
                try:
                    run_scheduled_automations()
                except Exception as exc:
                    self.stderr.write(f"Communication automation check failed safely: {exc}")
                last_automation = now

            if now - last_security_cleanup >= 3600:
                try:
                    from core.whatsapp import purge_webhook_evidence
                    from core.whatsapp_bot import purge_old_replies
                    purge_webhook_evidence()
                    purge_old_replies()
                except Exception as exc:
                    self.stderr.write(f"Security retention cleanup failed safely: {exc}")
                last_security_cleanup = now

            if now - last_delivery_sync >= 5:
                try:
                    process_order_sms()
                except Exception as exc:
                    self.stderr.write(f"Order SMS outbox check failed safely: {exc}")
                try:
                    delivery_updates += sync_delivery_reports()
                except Exception as exc:
                    self.stderr.write(f"SMS delivery-status check failed safely: {exc}")
                try:
                    process_whatsapp_queue()
                except Exception as exc:
                    self.stderr.write(f"WhatsApp queue check failed safely: {exc}")
                try:
                    from core.whatsapp_bot import process_replies
                    process_replies()
                except Exception:
                    self.stderr.write("WhatsApp assistant queue failed safely; will retry.")
                last_delivery_sync = now

            # Payment checks and receipt delivery both run independently of any customer browser.
            # Stale-message recovery remains less frequent because those records need time to age.
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
