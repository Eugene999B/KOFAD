"""Safe provider-check regression tests: never send a real request or paid event."""
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core import integration_live, integration_health
from core.test_integration_health import ConnectionsCenterTests


class IntegrationProviderChecksTests(ConnectionsCenterTests):
    def test_live_verification_requires_post_authorization_and_csrf(self):
        detail = reverse("connections_detail", args=["brevo"])
        verify = reverse("connections_live_verify", args=["brevo"])
        with patch.object(integration_live, "verify") as call:
            self.assertEqual(self.client.get(detail).status_code, 200)
            self.assertEqual(self.client.get(verify).status_code, 405)
            self.assertEqual(self.client.post(reverse("connections_live_verify", args=["hubtel"])).status_code, 400)
            call.assert_not_called()

        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.staff)
        self.assertEqual(secure.post(verify).status_code, 403)

    def test_live_result_is_scoped_to_session_and_throttled(self):
        checked = timezone.now().isoformat()
        result = {
            "state": "verified", "summary": "Brevo account authenticated without a message.",
            "checks": [{"name": "Provider API", "state": "passed", "detail": "Verified"}],
            "checked_at": checked, "scope": "Read-only Brevo API"
        }
        with override_settings(
            KOFAD_BREVO_API_KEY="test-key",
            KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
            KOFAD_BREVO_SECURITY_FROM_EMAIL="security@kofadimpex.com",
        ):
            with patch.object(integration_live, "verify", return_value=result) as call:
                url = reverse("connections_live_verify", args=["brevo"])
                self.assertEqual(self.client.post(url).status_code, 302)
                self.assertEqual(self.client.post(url).status_code, 302)
                call.assert_called_once_with("brevo")
                detail = self.client.get(reverse("connections_detail", args=["brevo"]))
                self.assertEqual(detail.status_code, 200)
                self.assertContains(detail, "Brevo account authenticated without a message.")
                self.assertEqual(detail.context["item"]["status"], "working")
                self.assertIn("no-store", detail["Cache-Control"])

    def test_expired_session_probes_do_not_falsely_show_green(self):
        old = (timezone.now() - timedelta(hours=1)).isoformat()
        session = self.client.session
        session["kofad_integration_live_checks"] = {
            "brevo": {"state": "verified", "checked_at": old, "summary": "old"}
        }
        session.save()
        self.assertEqual(integration_live.recent_probes(session), {})

    @override_settings(
        KOFAD_BREVO_API_KEY="do-not-print-this-test-key",
        KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
        KOFAD_BREVO_SECURITY_FROM_EMAIL="security@kofadimpex.com",
    )
    def test_brevo_live_confirm_sender_without_mail(self):
        account = {"plan": [{"creditsType": "sendLimit", "credits": 300}]}
        senders = {"senders": [
            {"email": "transactions@kofadimpex.com", "active": True},
            {"email": "security@kofadimpex.com", "active": True},
        ]}
        with patch.object(integration_live, "_get", side_effect=[(account, None), (senders, None)]) as getter:
            evidence = integration_live.verify("brevo")
        self.assertEqual(evidence["state"], "verified")
        self.assertEqual(getter.call_count, 2)
        self.assertNotIn("do-not-print-this-test-key", str(evidence))
        self.assertNotIn("security@kofadimpex.com", str(evidence))

    @override_settings(KOFAD_BREVO_API_KEY="test-api-key", KOFAD_BREVO_WEBHOOK_TOKEN="")
    def test_brevo_reporting_independent_of_missing_webhook(self):
        with patch.object(integration_live, "_get", return_value=(
            {"events": [{"event": "delivered", "email": "private.customer@example.com"},
                        {"event": "hard_bounce", "email": "private.customer2@example.com"}]},
            None,
        )):
            evidence = integration_live.verify("brevo_reporting")
        self.assertEqual(evidence["state"], "verified")
        self.assertNotIn("private.customer", str(evidence))
        webhook = integration_live.verify("brevo_webhook")
        self.assertEqual(webhook["state"], "failed")
        self.assertIn("cannot authenticate", webhook["summary"])

    @override_settings(KOFAD_CLOUDFLARE_READ_TOKEN="", KOFAD_CLOUDFLARE_ZONE_ID="5459e449118218037a186d47a9fe7fd7")
    def test_public_cloudflare_mx_only_is_partial_not_routing_verified(self):
        mx = {"Status": 0, "Answer": [
            {"type": 15, "data": "10 route1.mx.cloudflare.net."},
            {"type": 15, "data": "20 route2.mx.cloudflare.net."},
        ]}
        with patch.object(integration_live, "_get", return_value=(mx, None)) as getter:
            evidence = integration_live.verify("cloudflare")
        self.assertEqual(evidence["state"], "partial")
        self.assertEqual(getter.call_count, 1)

    @override_settings(KOFAD_CLOUDFLARE_READ_TOKEN="read-only-token-that-does-not-exist", KOFAD_CLOUDFLARE_ZONE_ID="5459e449118218037a186d47a9fe7fd7", KOFAD_EMAIL_CENTER_ENABLED=True)
    def test_authenticated_cloudflare_rules_verify_inbound_without_exposing_address(self):
        mx = {"Status": 0, "Answer": [
            {"type": 15, "data": "10 route1.mx.cloudflare.net."},
            {"type": 15, "data": "20 route2.mx.cloudflare.net."},
        ]}
        settings = {"success": True, "result": {"enabled": True, "status": "ready"}}
        rules = {"success": True, "result": [
            {"enabled": True, "actions": [{"type": "worker", "value": ["kofad-inbound-gateway"]}],
             "matchers": [{"field": "to", "value": "private.inbox@kofadimpex.com"}]},
            {"enabled": True, "actions": [{"type": "forward", "value": ["secret@demo.invalid"]}]},
        ]}
        with patch.object(integration_live, "_get", side_effect=[(mx, None), (settings, None), (rules, None)]):
            evidence = integration_live.verify("inbound_email")
        self.assertEqual(evidence["state"], "verified")
        self.assertNotIn("private.inbox", str(evidence))
        self.assertNotIn("secret@demo.invalid", str(evidence))

    @override_settings(PAYSTACK_SECRET_KEY="test-secret")
    def test_paystack_authentication_does_not_expose_financial_balance(self):
        with patch.object(integration_live, "_get", return_value=(
            {"status": True, "data": [{"balance": 13371337, "currency": "GHS"}]}, None,
        )) as get:
            evidence = integration_live.verify("paystack")
        self.assertEqual(evidence["state"], "verified")
        self.assertNotIn("13371337", str(evidence))
        self.assertEqual(get.call_args.args[0], "https://api.paystack.co/balance")

    def test_errors_are_generic_and_not_provider_payloads(self):
        fake = Mock(status_code=401)
        fake.text = "leaked token and customer email!"
        with patch.object(integration_live.requests, "get", return_value=fake):
            payload, code = integration_live._get("https://api.brevo.com/v3/account")
        self.assertIsNone(payload)
        self.assertEqual(code, "unauthorized")
        self.assertNotIn("leaked", integration_live.ERROR_REASON[code])
