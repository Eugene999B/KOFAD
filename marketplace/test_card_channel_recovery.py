"""Verify hosted Paystack checkout never silently becomes an unrestricted charge."""
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import override_settings

from marketplace import services
from marketplace.tests import MarketFixtures


@override_settings(PAYSTACK_SECRET_KEY="sk_test_synthetic_card_only")
class PaystackCardChannelRecoveryTests(MarketFixtures):
    @patch("marketplace.services.requests.post")
    def test_hosted_card_checkout_persists_card_only_channel(self, submit):
        order = self.order()
        provider_reply = Mock(status_code=200)
        provider_reply.json.side_effect = lambda: {
            "status": True,
            "data": {
                "authorization_url": "https://checkout.paystack.com/test-card-auth",
                "access_code": "test-card-auth",
                "reference": submit.call_args.kwargs["json"]["reference"],
            },
        }
        submit.return_value = provider_reply

        attempt = services.initialize_paystack(
            order, "https://market.kofadimpex.com/market/payment/return/",
        )
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(submit.call_args.kwargs["json"]["channels"], ["card"])
        self.assertEqual(attempt.verification_summary["flow"], "card")
        with self.assertRaises(ValidationError):
            services.finalize_payment(attempt.reference, {
                "status": "success", "channel": "mobile_money",
                "currency": "GHS", "amount": int(order.total * 100),
            })
        order.refresh_from_db()
        self.assertNotEqual(order.payment_status, "paid")
        self.assertIsNone(order.sale_document_id)
