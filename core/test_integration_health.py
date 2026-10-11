"""Private Connections health centre permissions, status semantics and leak checks."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import integration_health
from marketplace.tests import MarketFixtures


class ConnectionsCenterTests(MarketFixtures):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session.save()

    def test_owner_can_open_overview_and_all_catalogue_details(self):
        response = self.client.get(reverse("connections_center"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connections")
        self.assertContains(response, "Cloudflare DNS")
        self.assertContains(response, "Google Apps Script")
        self.assertContains(response, "Hubtel payments")
        self.assertContains(response, "PayPal")
        self.assertContains(response, "Arkesel SMS")
        self.assertContains(response, "No locally verified activity available")
        for item in integration_health.CATALOG:
            with self.subTest(provider=item["id"]):
                detail = self.client.get(reverse("connections_detail", args=[item["id"]]))
                self.assertEqual(detail.status_code, 200)
                self.assertContains(detail, "How this connection works")
                self.assertContains(detail, "Recommended action")

    def test_settings_exposes_connections_link(self):
        response = self.client.get(reverse("settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/settings/connections/")

    def test_owner_can_filter_by_category_and_status(self):
        response = self.client.get(reverse("connections_center"), {
            "group": "payments", "state": "planned",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.context["visible_connections"]], ["paypal"])
        invalid = self.client.get(reverse("connections_center"), {
            "group": "invalid-group", "state": "hijack",
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertFalse(invalid.context["active_group"])
        self.assertFalse(invalid.context["active_state"])

    def test_detail_is_not_guessable_or_open_to_non_admin_staff(self):
        self.assertEqual(self.client.get("/settings/connections/does-not-exist/").status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("connections_center")).status_code, 302)
        unauthorized = User.objects.create_user("ordinary-connections-user", password="test-only-password")
        self.client.force_login(unauthorized)
        self.assertEqual(self.client.get(reverse("connections_center")).status_code, 403)
        self.assertEqual(self.client.get("/settings/connections/brevo/").status_code, 403)

    @override_settings(
        KOFAD_GOOGLE_OAUTH_ENABLED=True,
        KOFAD_GOOGLE_CLIENT_ID="sensitive-testing-client-id-not-to-be-shown",
        KOFAD_GOOGLE_CLIENT_SECRET="sensitive-testing-google-secret-not-to-be-shown",
        KOFAD_BREVO_API_KEY="sensitive-testing-brevo-key-not-to-be-shown",
        KOFAD_BREVO_SECURITY_FROM_EMAIL="security@kofadimpex.com",
        KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
    )
    def test_diagnostics_never_render_secret_values_or_recipient_records(self):
        with patch("core.integration_health._evidence", return_value={}):
            response = self.client.get(reverse("connections_center"))
            detail = self.client.get(reverse("connections_detail", args=["google_login"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        for data in (response.content.decode(), detail.content.decode()):
            self.assertNotIn("sensitive-testing-client-id-not-to-be-shown", data)
            self.assertNotIn("sensitive-testing-google-secret-not-to-be-shown", data)
            self.assertNotIn("sensitive-testing-brevo-key-not-to-be-shown", data)
        self.assertIn("no-store", response["Cache-Control"])

    @override_settings(
        KOFAD_GOOGLE_OAUTH_ENABLED=True,
        KOFAD_GOOGLE_CLIENT_ID="test-client-id",
        KOFAD_GOOGLE_CLIENT_SECRET="test-client-secret",
    )
    def test_keys_only_mean_configured_not_working(self):
        with patch("core.integration_health._evidence", return_value={}):
            item = next(x for x in integration_health.catalog()["items"] if x["id"] == "google_login")
        self.assertEqual(item["status"], "ready")
        self.assertEqual(item["requirements_ok"], 3)
        self.assertIsNone(item["proof"])

    @override_settings(
        KOFAD_GOOGLE_OAUTH_ENABLED=True,
        KOFAD_GOOGLE_CLIENT_ID="test-client-id",
        KOFAD_GOOGLE_CLIENT_SECRET="",
    )
    def test_enabled_provider_missing_credential_is_action_required(self):
        with patch("core.integration_health._evidence", return_value={}):
            item = next(x for x in integration_health.catalog()["items"] if x["id"] == "google_login")
        self.assertEqual(item["status"], "issue")
        self.assertTrue(any("client secret" in x.lower() for x in item["missing"]))

    @override_settings(
        KOFAD_GOOGLE_OAUTH_ENABLED=True,
        KOFAD_GOOGLE_CLIENT_ID="test-client-id",
        KOFAD_GOOGLE_CLIENT_SECRET="test-client-secret",
    )
    def test_recent_success_and_failure_cannot_be_confused(self):
        fresh = timezone.now()
        for outcome, expected in (("success", "working"), ("failed", "issue"), ("uncertain", "warning")):
            with self.subTest(outcome=outcome):
                with patch("core.integration_health._evidence", return_value={
                    "google_login": {"at": fresh, "outcome": outcome, "text": "Historical sign-in event."}
                }):
                    record = next(x for x in integration_health.catalog()["items"] if x["id"] == "google_login")
                self.assertEqual(record["status"], expected)

    def test_post_is_rejected_and_cache_headers_are_private(self):
        response = self.client.post(reverse("connections_center"))
        self.assertEqual(response.status_code, 405)
        page = self.client.get(reverse("connections_center"))
        self.assertIn("no-store", page["Cache-Control"])
        self.assertNotIn("public", page["Cache-Control"])

    def test_diagnostics_do_not_modify_business_or_payment_data(self):
        from marketplace.models import MarketPaymentAttempt, OnlineOrder
        from core.models import SmsAttempt
        before = (MarketPaymentAttempt.objects.count(), SmsAttempt.objects.count(), OnlineOrder.objects.count())
        self.client.get(reverse("connections_center"))
        self.client.get(reverse("connections_detail", args=["paystack"]))
        after = (MarketPaymentAttempt.objects.count(), SmsAttempt.objects.count(), OnlineOrder.objects.count())
        self.assertEqual(before, after)
