"""PKCE mobile device authorization security tests: identity, replay and revocation."""
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase, override_settings
from django.utils import timezone

from core.tests import Fixtures
from marketplace import services
from marketplace.models import CustomerAccount
from marketplace.mobile_auth_models import MobileAuthorizationGrant, MobileDeviceSession


@override_settings(
    ALLOWED_HOSTS=["testserver", "market.kofadimpex.com", "staff.kofadimpex.com"],
    PRIVILEGED_MFA_ENFORCED=False,
)
class NativeIdentityTests(Fixtures, TestCase):
    verifier = "V" * 43
    state = "nativeDeviceStateIsLongEnough123"
    customer_base = "/market/mobile/v1/"
    staff_base = "/staff/mobile/v1/"

    def setUp(self):
        self.setup_data()
        self.market_customer = CustomerAccount(
            phone="0240000000", full_name="Customer Example", active=True
        )
        self.market_customer.set_password("a-long-testing-password")
        self.market_customer.save()

    def _params(self, channel="customer", **kwargs):
        scheme = "kofadmarket" if channel == "customer" else "kofadstaff"
        values = {
            "client_id": "kofad-market" if channel == "customer" else "kofad-staff",
            "redirect_uri": f"{scheme}://auth/callback",
            "code_challenge": base64.urlsafe_b64encode(
                hashlib.sha256(self.verifier.encode()).digest()
            ).rstrip(b"=").decode(),
            "code_challenge_method": "S256",
            "state": self.state,
        }
        values.update(kwargs)
        return values

    def _grant(self, channel="customer"):
        path = (self.customer_base if channel == "customer" else self.staff_base) + "authorize/"
        host = "market.kofadimpex.com" if channel == "customer" else "staff.kofadimpex.com"
        values = self._params(channel)
        opening = self.client.get(path, values, HTTP_HOST=host, secure=True)
        self.assertEqual(opening.status_code, 200, opening.get("Location"))
        self.assertContains(opening, "Continue to", status_code=200)
        response = self.client.post(path, values, HTTP_HOST=host, secure=True)
        self.assertEqual(response.status_code, 302, response.content[:120])
        location = response["Location"]
        self.assertTrue(location.startswith(values["redirect_uri"] + "?"))
        query = parse_qs(urlsplit(location).query)
        self.assertEqual(query["state"], [self.state])
        return query["code"][0]

    def _token(self, body, channel="customer", origin="https://localhost"):
        path = (self.customer_base if channel == "customer" else self.staff_base) + "token/"
        host = "market.kofadimpex.com" if channel == "customer" else "staff.kofadimpex.com"
        return self.client.post(
            path, data=json.dumps(body), content_type="application/json",
            HTTP_HOST=host, HTTP_ORIGIN=origin, secure=True,
        )

    def _exchange(self, code, channel="customer", **kwargs):
        body = {
            "grant_type": "authorization_code",
            "client_id": "kofad-market" if channel == "customer" else "kofad-staff",
            "redirect_uri": "kofadmarket://auth/callback" if channel == "customer" else "kofadstaff://auth/callback",
            "code_verifier": self.verifier,
            "code": code,
        }
        body.update(kwargs)
        return self._token(body, channel)

    def _me(self, token, channel="customer"):
        path = (self.customer_base if channel == "customer" else self.staff_base) + "me/"
        host = "market.kofadimpex.com" if channel == "customer" else "staff.kofadimpex.com"
        # An actual native request has a bearer token, never the OS browser
        # session cookie. Keep it separate to catch accidental redirects.
        from django.test import Client
        return Client().get(
            path, HTTP_HOST=host, HTTP_ORIGIN="https://localhost",
            HTTP_AUTHORIZATION="Bearer " + token, secure=True,
        )

    def test_customer_login_to_one_time_pkce_code_and_token(self):
        self.client_request()
        code = self._grant()
        self.assertEqual(MobileAuthorizationGrant.objects.count(), 1)
        grant = MobileAuthorizationGrant.objects.get()
        self.assertNotEqual(grant.code_hash, code)
        self.assertEqual(grant.customer, self.market_customer)
        wrong = self._exchange(code, code_verifier="W" * 43)
        self.assertEqual(wrong.status_code, 400)
        self.assertFalse(MobileAuthorizationGrant.objects.get().consumed_at)
        issued = self._exchange(code)
        self.assertEqual(issued.status_code, 200, issued.content[:200])
        tokens = issued.json()
        self.assertEqual(tokens["channel"], "customer")
        self.assertEqual(len(tokens["access_token"]), 43)
        self.assertNotIn(tokens["access_token"], str(MobileDeviceSession.objects.values().first()))
        data = self._me(tokens["access_token"])
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data.json()["id"], self.market_customer.pk)
        self.assertEqual(data["Access-Control-Allow-Origin"], "https://localhost")
        self.assertIn("no-store", data["Cache-Control"])
        self.assertEqual(self._exchange(code).status_code, 400)
        return tokens

    def client_request(self):
        # Persist the exact authentication stamps issued by KOFAD's customer
        # sign-in service in Django's real integration-test browser session.
        session = self.client.session
        session["market_customer_id"] = self.market_customer.pk
        session["market_credential_stamp"] = services.customer_credential_stamp(self.market_customer)
        session["market_session_expires_at"] = (timezone.now().timestamp() + 7200)
        session.save()


    def test_refresh_rotation_replay_rejection_and_device_revocation(self):
        self.client_request()
        credentials = self._exchange(self._grant()).json()
        refresh_body = {
            "grant_type": "refresh_token", "client_id": "kofad-market",
            "refresh_token": credentials["refresh_token"],
        }
        refreshed = self._token(refresh_body)
        self.assertEqual(refreshed.status_code, 200)
        newer = refreshed.json()
        self.assertNotEqual(newer["access_token"], credentials["access_token"])
        self.assertNotEqual(newer["refresh_token"], credentials["refresh_token"])
        self.assertEqual(self._token(refresh_body).status_code, 400)
        self.assertEqual(self._me(credentials["access_token"]).status_code, 401)
        self.assertEqual(self._me(newer["access_token"]).status_code, 200)
        revoke = self.client.post(
            self.customer_base + "revoke/", HTTP_HOST="market.kofadimpex.com",
            HTTP_AUTHORIZATION="Bearer " + newer["access_token"],
            HTTP_ORIGIN="https://localhost", secure=True,
        )
        self.assertEqual(revoke.status_code, 200)
        self.assertEqual(self._me(newer["access_token"]).status_code, 401)
        self.assertEqual(self._token({
            "grant_type": "refresh_token", "client_id": "kofad-market",
            "refresh_token": newer["refresh_token"],
        }).status_code, 400)

    def test_password_change_revokes_customer_mobile_access(self):
        self.client_request()
        credentials = self._exchange(self._grant()).json()
        self.market_customer.set_password("another-long-password")
        self.market_customer.save(update_fields=["password_hash"])
        self.assertEqual(self._me(credentials["access_token"]).status_code, 401)

    def test_native_redirect_pkce_and_untrusted_origins_are_rejected(self):
        self.client_request()
        path = self.customer_base + "authorize/"
        invalid = self.client.get(path, self._params(redirect_uri="https://evil.example/callback"))
        self.assertEqual(invalid.status_code, 400)
        invalid = self.client.get(path, self._params(code_challenge="bad"))
        self.assertEqual(invalid.status_code, 400)
        code = self._grant()
        evil_origin = self._token({
            "grant_type": "authorization_code", "client_id": "kofad-market",
            "code": code, "code_verifier": self.verifier,
            "redirect_uri": "kofadmarket://auth/callback",
        }, origin="https://evil.example")
        self.assertEqual(evil_origin.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", evil_origin)
        self.assertEqual(self._exchange(code).status_code, 200)

    def test_staff_identity_requires_active_role_mfa_scope_and_version(self):
        self.authenticate_client()
        code = self._grant("staff")
        credentials = self._exchange(code, "staff").json()
        staff = self._me(credentials["access_token"], "staff")
        self.assertEqual(staff.status_code, 200, staff.content[:120])
        self.assertEqual(staff.json()["branch"]["id"], self.branch.pk)
        self.assertTrue(staff.json()["permissions"]["operate_sales"])
        self.user.access.session_version += 1
        self.user.access.save(update_fields=["session_version"])
        self.assertEqual(self._me(credentials["access_token"], "staff").status_code, 401)

    def test_staff_auth_requires_mfa_when_enforced(self):
        with override_settings(PRIVILEGED_MFA_ENFORCED=True):
            self.authenticate_client()
            path = self.staff_base + "authorize/"
            reply = self.client.get(path, self._params("staff"), HTTP_HOST="staff.kofadimpex.com", secure=True)
            self.assertEqual(reply.status_code, 302)
            session = self.client.session
            session["mfa_verified_at"] = timezone.now().timestamp()
            session.save()
            code = self._grant("staff")
            tokens = self._exchange(code, "staff").json()
            self.assertEqual(self._me(tokens["access_token"], "staff").status_code, 200)
