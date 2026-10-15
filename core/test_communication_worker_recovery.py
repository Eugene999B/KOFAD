"""Keep KOFAD communication automation resilient without paid provider calls."""
from io import StringIO
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from core.management.commands.process_sms import Command, recover_pending_deliveries


class RecoveryIsolationTests(SimpleTestCase):
    @patch("core.management.commands.process_sms.recover_stale_whatsapp")
    @patch("core.management.commands.process_sms.recover_stale", side_effect=RuntimeError("temporary provider error"))
    def test_sms_failure_does_not_skip_whatsapp_stale_recovery(self, sms, whatsapp):
        errors = StringIO()
        recover_pending_deliveries(errors)
        sms.assert_called_once()
        whatsapp.assert_called_once()
        self.assertIn("SMS recovery failed safely", errors.getvalue())
        self.assertNotIn("temporary provider error", errors.getvalue())

    @patch("core.management.commands.process_sms.recover_stale_whatsapp", side_effect=RuntimeError("temporary provider error"))
    @patch("core.management.commands.process_sms.recover_stale")
    def test_whatsapp_failure_does_not_crash_worker(self, sms, whatsapp):
        errors = StringIO()
        recover_pending_deliveries(errors)
        sms.assert_called_once()
        whatsapp.assert_called_once()
        self.assertIn("WhatsApp recovery failed safely", errors.getvalue())
        self.assertNotIn("temporary provider error", errors.getvalue())


@override_settings(
    SMS_ENABLED=False, KOFAD_EMAIL_ENABLED=False,
    EMAIL_AUTOMATIONS_ENABLED=False, KOFAD_EMAIL_CENTER_ENABLED=False,
)
class ScheduledCampaignRecoveryTests(TestCase):
    @patch("core.pos_hubtel.reconcile_due")
    @patch("marketplace.paystack_reconciliation.reconcile_due")
    @patch("core.pos_paystack.reconcile_due")
    @patch("marketplace.hubtel.reconcile_due")
    @patch("core.notification_engine.process_email_outbox")
    @patch("core.email_center.deliver_outgoing")
    @patch("core.email_identity.deliver_pending")
    @patch("core.email_campaigns.queue_active_campaigns")
    @patch("core.management.commands.process_sms.run_scheduled_automations")
    @patch("core.management.commands.process_sms.process_order_sms")
    @patch("core.management.commands.process_sms.sync_delivery_reports", return_value=0)
    @patch("core.management.commands.process_sms.process_whatsapp_queue")
    @patch("core.management.commands.process_sms.recover_pending_deliveries")
    def test_command_queues_opt_in_campaigns_independently(
        self, recovery, whatsapp, sync, order, automation, queue,
        email_identity, outgoing, engine, hubtel, paystack, market_paystack, pos_hubtel,
    ):
        # Other hourly housekeeping has no external settings in this test.
        Command(stdout=StringIO(), stderr=StringIO()).handle(loop=False)
        queue.assert_called_once_with(limit=75)
        recovery.assert_called_once()
        # Hubtel requests must not leave the unwhitelisted SMS worker.
        pos_hubtel.assert_not_called()
        hubtel.assert_not_called()
        order.assert_called_once()
        sync.assert_called_once()
        whatsapp.assert_called_once()

    @patch("core.pos_hubtel.reconcile_due", side_effect=RuntimeError("simulated merchant failure"))
    @patch("marketplace.paystack_reconciliation.reconcile_due")
    @patch("core.pos_paystack.reconcile_due")
    @patch("marketplace.hubtel.reconcile_due")
    @patch("core.notification_engine.process_email_outbox")
    @patch("core.email_center.deliver_outgoing")
    @patch("core.email_identity.deliver_pending")
    @patch("core.email_campaigns.queue_active_campaigns", side_effect=RuntimeError("campaign issue"))
    @patch("core.management.commands.process_sms.run_scheduled_automations")
    @patch("core.management.commands.process_sms.process_order_sms")
    @patch("core.management.commands.process_sms.sync_delivery_reports", return_value=0)
    @patch("core.management.commands.process_sms.process_whatsapp_queue")
    @patch("core.management.commands.process_sms.recover_pending_deliveries")
    def test_campaign_failure_does_not_block_receipt_worker(
        self, recovery, whatsapp, sync, order, automation, queue,
        email_identity, outgoing, engine, hubtel, paystack, market_paystack, pos_hubtel,
    ):
        output = StringIO()
        Command(stdout=StringIO(), stderr=output).handle(loop=False)
        self.assertIn("Campaign queuing failed safely", output.getvalue())
        self.assertNotIn("simulated merchant failure", output.getvalue())
        pos_hubtel.assert_not_called()
        hubtel.assert_not_called()
        market_paystack.assert_called_once()
        order.assert_called_once()
        whatsapp.assert_called_once()
