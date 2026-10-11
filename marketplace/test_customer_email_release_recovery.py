"""Regression checks for recovered verified-email Market customer authentication.

All email/SMS sending is mocked; never contacts paid providers.
"""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from marketplace.models import CustomerAccount, CustomerEmailRecovery, EmailIdentity, OtpThrottle


class CustomerEmailReleaseRecoveryTests(TestCase):
    def setUp(self):
        self.customer = CustomerAccount(phone="+233241234567", full_name="Verified Customer", email="customer@example.com", verified_at=timezone.now())
        self.customer.set_password("Original-strong-pass-91!")
        self.customer.save()
        EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk,
            email="customer@example.com", verified_at=timezone.now(),
        )

    def test_password_login_with_verified_email(self):
        response = self.client.post("/market/account/login/", {
            "phone": "CUSTOMER@example.com",
            "password": "Original-strong-pass-91!",
        })
        self.assertRedirects(response, "/market/account/", fetch_redirect_response=False)
        self.assertEqual(self.client.session.get("market_customer_id"), self.customer.pk)
        self.assertTrue(OtpThrottle.objects.filter(phone__startswith="email:", purpose="login").exists())

    def test_wrong_email_password_does_not_sign_in(self):
        response = self.client.post("/market/account/login/", {
            "phone": "customer@example.com", "password": "incorrect",
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("market_customer_id", self.client.session)

    def test_unverified_profile_address_never_grants_email_login(self):
        unverified = CustomerAccount(phone="+233241111222", full_name="Unverified", email="unverified@example.com")
        unverified.set_password("An-unverified-password-91!")
        unverified.save()
        response = self.client.post("/market/account/login/", {
            "phone": "unverified@example.com", "password": "An-unverified-password-91!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("market_customer_id", self.client.session)

    @patch("marketplace.email_recovery._consume_customer_otp_budget")
    @patch("marketplace.email_recovery._send_kofad_mail")
    @patch("marketplace.email_recovery.secrets.randbelow", return_value=123456)
    @patch("marketplace.email_recovery.delivery_ready", return_value=True)
    def test_verified_email_recovery_is_one_use(self, ready, random, send_mail, budget):
        response = self.client.post("/market/account/password-reset/", {
            "channel": "email", "identifier": "customer@example.com",
        })
        self.assertRedirects(response, "/market/account/password-reset/verify/", fetch_redirect_response=False)
        challenge_id = self.client.session.get("market_reset_email_id")
        self.assertTrue(challenge_id)
        self.assertEqual(send_mail.call_count, 1)

        response = self.client.post("/market/account/password-reset/verify/", {"code": "123456"})
        self.assertRedirects(response, "/market/account/password-reset/finish/", fetch_redirect_response=False)

        response = self.client.post("/market/account/password-reset/finish/", {
            "password": "Fresh-customer-password-2026-Q!",
            "password_confirm": "Fresh-customer-password-2026-Q!",
        })
        self.assertRedirects(response, "/market/", fetch_redirect_response=False)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.check_password("Fresh-customer-password-2026-Q!"))
        self.assertTrue(CustomerEmailRecovery.objects.get(pk=challenge_id).used)
        self.assertNotIn("market_reset_email_id", self.client.session)

    @patch("marketplace.email_recovery._consume_customer_otp_budget")
    @patch("marketplace.email_recovery._send_kofad_mail")
    @patch("marketplace.email_recovery.delivery_ready", return_value=True)
    def test_unknown_email_recovery_does_not_reveal_account(self, ready, send_mail, budget):
        response = self.client.post("/market/account/password-reset/", {
            "channel": "email", "identifier": "unknown@example.com",
        })
        self.assertRedirects(response, "/market/account/password-reset/verify/", fetch_redirect_response=False)
        send_mail.assert_not_called()

    @patch("marketplace.views.services.send_otp")
    def test_sms_recovery_remains_available(self, send_otp):
        response = self.client.post("/market/account/password-reset/", {
            "channel": "sms", "identifier": "+233241234567",
        })
        self.assertRedirects(response, "/market/account/password-reset/verify/", fetch_redirect_response=False)
        send_otp.assert_called_once()

    def test_existing_account_never_authenticated_from_registration_finish(self):
        session = self.client.session
        session["market_verified_phone"] = self.customer.phone
        session.save()
        response = self.client.get("/market/account/finish/")
        self.assertRedirects(response, "/market/account/login/", fetch_redirect_response=False)
        self.assertNotIn("market_customer_id", self.client.session)
        self.assertNotIn("market_verified_phone", self.client.session)
