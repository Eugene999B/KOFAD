import hashlib
import hmac
import json
import uuid

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from .models import Branch, Message, Party, WhatsAppAttempt, WhatsAppWebhookEvent


VERIFY = "kofad-test-verify-token"
SECRET = "kofad-test-app-secret"


def signed(body):
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


@override_settings(
    WHATSAPP_WEBHOOK_VERIFY_TOKEN=VERIFY,
    WHATSAPP_APP_SECRET=SECRET,
    WHATSAPP_WEBHOOK_MAX_BYTES=524288,
)
class WhatsAppWebhookTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Main", code="main")
        self.user = User.objects.create_user("wa-test-user", password="test-password-long-enough")
        self.party = Party.objects.create(
            branch=self.branch, kind="customer", name="WhatsApp Customer",
            phone="+233241234567", consent=True,
        )

    def post_payload(self, payload, signature=None):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return self.client.post(
            "/whatsapp/webhook/",
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature or signed(raw),
        )

    def test_meta_verification_challenge_requires_matching_token(self):
        ok = self.client.get("/whatsapp/webhook/", {
            "hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "987654",
        })
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.content, b"987654")
        denied = self.client.get("/whatsapp/webhook/", {
            "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "987654",
        })
        self.assertEqual(denied.status_code, 403)

    def test_webhook_rejects_bad_signature(self):
        response = self.post_payload(
            {"object": "whatsapp_business_account", "entry": []},
            signature="sha256=" + ("0" * 64),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(WhatsAppWebhookEvent.objects.count(), 0)

    def test_incoming_message_is_deduplicated(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "WABA-1",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "PHONE-1"},
                        "messages": [{
                            "from": "233241234567", "id": "wamid.inbound.1",
                            "timestamp": "1791060000", "type": "text",
                            "text": {"body": "Please send my receipt"},
                        }],
                    },
                }],
            }],
        }
        first = self.post_payload(payload)
        second = self.post_payload(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        event = WhatsAppWebhookEvent.objects.get()
        self.assertEqual(event.event_type, "message")
        self.assertEqual(event.provider_message_id, "wamid.inbound.1")
        self.assertEqual(event.wa_id, "233241234567")

    def test_delivery_status_reconciles_whatsapp_message(self):
        message = Message.objects.create(
            branch=self.branch, party=self.party, channel="whatsapp",
            body="Your KOFAD receipt is ready.", status="accepted",
            created_by=self.user, recipient="+233241234567",
        )
        attempt = WhatsAppAttempt.objects.create(
            message=message, number=1, provider_id="wamid.outbound.1", status="accepted",
        )
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "WABA-1",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "PHONE-1"},
                        "statuses": [{
                            "id": "wamid.outbound.1", "status": "read",
                            "timestamp": "1791060001", "recipient_id": "233241234567",
                        }],
                    },
                }],
            }],
        }
        response = self.post_payload(payload)
        self.assertEqual(response.status_code, 200)
        message.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(message.status, "read")
        self.assertEqual(attempt.status, "read")
        self.assertEqual(WhatsAppWebhookEvent.objects.get().status, "read")
