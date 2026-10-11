"""Regression tests for payment-settled staff notifications lost by the Oct 10 promotion.

All provider receipts here are synthetic; no SMS, WhatsApp, email or payment API calls.
"""
from unittest.mock import patch

from django.test import override_settings

from marketplace import services
from marketplace.models import MarketPaymentAttempt
from marketplace.tests import MarketFixtures


@override_settings(SMS_ENABLED=False, EMAIL_AUTOMATIONS_ENABLED=False)
class StaffPaymentAlertRecoveryTests(MarketFixtures):
    def attempt(self, order, reference):
        return MarketPaymentAttempt.objects.create(
            order=order, provider="paystack", reference=reference,
            amount=order.total, currency="GHS",
            status="pending", verification_summary={"flow": "card"},
        )

    @staticmethod
    def verified_card(order):
        return {
            "status": "success", "amount": int(order.total * 100),
            "currency": "GHS", "channel": "card", "id": 19801,
        }

    @patch("core.email_identity.enqueue_staff_payment_alerts")
    def test_verified_first_settlement_queues_exactly_one_staff_alert(self, queue):
        order = self.order()
        attempt = self.attempt(order, "staff-alert-first-payment")
        with self.captureOnCommitCallbacks(execute=True):
            services.finalize_payment(attempt.reference, self.verified_card(order))
        queue.assert_called_once()
        self.assertEqual(queue.call_args.args[0].pk, order.pk)

        # An idempotent provider callback is not a fresh financial event.
        with self.captureOnCommitCallbacks(execute=True):
            services.finalize_payment(attempt.reference, self.verified_card(order))
        queue.assert_called_once()

    @patch("core.email_identity.enqueue_staff_payment_alerts")
    def test_unverified_payment_does_not_queue_staff_alert(self, queue):
        from django.core.exceptions import ValidationError

        order = self.order()
        attempt = self.attempt(order, "staff-alert-unverified")
        with self.assertRaises(ValidationError):
            with self.captureOnCommitCallbacks(execute=True):
                services.finalize_payment(attempt.reference, {
                    **self.verified_card(order), "status": "failed",
                })
        queue.assert_not_called()

    @patch("core.email_identity.enqueue_staff_payment_alerts", side_effect=RuntimeError("synthetic mail error"))
    def test_email_failure_cannot_reject_verified_payment(self, queue):
        order = self.order()
        attempt = self.attempt(order, "staff-alert-mail-failed")
        with self.assertLogs("marketplace.services", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                services.finalize_payment(attempt.reference, self.verified_card(order))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        queue.assert_called_once()
