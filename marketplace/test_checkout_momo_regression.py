"""Restore tested first-checkout payment routing without calling paid providers."""
from unittest.mock import patch

from django.test import override_settings

from marketplace.models import OnlineOrder
from marketplace.tests import MarketFixtures


@override_settings(
    PAYSTACK_SECRET_KEY="sk_test_checkout_only",
    PAYSTACK_CUSTOMER_MOMO_ENABLED=True,
    CUSTOMER_OTP_ENABLED=True,
)
class FirstCheckoutPaymentRecoveryTests(MarketFixtures):
    def sign_in_with_cart(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session["market_cart"] = {str(self.listing.pk): 1}
        session.save()

    def checkout_payload(self, method):
        return {
            "fulfilment": "pickup",
            "recipient_name": self.customer.full_name,
            "phone": self.customer.phone,
            "email": self.customer.email,
            "payment_method": method,
            "momo_phone": self.customer.phone,
            "momo_network": "mtn",
        }

    @patch("marketplace.paystack_momo.requests.post")
    @patch("marketplace.momo_security.services.send_otp")
    def test_checkout_first_momo_requires_sms_before_charge(self, otp, charge):
        self.sign_in_with_cart()
        response = self.client.post("/market/checkout/", self.checkout_payload("momo"))
        order = OnlineOrder.objects.latest("created_at")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/market/orders/{order.pk}/")
        otp.assert_called_once()
        charge.assert_not_called()
        self.assertFalse(order.payment_attempts.exists())
        pending = self.client.session["market_momo_phone_pending"]
        self.assertEqual(pending["order_id"], str(order.pk))
        self.assertContains(self.client.get(f"/market/orders/{order.pk}/"), "Verify your Mobile Money number")

    @patch("marketplace.views.hubtel.initialize_payment")
    @patch("marketplace.views.services.initialize_paystack")
    def test_checkout_card_uses_selected_provider(self, paystack, hubtel):
        self.sign_in_with_cart()
        response = self.client.post("/market/checkout/", self.checkout_payload("hosted"))
        order = OnlineOrder.objects.latest("created_at")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/market/orders/{order.pk}/payment/launch/")
        paystack.assert_called_once()
        hubtel.assert_not_called()
