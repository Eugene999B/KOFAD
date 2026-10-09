from unittest.mock import Mock, patch

from django.test import override_settings
from marketplace.models import CustomerAccount, EmailIdentity, GoogleIdentity
from .test_google_oauth import GOOGLE_TEST, GoogleOAuthContractTests


@GOOGLE_TEST
class GoogleFirstCustomerRegistrationTests(GoogleOAuthContractTests):
    """Verified OIDC subjects can create customer-only accounts without a phone."""

    @patch("core.google_oauth._validated_google_claims")
    @patch("core.google_oauth.requests.post")
    def test_google_customer_creates_name_only_account_without_a_phone(self, post, claims):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"id_token": "fake-verified-google-id-token"}
        claims.return_value = {
            "subject": "brand-new-google-customer-2026",
            "email": "new-shopper@gmail.com",
        }
        pending = self._start("/market/auth/google/login/")
        response = self.client.get("/market/auth/google/callback/", {
            "state": pending["state"], "code": "valid-code",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/market/auth/google/complete/")
        finish = self.client.post("/market/auth/google/complete/", {
            "full_name": "New Market Shopper",
        })
        self.assertEqual(finish.status_code, 302)
        customer = CustomerAccount.objects.get(full_name="New Market Shopper")
        self.assertIsNone(customer.phone)
        self.assertFalse(customer.has_usable_password() if hasattr(customer, "has_usable_password") else
                         __import__("django.contrib.auth.hashers", fromlist=["is_password_usable"]).is_password_usable(customer.password_hash))
        self.assertEqual(self.client.session["market_customer_id"], customer.pk)
        self.assertEqual(GoogleIdentity.objects.get(kind="customer", owner_id=customer.pk).subject, claims.return_value["subject"])
        self.assertEqual(EmailIdentity.objects.get(kind="customer", owner_id=customer.pk).email, "new-shopper@gmail.com")
        self.assertEqual(self.client.get("/market/auth/google/complete/").status_code, 302)

    @patch("core.google_oauth._validated_google_claims")
    @patch("core.google_oauth.requests.post")
    def test_matching_an_existing_verified_email_never_autolinks_google(self, post, claims):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"id_token": "fake"}
        EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk,
            email="existing-shopper@gmail.com",
            verified_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
        )
        claims.return_value = {"subject": "new-google-identity", "email": "existing-shopper@gmail.com"}
        pending = self._start("/market/auth/google/login/")
        response = self.client.get("/market/auth/google/callback/", {
            "state": pending["state"], "code": "valid-code",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(GoogleIdentity.objects.filter(kind="customer").count(), 0)
        self.assertNotIn("market_google_signup_pending", self.client.session)

    @patch("core.google_oauth._validated_google_claims")
    @patch("core.google_oauth.requests.post")
    def test_staff_can_never_self_register_from_google(self, post, claims):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"id_token": "fake"}
        claims.return_value = {"subject": "unauthorised-staff-oidc", "email": "someone@gmail.com"}
        pending = self._start("/auth/google/staff/login/")
        response = self.client.get("/auth/google/staff/callback/", {
            "state": pending["state"], "code": "valid-code",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(GoogleIdentity.objects.filter(kind="staff").exists())
        self.assertNotIn("market_google_signup_pending", self.client.session)
