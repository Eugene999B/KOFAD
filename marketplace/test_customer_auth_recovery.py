"""Recovered customer sign-in methods and low-cost login flows.

Provider sends are mocked; no email/SMS is delivered by these tests.
"""
from unittest.mock import patch

from django.test import override_settings

from marketplace.models import CustomerEmailRecovery, EmailIdentity
from marketplace.tests import MarketFixtures


class CustomerEntryRecoveryTests(MarketFixtures):
    @patch("marketplace.views.services.send_otp")
    def test_existing_phone_skips_chargeable_sms(self, send):
        response = self.client.post("/market/access/", {"phone": "0241234567"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/market/account/login/")
        send.assert_not_called()

    @patch("marketplace.views.services.send_otp")
    def test_new_number_uses_registration_challenge_not_login(self, send):
        response = self.client.post("/market/access/", {"phone": "0245559000"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/market/account/verify/")
        self.assertEqual(send.call_args.args[:2], ("+233245559000", "register"))

    def test_only_verified_identity_allows_email_password_login(self):
        EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk,
            email="verified.recovery@example.com",
        )
        payload = {"phone": "verified.recovery@example.com", "password": "Very-strong-customer-password-42!"}
        self.assertEqual(self.client.post("/market/account/login/", payload).status_code, 200)
        row = EmailIdentity.objects.get(owner_id=self.customer.pk)
        from django.utils import timezone
        row.verified_at = timezone.now()
        row.save(update_fields=["verified_at"])
        self.assertEqual(self.client.post("/market/account/login/", payload).status_code, 302)

    def test_anonymous_recovery_page_shows_both_verified_email_and_sms(self):
        response = self.client.get("/market/account/password-reset/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recovery method")
        self.assertContains(response, "SMS to my verified phone")


@override_settings(
    KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_NOTIFICATIONS_ENABLED=True,
    EMAIL_HOST="test.mail.invalid",
    EMAIL_HOST_USER="ci-only@example.com",
    EMAIL_HOST_PASSWORD="ci-test-password",
)
class EmailRecoveryFlowTests(MarketFixtures):
    @patch("marketplace.email_recovery._send_kofad_mail")
    def test_verified_email_recovery_is_one_time_and_private(self, send):
        from django.utils import timezone
        EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk,
            email="recovery.test@example.com", verified_at=timezone.now(),
        )
        response = self.client.post("/market/account/password-reset/", {
            "identifier": "recovery.test@example.com", "channel": "email",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/market/account/password-reset/verify/")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(CustomerEmailRecovery.objects.filter(customer=self.customer).count(), 1)
        page = self.client.get("/market/account/password-reset/verify/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "email")

    @patch("marketplace.email_recovery._send_kofad_mail")
    def test_unknown_email_recovery_does_not_disclose_account_ownership(self, send):
        response = self.client.post("/market/account/password-reset/", {
            "identifier": "no-account@example.com", "channel": "email",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/market/account/password-reset/verify/")
        send.assert_not_called()
