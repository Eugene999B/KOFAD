"""Customer account-linked MoMo number and order-bound alternate OTP policy."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from marketplace.models import OtpThrottle, VerifiedMomoPhone
from marketplace.tests import MarketFixtures
from .models import DirectTransaction
from .tests import READY


@override_settings(**READY, CUSTOMER_OTP_ENABLED=True)
class CustomerPaymentPhonePolicyTests(MarketFixtures):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()
        self.other = "+233501112233"

    def endpoint(self, order, suffix=""):
        return f"/market/orders/{order.pk}/hubtel-direct/{suffix}"

    def send(self, order, action, phone=None, channel="mtn-gh", code=None):
        body = {"action": action, "phone": phone or self.other, "channel": channel}
        if code is not None:
            body["code"] = code
        return self.client.post(
            self.endpoint(order, "verify-phone/"), data=json.dumps(body),
            content_type="application/json",
        )

    def pay(self, order, phone=None, channel="mtn-gh"):
        return self.client.post(
            self.endpoint(order),
            data=json.dumps({"phone": phone or self.other,
                             "channel": channel, "customer_authorized": True}),
            content_type="application/json",
        )

    def fake_send(self, phone, purpose, request=None):
        self.assertEqual(purpose, "momo")
        OtpThrottle.objects.update_or_create(
            phone=phone, purpose=purpose,
            defaults={
                "last_sent_at": timezone.now(),
                "expires_at": timezone.now() + timedelta(minutes=10),
                "verified_at": None,
                "code_digest": "test-only",
            },
        )
        return phone

    def fake_verify(self, phone, code, purpose):
        self.assertEqual(code, "123456")
        self.assertEqual(purpose, "momo")
        OtpThrottle.objects.filter(phone=phone, purpose=purpose).update(
            verified_at=timezone.now(), code_digest=""
        )
        return phone

    @patch("hubtel_direct.service.dispatch_once")
    def test_account_linked_phone_needs_no_otp_or_wallet_name_inquiry(self, dispatch):
        order = self.order()
        VerifiedMomoPhone.objects.all().delete()
        with patch("hubtel_direct.verification.inquiry") as wallet_name:
            response = self.pay(order, self.customer.phone)
        self.assertEqual(response.status_code, 202, response.content)
        wallet_name.assert_not_called()
        self.assertEqual(DirectTransaction.objects.filter(order=order).count(), 1)
        dispatch.assert_called_once()

    @patch("hubtel_direct.service.dispatch_once")
    def test_old_verified_wallet_not_enough_for_alternative(self, dispatch):
        order = self.order()
        VerifiedMomoPhone.objects.create(customer=self.customer, phone=self.other)
        response = self.pay(order)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(DirectTransaction.objects.filter(order=order).count(), 0)
        dispatch.assert_not_called()

    @patch("hubtel_direct.service.dispatch_once")
    def test_alternative_otp_is_bound_to_order_and_consumed_at_payment(self, dispatch):
        order = self.order()
        with patch("hubtel_direct.views.market_services.send_otp", side_effect=self.fake_send):
            sent = self.send(order, "send")
        self.assertEqual(sent.status_code, 200, sent.content)
        self.assertFalse(sent.json()["verified"])
        other_order = self.order()
        self.assertEqual(self.pay(other_order).status_code, 400)
        # Verifying on another order cannot use the first order's SMS.
        self.assertEqual(self.send(other_order, "verify", code="123456").status_code, 400)
        with patch("hubtel_direct.views.market_services.verify_otp", side_effect=self.fake_verify):
            confirmed = self.send(order, "verify", code="123456")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertTrue(confirmed.json()["verified"])
        self.assertEqual(self.pay(order).status_code, 202)
        self.assertNotIn("checkout_alt_momo:" + str(order.pk), self.client.session)
        self.assertEqual(self.pay(other_order).status_code, 400)
        self.assertEqual(DirectTransaction.objects.filter(order=order).count(), 1)
        dispatch.assert_called_once()

    def test_alternative_otp_cannot_be_used_on_another_network(self):
        order = self.order()
        with patch("hubtel_direct.views.market_services.send_otp", side_effect=self.fake_send):
            self.assertEqual(self.send(order, "send").status_code, 200)
        with patch("hubtel_direct.views.market_services.verify_otp") as verified:
            response = self.send(order, "verify", channel="vodafone-gh", code="123456")
        self.assertEqual(response.status_code, 400)
        verified.assert_not_called()

    def test_customer_payment_screen_has_no_wallet_name_verification(self):
        from django.test import override_settings
        order = self.order()
        page = self.client.get(f"/market/orders/{order.pk}/")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertNotIn("data-direct-name-review", html)
        self.assertNotIn("data-direct-registered-name", html)
        self.assertIn("Use another number", html)
        self.assertIn("My account number", html)
