"""Prevent loss of the customer account pulse and verified sign-in experience."""
from django.test import override_settings
from django.utils import timezone

from marketplace.models import EmailIdentity
from marketplace.tests import MarketFixtures


@override_settings(SECURE_SSL_REDIRECT=False)
class CustomerAccountRecoveryTests(MarketFixtures):
    def login_customer(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def test_customer_account_shows_summary_security_and_new_email_preferences(self):
        self.login_customer()
        EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk,
            email="verified.account@example.test", verified_at=timezone.now(),
        )
        response = self.client.get("/market/account/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your account at a glance")
        self.assertContains(response, "Paid order value")
        self.assertContains(response, "Verified sign-in email")
        self.assertContains(response, "verified.account@example.test")
        self.assertContains(response, "Manage email &amp; security")
        self.assertContains(response, "Email preferences")
        self.assertContains(response, "transactional_email_enabled")
        self.assertContains(response, "marketing_email_opt_in")
        self.assertContains(response, "experience-upgrade.css")

    def test_customer_without_verified_email_still_sees_security_link(self):
        self.login_customer()
        response = self.client.get("/market/account/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email sign-in not linked yet")
        self.assertContains(response, "/market/account/security/")
        self.assertContains(response, "Email preferences")
