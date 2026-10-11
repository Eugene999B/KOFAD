"""Restore safety for settled/refunded Market orders and card-only attempts.

Every provider confirmation here is synthetic. These tests NEVER invoke a
Paystack or Hubtel API, SMS or real payment.
"""
from django.core.exceptions import ValidationError
from django.test import override_settings

from marketplace import services
from marketplace.models import MarketPaymentAttempt, OrderEvent
from marketplace.tests import MarketFixtures


@override_settings(SMS_ENABLED=False)
class MarketSettlementRecoveryTests(MarketFixtures):
    def attempt(self, order, reference, *, flow="card"):
        return MarketPaymentAttempt.objects.create(
            order=order,
            provider="paystack",
            reference=reference,
            amount=order.total,
            currency="GHS",
            status="pending",
            verification_summary={"flow": flow},
        )

    def provider_data(self, order, *, channel="card"):
        return {
            "status": "success", "amount": int(order.total * 100),
            "currency": "GHS", "channel": channel, "id": 99,
        }

    def test_refunded_order_cannot_become_paid_again(self):
        order = self.order()
        order.payment_status = "refunded"
        order.status = "cancelled"
        order.payment_reference = "original-payment"
        order.save(update_fields=["payment_status", "status", "payment_reference"])
        new_attempt = self.attempt(order, "different-refunded-payment")
        result = services.finalize_payment(new_attempt.reference, self.provider_data(order))
        self.assertEqual(result.pk, order.pk)
        order.refresh_from_db()
        new_attempt.refresh_from_db()
        self.assertEqual(order.payment_status, "refunded")
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(order.payment_reference, "original-payment")
        self.assertEqual(new_attempt.status, "attention")
        self.assertEqual(OrderEvent.objects.filter(order=order, status="payment_attention").count(), 1)
        self.assertIsNone(new_attempt.next_check_at)

    def test_duplicate_success_after_paid_is_reconciliation_only(self):
        order = self.order()
        order.payment_status = "paid"
        order.payment_reference = "first-confirmed-reference"
        order.save(update_fields=["payment_status", "payment_reference"])
        duplicate = self.attempt(order, "second-confirmed-reference")
        for _ in range(2):
            services.finalize_payment(duplicate.reference, self.provider_data(order))
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, "attention")
        self.assertEqual(OrderEvent.objects.filter(order=order, status="payment_attention").count(), 1)
        order.refresh_from_db()
        self.assertEqual(order.payment_reference, "first-confirmed-reference")

    def test_card_only_attempt_refuses_verified_mobile_money(self):
        order = self.order()
        attempt = self.attempt(order, "card-channel-mismatch")
        with self.assertRaisesMessage(ValidationError, "channel"):
            services.finalize_payment(attempt.reference, self.provider_data(order, channel="mobile_money"))
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "attention")
        order.refresh_from_db()
        self.assertNotEqual(order.payment_status, "paid")

    def test_missing_verification_summary_does_not_crash(self):
        order = self.order()
        attempt = self.attempt(order, "no-summary")
        attempt.verification_summary = {}
        attempt.save(update_fields=["verification_summary"])
        # A verified card payment without a channel restriction still follows
        # regular amount/currency/ledger validation instead of raising AttributeError.
        result = services.finalize_payment(attempt.reference, self.provider_data(order))
        self.assertEqual(result.pk, order.pk)
