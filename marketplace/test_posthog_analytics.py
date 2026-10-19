"""Consent, privacy and failure contracts for PostHog's public-page counter."""
import json
import time
from unittest.mock import patch
from urllib.parse import quote

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

TOKEN = "phc_" + "A" * 32
URL = "/market/analytics/page-view/"


@override_settings(
    ALLOWED_HOSTS=["testserver", "kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com"],
    KOFAD_POSTHOG_ENABLED=True,
    KOFAD_POSTHOG_PROJECT_TOKEN=TOKEN,
    KOFAD_GA4_MEASUREMENT_ID="",
)
class PostHogConsentTests(TestCase):
    def setUp(self):
        cache.clear()

    def consent(self, *, version=2, enabled=True, age_ms=0):
        payload = {
            "version": version, "analytics": enabled, "preferences": False,
            "savedAt": int(time.time() * 1000) - age_ms,
        }
        self.client.cookies["__Host-kofad_consent"] = quote(json.dumps(payload))

    def post(self, data=None, *, host="kofadimpex.com"):
        return self.client.post(
            URL, json.dumps(data if data is not None else {"path": "/"}),
            content_type="application/json", HTTP_HOST=host, secure=True,
        )

    @patch("marketplace.posthog_analytics.requests.post")
    def test_consent_sends_only_public_page_and_no_customer_identifiers(self, provider):
        provider.return_value.status_code = 200
        self.consent()
        response = self.post()
        self.assertEqual(response.status_code, 204)
        provider.assert_called_once()
        args, kwargs = provider.call_args
        self.assertEqual(args, ("https://eu.i.posthog.com/i/v0/e/",))
        self.assertEqual(kwargs["timeout"], (2, 3))
        payload = kwargs["json"]
        self.assertEqual(payload["api_key"], TOKEN)
        self.assertEqual(payload["event"], "kofad public page viewed")
        self.assertTrue(payload["distinct_id"].startswith("one_time_"))
        self.assertEqual(payload["properties"], {
            "public_page": "/", "$process_person_profile": False, "$geoip_disable": True,
        })
        self.assertNotIn("kofad_consent", str(payload))
        for forbidden in ("email", "phone", "url", "referrer", "order", "ip_address", "session"):
            self.assertNotIn(forbidden, str(payload).lower())
        self.assertNotIn(TOKEN, response.content.decode())

    @patch("marketplace.posthog_analytics.requests.post")
    def test_no_opt_in_no_event_and_existing_v1_consent_requires_reconsent(self, provider):
        self.assertEqual(self.post().status_code, 204)
        self.consent(version=1)
        self.assertEqual(self.post().status_code, 204)
        self.consent(enabled=False)
        self.assertEqual(self.post().status_code, 204)
        self.consent(age_ms=181 * 86400000)
        self.assertEqual(self.post().status_code, 204)
        provider.assert_not_called()

    @patch("marketplace.posthog_analytics.requests.post")
    def test_invalid_paths_hosts_and_malformed_input_do_not_reach_provider(self, provider):
        self.consent()
        for path in ("/market/account/", "/market/orders/private/", "/market/?q=private"):
            with self.subTest(path=path):
                self.assertEqual(self.post({"path": path}).status_code, 400)
        for path in ([], {}, 1, None):
            with self.subTest(path=path):
                self.assertEqual(self.post({"path": path}).status_code, 400)
        self.assertEqual(self.post({"path": "/about/"}, host="market.kofadimpex.com").status_code, 400)
        self.assertEqual(self.post(host="staff.kofadimpex.com").status_code, 404)
        provider.assert_not_called()

    @patch("marketplace.posthog_analytics.requests.post")
    def test_disabled_or_bad_token_is_not_enabled_by_a_browser_choice(self, provider):
        self.consent()
        with override_settings(KOFAD_POSTHOG_ENABLED=False):
            self.assertEqual(self.post().status_code, 404)
        with override_settings(KOFAD_POSTHOG_PROJECT_TOKEN="invalid"):
            self.assertEqual(self.post().status_code, 404)
        provider.assert_not_called()

    @patch("marketplace.posthog_analytics.requests.post")
    def test_provider_outage_and_quota_fail_closed_without_site_breakage(self, provider):
        self.consent()
        with patch("marketplace.posthog_analytics._reserve_daily_slot", return_value=False):
            self.assertEqual(self.post().status_code, 429)
        provider.assert_not_called()
        provider.return_value.status_code = 429
        self.assertEqual(self.post().status_code, 503)

    def test_csrf_is_required_even_with_analytics_consent(self):
        browser = Client(enforce_csrf_checks=True)
        browser.cookies["__Host-kofad_consent"] = quote(json.dumps({
            "version": 2, "analytics": True, "preferences": False,
            "savedAt": int(time.time() * 1000),
        }))
        response = browser.post(
            URL, '{"path": "/"}', content_type="application/json",
            HTTP_HOST="kofadimpex.com", secure=True,
        )
        self.assertEqual(response.status_code, 403)

    def test_only_public_pages_show_posthog_consent_and_privacy_disclosure(self):
        public = self.client.get("/", HTTP_HOST="kofadimpex.com", secure=True)
        self.assertEqual(public.status_code, 200)
        self.assertContains(public, 'data-posthog-enabled="1"')
        self.assertContains(public, 'posthog-consent.')
        self.assertNotContains(public, TOKEN)
        staff = self.client.get("/", HTTP_HOST="staff.kofadimpex.com", secure=True)
        self.assertNotContains(staff, 'posthog-consent.', status_code=302)
        privacy = self.client.get("/privacy/", HTTP_HOST="kofadimpex.com", secure=True)
        self.assertContains(privacy, "PostHog")
        with override_settings(KOFAD_POSTHOG_ENABLED=False):
            disabled = self.client.get("/", HTTP_HOST="kofadimpex.com", secure=True)
        self.assertNotContains(disabled, 'posthog-consent.')
