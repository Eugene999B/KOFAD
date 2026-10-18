"""Invisible Turnstile login boundary regression tests."""
import io
import json
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core.turnstile import verify_turnstile


@override_settings(
    KOFAD_TURNSTILE_ENABLED=True,
    KOFAD_TURNSTILE_MARKET_SITE_KEY="test-market-site",
    KOFAD_TURNSTILE_MARKET_SECRET_KEY="test-market-secret",
    KOFAD_TURNSTILE_STAFF_SITE_KEY="test-staff-site",
    KOFAD_TURNSTILE_STAFF_SECRET_KEY="test-staff-secret",
)
class TurnstileSiteverifyTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def request(self, token="valid-token"):
        return self.factory.post("/market/account/login/", {
            "cf-turnstile-response": token,
        })

    @patch("core.turnstile.urlopen")
    def test_valid_market_token_is_accepted_for_exact_hostname_and_action(self, urlopen):
        urlopen.return_value = io.BytesIO(json.dumps({
            "success": True,
            "hostname": "market.kofadimpex.com",
            "action": "market_login",
        }).encode())
        self.assertTrue(verify_turnstile(self.request(), kind="market", action="market_login"))
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 5)

    @patch("core.turnstile.urlopen")
    def test_missing_and_oversized_tokens_are_rejected_without_external_call(self, urlopen):
        self.assertFalse(verify_turnstile(self.request(""), kind="market", action="market_login"))
        self.assertFalse(verify_turnstile(self.request("x" * 2049), kind="market", action="market_login"))
        urlopen.assert_not_called()

    @patch("core.turnstile.urlopen")
    def test_wrong_hostname_or_action_is_rejected(self, urlopen):
        for hostname, action in [
            ("staff.kofadimpex.com", "market_login"),
            ("market.kofadimpex.com", "staff_login"),
        ]:
            urlopen.return_value = io.BytesIO(json.dumps({
                "success": True,
                "hostname": hostname,
                "action": action,
            }).encode())
            self.assertFalse(verify_turnstile(self.request(), kind="market", action="market_login"))

    @patch("core.turnstile.urlopen")
    def test_cloudflare_failure_is_fail_closed(self, urlopen):
        urlopen.side_effect = TimeoutError("test simulated timeout")
        self.assertFalse(verify_turnstile(self.request(), kind="market", action="market_login"))

    @override_settings(KOFAD_TURNSTILE_STAFF_SECRET_KEY="")
    def test_missing_config_does_not_disable_enforcement(self):
        self.assertFalse(verify_turnstile(self.request(), kind="staff", action="staff_login"))

    @override_settings(KOFAD_TURNSTILE_ENABLED=False)
    def test_disabled_toggle_preserves_login_for_safe_rollout(self):
        self.assertTrue(verify_turnstile(self.request(""), kind="market", action="market_login"))


@override_settings(
    ALLOWED_HOSTS=["testserver", "market.kofadimpex.com", "staff.kofadimpex.com"],
    KOFAD_TURNSTILE_ENABLED=True,
    KOFAD_TURNSTILE_MARKET_SITE_KEY="test-market-site",
    KOFAD_TURNSTILE_MARKET_SECRET_KEY="test-market-secret",
    KOFAD_TURNSTILE_STAFF_SITE_KEY="test-staff-site",
    KOFAD_TURNSTILE_STAFF_SECRET_KEY="test-staff-secret",
)
class TurnstileLoginIntegrationTests(TestCase):
    def test_staff_login_blocks_missing_token_before_authentication(self):
        with patch("core.views.authenticate") as authenticate:
            response = self.client.post(
                "/login/", {"username": "testing", "password": "not-a-secret"},
                secure=True, HTTP_HOST="staff.kofadimpex.com",
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Verification could not be completed")
        authenticate.assert_not_called()

    def test_market_password_login_blocks_missing_token(self):
        response = self.client.post(
            "/market/account/login/",
            {"phone": "0550000000", "password": "not-a-secret"},
            secure=True, HTTP_HOST="market.kofadimpex.com",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Verification could not be completed")

    def test_market_mobile_access_blocks_missing_token_before_sending_otp(self):
        with patch("marketplace.services.send_otp") as send_otp:
            response = self.client.post(
                "/market/access/", {"phone": "0550000000"},
                secure=True, HTTP_HOST="market.kofadimpex.com",
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Verification could not be completed")
        send_otp.assert_not_called()

    def test_login_csp_allows_turnstile_only_on_protected_forms(self):
        for host, path in [
            ("staff.kofadimpex.com", "/login/"),
            ("market.kofadimpex.com", "/market/account/login/"),
            ("market.kofadimpex.com", "/market/access/"),
        ]:
            response = self.client.get(path, secure=True, HTTP_HOST=host)
            self.assertEqual(response.status_code, 200)
            self.assertIn("https://challenges.cloudflare.com", response["Content-Security-Policy"])
        response = self.client.get("/market/", secure=True, HTTP_HOST="market.kofadimpex.com")
        self.assertNotIn("https://challenges.cloudflare.com", response["Content-Security-Policy"])
