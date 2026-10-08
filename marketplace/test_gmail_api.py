import base64
import json
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import override_settings

from core import email_identity, gmail_api
from marketplace.models import EmailNotice, GmailSenderConnection
from .tests import MarketFixtures


GMAIL_TEST = override_settings(
    KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_NOTIFICATIONS_ENABLED=True,
    KOFAD_GMAIL_API_ENABLED=True,
    KOFAD_GMAIL_CLIENT_ID="gmail-api-kofad-test.apps.googleusercontent.com",
    KOFAD_GMAIL_CLIENT_SECRET="dummy-only-for-tests",
)


@GMAIL_TEST
class GmailApiIntegrationTests(MarketFixtures):
    def _login_staff(self):
        self.client.force_login(self.staff)
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session.save()

    def _sender(self):
        return GmailSenderConnection.objects.create(
            pk=1, email="company@gmail.com", google_subject="gmail-sender-google-id",
            connected_by_id=self.staff.pk,
            encrypted_refresh_token=gmail_api._cipher().encrypt(
                b"valid-refresh-token-for-unit-tests"
            ).decode(),
        )

    @patch("core.gmail_api.google_oauth._validated_google_claims")
    @patch("core.gmail_api.requests.post")
    def test_manager_can_authorize_send_only_mailbox_and_revoke(self, post, claims):
        self._login_staff()
        get = self.client.get("/auth/google/gmail/connect/")
        self.assertEqual(get.status_code, 302)
        self.assertNotIn(gmail_api.SESSION_KEY, self.client.session)
        start = self.client.post("/auth/google/gmail/connect/", {
            "current_password": "market-owner-password",
        })
        self.assertEqual(start.status_code, 302)
        query = parse_qs(urlparse(start["Location"]).query)
        self.assertIn(gmail_api.GMAIL_SCOPE, query["scope"][0].split())
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(
            query["redirect_uri"],
            ["https://staff.kofadimpex.com/auth/google/gmail/callback/"],
        )
        pending = self.client.session[gmail_api.SESSION_KEY]
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {
            "id_token": "google-signed-id-token",
            "scope": "openid email " + gmail_api.GMAIL_SCOPE,
            "refresh_token": "refresh-token-actual-provider-example-test",
        }
        claims.return_value = {
            "subject": "gmail-sender-google-id", "email": "company@gmail.com",
        }
        callback = self.client.get("/auth/google/gmail/callback/", {
            "state": pending["state"], "code": "provider-authorisation-code",
        })
        self.assertEqual(callback.status_code, 302)
        sender = GmailSenderConnection.objects.get(pk=1)
        self.assertEqual(sender.email, "company@gmail.com")
        self.assertNotIn("refresh-token-actual", sender.encrypted_refresh_token)
        self.assertTrue(gmail_api.ready())
        self.assertTrue(email_identity.delivery_ready())
        # A replayed callback cannot replace credentials.
        repeat = self.client.get("/auth/google/gmail/callback/", {
            "state": pending["state"], "code": "provider-authorisation-code",
        })
        self.assertEqual(repeat.status_code, 302)
        self.assertEqual(post.call_count, 1)
        disconnect = self.client.post("/auth/google/gmail/disconnect/", {
            "current_password": "market-owner-password",
        })
        self.assertEqual(disconnect.status_code, 302)
        self.assertFalse(GmailSenderConnection.objects.exists())

    @patch("core.gmail_api.requests.post")
    def test_send_mail_uses_https_gmail_api_not_smtp(self, post):
        sender = self._sender()
        post.side_effect = [
            Mock(status_code=200),
            Mock(status_code=200),
        ]
        post.side_effect[0].json.return_value = {
            "access_token": "this-is-an-access-token-valid-for-tests",
        }
        self.assertEqual(gmail_api.send_gmail(
            subject="Receipt ready", body="Thanks for the order",
            recipient="customer@example.com",
        ), 1)
        token_req, email_req = post.call_args_list
        self.assertEqual(token_req.args[0], "https://oauth2.googleapis.com/token")
        self.assertEqual(token_req.kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(email_req.args[0], "https://gmail.googleapis.com/gmail/v1/users/me/messages/send")
        self.assertIn("Bearer ", email_req.kwargs["headers"]["Authorization"])
        payload = email_req.kwargs["json"]["raw"]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        self.assertIn("From: company@gmail.com", decoded)
        self.assertIn("To: customer@example.com", decoded)
        self.assertIn("Subject: Receipt ready", decoded)
        self.assertIn("Thanks for the order", decoded)
        sender.refresh_from_db()
        self.assertIsNotNone(sender.last_send_at)

    @patch("core.gmail_api.send_gmail", return_value=1)
    def test_code_delivery_and_opted_in_outbox_use_gmail_without_smtp(self, deliver):
        self._sender()
        email_identity.request_code("customer", self.customer.pk, "shopper@example.com")
        self.assertEqual(deliver.call_count, 1)
        identity = email_identity.EmailIdentity.objects.get(
            kind="customer", owner_id=self.customer.pk,
        )
        self.assertIsNotNone(identity.expires_at)
        identity.email = "shopper@example.com"
        identity.pending_email = ""
        identity.verified_at = identity.requested_at
        identity.notifications_enabled = True
        identity.save()
        notice = email_identity.enqueue_notice(
            "customer", self.customer.pk, "test-order-1",
            "Order confirmed", "Your payment was received.",
        )
        self.assertIsNotNone(notice)
        self.assertEqual(email_identity.deliver_pending(limit=4), 1)
        notice.refresh_from_db()
        self.assertEqual(notice.status, "sent")
        self.assertEqual(deliver.call_count, 2)

    def test_disabled_sender_cannot_claim_ready_without_google_consent(self):
        self.assertFalse(email_identity.delivery_ready())
        with self.assertRaises(ValidationError):
            gmail_api.send_gmail(
                subject="Test", body="Unsent", recipient="test@example.com",
            )
        with override_settings(KOFAD_GMAIL_API_ENABLED=False):
            self.assertFalse(gmail_api.ready())
            self.assertFalse(gmail_api.configured())

    @patch("core.gmail_api.requests.post")
    def test_provider_errors_do_not_appear_in_user_messages(self, post):
        self._sender()
        post.return_value = Mock(status_code=400)
        post.return_value.raise_for_status.side_effect = ValueError("sensitive provider response")
        with self.assertRaises(ValidationError) as raised:
            gmail_api.send_gmail(
                subject="Code", body="Verification", recipient="test@example.com",
            )
        self.assertNotIn("sensitive provider response", str(raised.exception))
