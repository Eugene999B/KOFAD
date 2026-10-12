import time

import requests
from django.conf import settings

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.automations import run_scheduled_automations
from core.sms.service import recover_stale, sync_delivery_reports
from core.whatsapp_delivery import process_whatsapp_queue, recover_stale_whatsapp
from marketplace.notifications import process_order_sms


def recover_pending_deliveries(error_stream):
    """Isolate SMS and WhatsApp recovery failures so one cannot stop the worker."""
    for label, recover in (("SMS", recover_stale), ("WhatsApp", recover_stale_whatsapp)):
        try:
            recover()
        except Exception:
            # Do not re-submit unknown provider outcomes here; defer to each
            # channel's existing idempotent stale-delivery reconciler.
            error_stream.write(f"{label} recovery failed safely; will retry.")


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
                    # Existing cashier Hubtel checkout records need their own
                    # reconciliation; Market Hubtel does not inspect HeldSale.
                    from core.pos_hubtel import reconcile_due as reconcile_pos_hubtel
                    reconcile_pos_hubtel()
                except Exception:
                    self.stderr.write("Hubtel POS reconciliation check failed safely; will retry.")
                try:
                    from marketplace.paystack_reconciliation import reconcile_due as reconcile_market_paystack
                    reconcile_market_paystack()
                except Exception:
                    self.stderr.write("Paystack checkout reconciliation failed safely; will retry.")
                last_payment_check = now

            if now - last_automation >= 60:
                try:
                    run_scheduled_automations()
                except Exception:
                    self.stderr.write("Communication automation check failed safely; will retry.")
                # Independent email queues: one failure must never block receipts,
                # approved staff letters or verified-account notifications.
                try:
                    from core.notification_engine import process_email_outbox
                    process_email_outbox(limit=10)
                except Exception:
                    self.stderr.write("Intelligent email notice delivery failed safely; will retry.")
                try:
                    from core.email_center import deliver_outgoing
                    deliver_outgoing(limit=10)
                except Exception:
                    self.stderr.write("Business email outbox check failed safely; will retry.")
                try:
                    from core.email_campaigns import queue_active_campaigns
                    queue_active_campaigns(limit=75)
                except Exception:
                    self.stderr.write("Campaign queuing failed safely; will retry.")
                try:
                    from core.email_identity import deliver_pending
                    deliver_pending(limit=10)
                except Exception:
                    self.stderr.write("Verified-account email outbox check failed safely; will retry.")
                last_automation = now
                # Direct Hubtel status verification must egress from kofad-web,
                # whose Railway static IPs are whitelisted by Hubtel.
                # The communications worker only triggers a short authenticated
                # call; it never calls Hubtel's allowlisted APIs itself.
                reconcile_token = getattr(settings, "HUBTEL_DIRECT_RECONCILE_TOKEN", "")
                if len(reconcile_token) >= 48 and getattr(settings, "HUBTEL_DIRECT_EGRESS_CONFIRMED", False):
                    if settings.MARKET_SITE_ORIGIN.rstrip("/") == "https://market.kofadimpex.com":
                        try:
                            response = requests.post(
                                "https://market.kofadimpex.com/internal/hubtel-direct/reconcile/",
                                headers={"X-Kofad-Direct-Reconcile": reconcile_token},
                                timeout=15, allow_redirects=False,
                            )
                            if response.status_code != 200:
                                self.stderr.write("Hubtel Direct web-egress check was not accepted.")
                        except requests.RequestException:
                            self.stderr.write("Hubtel Direct web-egress status check temporarily unavailable.")

            if now - last_security_cleanup >= 3600:
                try:
                    from core.whatsapp import purge_webhook_evidence
                    from core.whatsapp_bot import purge_old_replies
                    purge_webhook_evidence()
                    purge_old_replies()
                except Exception as exc:
                    self.stderr.write(f"Security retention cleanup failed safely: {exc}")
                try:
                    from core.telegram_delivery import purge_old_telegram
                    purge_old_telegram()
                except Exception:
                    self.stderr.write("Telegram security retention cleanup failed safely.")
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
                try:
                    from core.telegram_delivery import deliver_pending as deliver_telegram
                    deliver_telegram(limit=10)
                except Exception:
                    self.stderr.write("Telegram notice delivery failed safely; will retry.")
                last_delivery_sync = now

            # Payment checks and receipt delivery both run independently of any customer browser.
            # Stale-message recovery remains less frequent because those records need time to age.
            if last_recovery is None or now - last_recovery >= 30:
                recover_pending_deliveries(self.stderr)
                last_recovery = now

            if not options["loop"]:
                break
            time.sleep(5)

        self.stdout.write(
            f"Communication automation complete; delivery tracking updated {delivery_updates} SMS record(s)."
        )
