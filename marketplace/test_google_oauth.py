import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from core import google_oauth
from marketplace.models import EmailIdentity, GoogleIdentity
from .tests import MarketFixtures


GOOGLE_TEST = override_settings(
    KOFAD_GOOGLE_OAUTH_ENABLED=True,
    KOFAD_GOOGLE_CLIENT_ID="kofad-ci.apps.googleusercontent.com",
    KOFAD_GOOGLE_CLIENT_SECRET="integration-test-secret",
    KOFAD_STAFF_SITE_ORIGIN="https://staff.kofadimpex.com",
    KOFAD_MARKET_SITE_ORIGIN="https://market.kofadimpex.com",
    PRIVILEGED_MFA_ENFORCED=False,
)


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


@GOOGLE_TEST
class GoogleOAuthContractTests(MarketFixtures):
    def _login_staff(self):
        self.client.force_login(self.staff)
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session.save()

    def _customer(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def _start(self, path, method="get", password=""):
        if method == "post":
            response = self.client.post(path, {"current_password": password})
        else:
            response = self.client.get(path)
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(urlparse(response["Location"]).hostname, "accounts.google.com")
        self.assertEqual(query["scope"], ["openid email"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(len(query["code_challenge"][0]), 43)
        self.assertTrue(query["nonce"][0])
        self.assertEqual(query["state"][0], self.client.session[google_oauth.SESSION_KEY]["state"])
        return self.client.session[google_oauth.SESSION_KEY]

    @patch("core.google_oauth._validated_google_claims")
    @patch("core.google_oauth.requests.post")
    def test_link_and_login_staff_with_google_subject_preserves_original_access(self, post, claims):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"id_token": "fake-signed-google-token"}
        claims.return_value = {"subject": "google-staff-sub-111111", "email": "owner@gmail.com"}
        self._login_staff()
        pending = self._start("/auth/google/staff/link/", method="post", password="market-owner-password")
        response = self.client.get("/auth/google/staff/callback/", {
            "state": pending["state"], "code": "valid-google-code",
        })
        self.assertRedirects(response, "/workspace/", fetch_redirect_response=False)
        binding = GoogleIdentity.objects.get(kind="staff", owner_id=self.staff.pk)
        self.assertEqual(binding.subject, "google-staff-sub-111111")
        self.assertEqual(EmailIdentity.objects.get(kind="staff", owner_id=self.staff.pk).email, "owner@gmail.com")
        self.assertTrue(self.staff.access.branches.filter(pk=self.branch.pk).exists())
        self.client.logout()
        pending = self._start("/auth/google/staff/login/")
        response = self.client.get("/auth/google/staff/callback/", {
            "state": pending["state"], "code": "valid-google-code",
        })
        self.assertRedirects(response, "/workspace/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["access_version"], self.staff.access.session_version)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.staff.pk))
        # Staff login must still respect the existing privileged MFA policy.
        self.client.logout()
        with override_settings(PRIVILEGED_MFA_ENFORCED=True):
            pending = self._start("/auth/google/staff/login/")
            response = self.client.get("/auth/google/staff/callback/", {
                "state": pending["state"], "code": "valid-google-code",
            })
            self.assertRedirects(response, "/mfa/", fetch_redirect_response=False)

    @patch("core.google_oauth._validated_google_claims")
    @patch("core.google_oauth.requests.post")
    def test_customer_google_binding_retains_verified_phone(self, post, claims):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"id_token": "fake"}
        claims.return_value = {"subject": "google-customer-11111", "email": "shopper@gmail.com"}
        self._customer()
        original_phone = self.customer.phone
        pending = self._start("/market/auth/google/link/", method="post",
                              password="Very-strong-customer-password-42!")
        response = self.client.get("/market/auth/google/callback/", {
            "state": pending["state"], "code": "customer-code",
        })
        self.assertRedirects(response, "/market/account/", fetch_redirect_response=False)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.phone, original_phone)
        self.assertEqual(GoogleIdentity.objects.get(kind="customer", owner_id=self.customer.pk).email, "shopper@gmail.com")
        self.client.session.flush()
        pending = self._start("/market/auth/google/login/")
        response = self.client.get("/market/auth/google/callback/", {
            "state": pending["state"], "code": "customer-code",
        })
        self.assertRedirects(response, "/market/account/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["market_customer_id"], self.customer.pk)

    @patch("core.google_oauth.requests.post")
    def test_forged_or_replayed_callback_never_calls_google(self, post):
        pending = self._start("/market/auth/google/login/")
        bad = self.client.get("/market/auth/google/callback/", {
            "state": "attacker-state", "code": "provider-code",
        })
        self.assertEqual(bad.status_code, 302)
        self.assertNotIn(google_oauth.SESSION_KEY, self.client.session)
        replay = self.client.get("/market/auth/google/callback/", {
            "state": pending["state"], "code": "provider-code",
        })
        self.assertEqual(replay.status_code, 302)
        post.assert_not_called()

    def test_link_requires_current_password_and_current_session(self):
        self.assertEqual(self.client.get("/auth/google/staff/link/").status_code, 405)
        guest = self.client.post("/auth/google/staff/link/", {"current_password": "anything"})
        self.assertEqual(guest.status_code, 302)
        self.assertNotIn(google_oauth.SESSION_KEY, self.client.session)
        self._login_staff()
        wrong = self.client.post("/auth/google/staff/link/", {"current_password": "not-current"})
        self.assertEqual(wrong.status_code, 302)
        self.assertNotIn(google_oauth.SESSION_KEY, self.client.session)
        self.assertEqual(GoogleIdentity.objects.count(), 0)

    def test_disabled_by_default_has_no_google_auth_url(self):
        with override_settings(KOFAD_GOOGLE_OAUTH_ENABLED=False):
            response = self.client.get("/auth/google/staff/login/")
            self.assertEqual(response.status_code, 302)
            self.assertNotIn("accounts.google.com", response["Location"])
            self.assertNotIn(google_oauth.SESSION_KEY, self.client.session)

    @patch("core.google_oauth.requests.get")
    def test_google_token_signature_nonce_audience_and_email_are_verified(self, jwks_request):
        cache.delete("kofad-google-oidc-jwks-v1")
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private.public_key().public_numbers()
        jwks = {"keys": [{
            "kid": "kofad-test-key", "kty": "RSA", "use": "sig",
            "n": b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
            "e": b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
        }]}
        jwks_request.return_value = Mock(status_code=200)
        jwks_request.return_value.json.return_value = jwks
        def token(**updates):
            header = {"alg": "RS256", "kid": "kofad-test-key"}
            claims = {
                "iss": "https://accounts.google.com",
                "aud": "kofad-ci.apps.googleusercontent.com",
                "nonce": "nonce-test", "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "email_verified": True, "email": "Person@Gmail.com",
                "sub": "google-immutable-user-123",
            }
            claims.update(updates)
            signed = b64(json.dumps(header).encode()) + "." + b64(json.dumps(claims).encode())
            sig = private.sign(signed.encode(), padding.PKCS1v15(), hashes.SHA256())
            return signed + "." + b64(sig)
        self.assertEqual(google_oauth._validated_google_claims(
            token(), "nonce-test",
        ), {"subject": "google-immutable-user-123", "email": "person@gmail.com"})
        for updates, nonce in [
            ({"aud": "malicious.app"}, "nonce-test"),
            ({"email_verified": False}, "nonce-test"),
            ({"exp": int(time.time()) - 1}, "nonce-test"),
            ({}, "wrong-nonce"),
        ]:
            with self.assertRaises(ValidationError):
                google_oauth._validated_google_claims(token(**updates), nonce)
        bad_signature = token().rsplit(".", 1)[0] + "." + b64(b"tampered")
        with self.assertRaises(ValidationError):
            google_oauth._validated_google_claims(bad_signature, "nonce-test")
        cache.delete("kofad-google-oidc-jwks-v1")
