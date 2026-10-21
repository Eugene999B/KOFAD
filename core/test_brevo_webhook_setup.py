"""Offline integration tests: no real provider calls, credentials, or email sends."""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .brevo_webhook_setup import CALLBACK, EVENTS, ensure_delivery_webhook

OPTS = {
    "KOFAD_BREVO_WEBHOOK_AUTO_REGISTER": True,
    "KOFAD_EMAIL_PROVIDER": "brevo",
    "KOFAD_BREVO_API_KEY": "offline-test-api-key-123456",
    "KOFAD_BREVO_WEBHOOK_TOKEN": "offline-test-bearer-token-is-strong-and-long-enough",
}


def response(status, content=None):
    return Mock(status_code=status, json=Mock(return_value=content or {}))


@override_settings(**OPTS)
class BrevoWebhookSetupTests(SimpleTestCase):
    @patch("core.brevo_webhook_setup.requests.post")
    @patch("core.brevo_webhook_setup.requests.get")
    def test_register_once_with_authenticated_email_events(self, get, post):
        get.return_value = response(200, {"webhooks": []})
        post.return_value = response(201, {"id": 1234})
        self.assertEqual(ensure_delivery_webhook(), "created")
        get.assert_called_once()
        post.assert_called_once()
        args = post.call_args.kwargs
        self.assertEqual(args["json"]["url"], CALLBACK)
        self.assertEqual(args["json"]["type"], "transactional")
        self.assertEqual(args["json"]["channel"], "email")
        self.assertEqual(args["json"]["auth"], {
            "type": "bearer", "token": OPTS["KOFAD_BREVO_WEBHOOK_TOKEN"]
        })
        self.assertEqual(set(args["json"]["events"]), EVENTS)
        self.assertFalse(args["json"]["batched"])
        self.assertFalse(args["allow_redirects"])

    @patch("core.brevo_webhook_setup.requests.post")
    @patch("core.brevo_webhook_setup.requests.get")
    def test_existing_correct_webhook_is_never_duplicated(self, get, post):
        get.return_value = response(200, {"webhooks": [{
            "url": CALLBACK, "events": list(EVENTS),
            "auth": {"type": "bearer", "token": OPTS["KOFAD_BREVO_WEBHOOK_TOKEN"]},
        }]})
        self.assertEqual(ensure_delivery_webhook(), "ready")
        post.assert_not_called()

    @patch("core.brevo_webhook_setup.requests.post")
    @patch("core.brevo_webhook_setup.requests.get")
    def test_existing_wrong_auth_never_silently_replaced(self, get, post):
        get.return_value = response(200, {"webhooks": [{
            "url": CALLBACK, "events": list(EVENTS),
            "auth": {"type": "bearer", "token": "not-the-same-token"},
        }]})
        self.assertEqual(ensure_delivery_webhook(), "requires_review")
        post.assert_not_called()

    @patch("core.brevo_webhook_setup.requests.post")
    @patch("core.brevo_webhook_setup.requests.get")
    def test_provider_error_does_not_create_unchecked_duplicate(self, get, post):
        get.return_value = response(503)
        self.assertEqual(ensure_delivery_webhook(), "unavailable")
        post.assert_not_called()

    @patch("core.brevo_webhook_setup.requests.post")
    @patch("core.brevo_webhook_setup.requests.get")
    def test_provider_timeout_does_not_send_retries(self, get, post):
        import requests
        get.side_effect = requests.Timeout()
        self.assertEqual(ensure_delivery_webhook(), "unavailable")
        post.assert_not_called()

    @patch("core.brevo_webhook_setup.requests.post")
    @patch("core.brevo_webhook_setup.requests.get")
    @override_settings(KOFAD_BREVO_WEBHOOK_TOKEN="")
    def test_missing_bearer_token_fails_closed(self, get, post):
        self.assertEqual(ensure_delivery_webhook(), "disabled")
        get.assert_not_called()
        post.assert_not_called()
