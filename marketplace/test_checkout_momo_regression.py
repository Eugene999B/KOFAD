"""Restore tested first-checkout payment routing without calling paid providers."""
from unittest.mock import patch

from django.test import override_settings

from marketplace.models import OnlineOrder, MarketPaymentAttempt
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

    @patch("marketplace.views.hubtel.initialize_payment")
    @patch("marketplace.views.services.initialize_paystack")
    @patch("hubtel_direct.client.enabled", return_value=True)
    def test_new_orders_use_direct_momo_only(self, ready, paystack, hosted):
        self.sign_in_with_cart()
        page = self.client.get("/market/checkout/")
        self.assertContains(page, 'name="direct_momo_phone"')
        self.assertContains(page, "Continue to wallet verification")
        payload = self.checkout_payload("hosted")
        payload["direct_momo_phone"] = self.customer.phone
        payload["direct_momo_channel"] = "mtn-gh"
        response = self.client.post("/market/checkout/", payload)
        order = OnlineOrder.objects.latest("created_at")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/market/orders/{order.pk}/")
        self.assertFalse(order.payment_attempts.exists())
        self.assertEqual(order.payment_status, "unpaid")
        paystack.assert_not_called()
        hosted.assert_not_called()
        self.assertContains(self.client.get(f"/market/orders/{order.pk}/"), "Pay with Direct Mobile Money")

    @patch("hubtel_direct.client.enabled", return_value=False)
    def test_disabled_direct_does_not_start_hosted_payment(self, ready):
        self.sign_in_with_cart()
        page = self.client.get("/market/checkout/")
        self.assertContains(page, "Direct Mobile Money is not active yet")
        payload = self.checkout_payload("hosted")
        payload["direct_momo_phone"] = self.customer.phone
        payload["direct_momo_channel"] = "mtn-gh"
        self.assertEqual(self.client.post("/market/checkout/", payload).status_code, 200)
        self.assertFalse(OnlineOrder.objects.exists())

    def test_hosted_payment_status_never_confuses_accepted_request_with_paid(self):
        order = self.order()
        MarketPaymentAttempt.objects.create(
            order=order, provider="paystack", reference="KOFAD-STAGE-TEST-001",
            amount=order.total, currency="GHS", status="pending",
        )
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()
        url = "/market/orders/" + str(order.pk) + "/checkout/verification/"
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-kpay-mode="payment"')
        pending = self.client.get(url + "?format=json").json()
        self.assertEqual(pending["status"], "pending")
        self.assertFalse(pending["paid"])
        order.payment_status = "paid"
        order.save(update_fields=["payment_status"])
        unposted = self.client.get(url + "?format=json").json()
        self.assertEqual(unposted["status"], "attention")
        self.assertFalse(unposted["paid"])
