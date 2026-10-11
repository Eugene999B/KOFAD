"""Regression tests for first-use MoMo phone ownership before charge initiation.

No paid SMS or charge provider calls are made.
"""
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse

from marketplace.models import VerifiedMomoPhone
from marketplace.tests import MarketFixtures


@override_settings(CUSTOMER_OTP_ENABLED=True, SECURE_SSL_REDIRECT=False)
class RestoredMomoPhoneControlTests(MarketFixtures):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    @patch("marketplace.paystack_momo.initialize")
    @patch("marketplace.momo_security.services.send_otp")
    @patch("marketplace.paystack_momo.ready", return_value=True)
    @patch("marketplace.hubtel.selected_provider", return_value="paystack")
    def test_first_momo_payment_requires_phone_challenge(self, provider, ready, send_otp, initialize):
        order = self.order()
        response = self.client.post(reverse("market_order_pay", kwargs={"pk": order.pk}), {
            "payment_method": "momo",
            "momo_phone": self.customer.phone,
            "momo_network": "mtn",
        })
        self.assertEqual(response.status_code, 302)
        send_otp.assert_called_once()
        initialize.assert_not_called()
        self.assertEqual(self.client.session["market_momo_phone_pending"]["order_id"], str(order.pk))
        page = self.client.get(reverse("market_order", kwargs={"pk": order.pk}))
        self.assertContains(page, "Verify your Mobile Money number")
        self.assertContains(page, reverse("customer_momo_phone_verify", kwargs={"pk": order.pk}))

    @patch("marketplace.paystack_momo.initialize")
    @patch("marketplace.momo_security.services.verify_otp")
    @patch("marketplace.momo_security.services.send_otp")
    @patch("marketplace.paystack_momo.ready", return_value=True)
    @patch("marketplace.hubtel.selected_provider", return_value="paystack")
    def test_verified_challenge_is_the_first_point_at_which_provider_runs(
        self, provider, ready, send_otp, verify_otp, initialize,
    ):
        order = self.order()
        self.client.post(reverse("market_order_pay", kwargs={"pk": order.pk}), {
            "payment_method": "momo", "momo_phone": self.customer.phone, "momo_network": "mtn",
        })
        verify_url = reverse("customer_momo_phone_verify", kwargs={"pk": order.pk})
        response = self.client.post(verify_url, {"action": "verify", "code": "123456"})
        self.assertEqual(response.status_code, 302)
        verify_otp.assert_called_once()
        initialize.assert_called_once_with(order, self.customer.phone, "mtn")
        self.assertTrue(VerifiedMomoPhone.objects.filter(customer=self.customer, phone=self.customer.phone).exists())
        self.assertNotIn("market_momo_phone_pending", self.client.session)

    @patch("marketplace.paystack_momo.initialize")
    @patch("marketplace.momo_security.services.verify_otp", side_effect=ValidationError("Incorrect phone code"))
    @patch("marketplace.momo_security.services.send_otp")
    @patch("marketplace.paystack_momo.ready", return_value=True)
    @patch("marketplace.hubtel.selected_provider", return_value="paystack")
    def test_wrong_code_never_triggers_payment(
        self, provider, ready, send_otp, verify_otp, initialize,
    ):
        order = self.order()
        self.client.post(reverse("market_order_pay", kwargs={"pk": order.pk}), {
            "payment_method": "momo", "momo_phone": self.customer.phone, "momo_network": "mtn",
        })
        response = self.client.post(
            reverse("customer_momo_phone_verify", kwargs={"pk": order.pk}),
            {"action": "verify", "code": "000000"},
        )
        self.assertEqual(response.status_code, 302)
        initialize.assert_not_called()
        self.assertFalse(VerifiedMomoPhone.objects.filter(customer=self.customer).exists())
