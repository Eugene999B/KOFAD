"""Brevo event authentication, replay protection, status ordering and BCC privacy."""
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.email_center import deliver_outgoing
from core.email_models import EmailDeliveryEvent, EmailLetter, EmailMailbox

TOKEN = "only-for-tests-brevo-event-token-long-enough-123456789"


@override_settings(
    KOFAD_BREVO_WEBHOOK_TOKEN=TOKEN,
    KOFAD_EMAIL_CENTER_ENABLED=True, KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_PROVIDER="brevo",
    KOFAD_BREVO_API_KEY="NOT-A-REAL-KEY",
    KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
    KOFAD_BREVO_SECURITY_FROM_EMAIL="transactions@kofadimpex.com",
    KOFAD_BREVO_REGISTERED_SENDERS="transactions@kofadimpex.com",
)
class BrevoDeliveryEventsTests(TestCase):
    def setUp(self):
        self.mailbox = EmailMailbox.objects.get(address="support@kofadimpex.com")
        self.message = EmailLetter.objects.create(
            mailbox=self.mailbox, direction="outbound", status="submitted",
            from_address=self.mailbox.address, to_address="customer@example.org",
            cc_addresses="manager@example.org", bcc_addresses="secret@example.org",
            subject="Order update", body_text="Package dispatched.",
            message_id="<brevo-100@example.com>",
            submitted_at=timezone.now(),
        )

    def payload(self, *, event="delivered", recipient="customer@example.org",
                message_id="<brevo-100@example.com>", ts=None, tags=None):
        data = {
            "event": event, "email": recipient, "message-id": message_id,
            "ts_event": int(ts or timezone.now().timestamp()),
        }
        if tags is not None:
            data["tags"] = tags
        return data

    def event(self, data, *, bearer=TOKEN):
        return self.client.post(
            reverse("email_brevo_events"), data=json.dumps(data),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer " + bearer,
        )

    def test_rejects_unauthenticated_requests_and_disabled_endpoint(self):
        event = self.payload()
        self.assertEqual(self.client.post(
            reverse("email_brevo_events"), json.dumps(event),
            content_type="application/json",
        ).status_code, 403)
        self.assertEqual(self.event(event, bearer="wrong").status_code, 403)
        with override_settings(KOFAD_BREVO_WEBHOOK_TOKEN=""):
            self.assertEqual(self.event(event).status_code, 503)
        self.assertEqual(EmailDeliveryEvent.objects.count(), 0)

    def test_confirmed_delivery_is_distinct_from_provider_submission(self):
        r = self.event(self.payload())
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["tracked"])
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, "submitted")
        self.assertEqual(self.message.delivery_status, "delivered")
        self.assertIsNotNone(self.message.delivery_updated_at)
        self.assertEqual(EmailDeliveryEvent.objects.count(), 1)
        self.assertEqual(self.event(self.payload(ts=self.message.delivery_updated_at.timestamp())).status_code, 200)
        self.assertEqual(EmailDeliveryEvent.objects.count(), 1)

    def test_cc_bcc_event_does_not_prove_primary_customer_received(self):
        self.assertEqual(
            self.event(self.payload(recipient="secret@example.org")).status_code, 200
        )
        self.message.refresh_from_db()
        self.assertEqual(self.message.delivery_status, "unknown")
        self.assertEqual(EmailDeliveryEvent.objects.count(), 1)
        self.assertEqual(self.event(self.payload(
            recipient="stranger@example.org"
        )).json()["tracked"], False)
        self.assertEqual(EmailDeliveryEvent.objects.count(), 1)

    def test_out_of_order_event_never_erases_newer_delivery_confirmation(self):
        now = int(timezone.now().timestamp())
        self.event(self.payload(event="delivered", ts=now))
        self.event(self.payload(event="deferred", ts=now - 500))
        self.message.refresh_from_db()
        self.assertEqual(self.message.delivery_status, "delivered")
        self.event(self.payload(event="request", ts=now + 1))
        self.message.refresh_from_db()
        self.assertEqual(self.message.delivery_status, "delivered")

    def test_provider_id_mismatch_not_associated_even_with_letter_tag(self):
        result = self.event(self.payload(
            message_id="<wrong-id@example.com>",
            tags=[f"kofad-letter-{self.message.pk}"]
        ))
        self.assertFalse(result.json()["tracked"])
        self.message.refresh_from_db()
        self.assertEqual(self.message.delivery_status, "unknown")

    @patch("core.brevo_email.requests.post")
    def test_sending_request_carries_opaque_reconciliation_tag(self, post):
        queued = EmailLetter.objects.create(
            mailbox=self.mailbox, direction="outbound", status="queued",
            from_address=self.mailbox.address, to_address="another@example.org",
            subject="Receipt", body_text="Acknowledged",
            next_attempt_at=timezone.now() - timedelta(minutes=1)
        )
        post.return_value = Mock(status_code=201)
        post.return_value.json.return_value = {"messageId": "<fresh@brevo.test>"}
        self.assertEqual(deliver_outgoing(limit=2), 1)
        self.assertEqual(
            post.call_args.kwargs["json"]["tags"],
            [f"kofad-letter-{queued.pk}"]
        )
        result = self.event(self.payload(
            recipient="another@example.org",
            message_id="<fresh@brevo.test>",
            tags=[f"kofad-letter-{queued.pk}"]
        ))
        self.assertTrue(result.json()["tracked"])
        queued.refresh_from_db()
        self.assertEqual(queued.delivery_status, "delivered")

    def test_unrecognised_activity_and_bad_timestamp_do_not_create_record(self):
        self.assertFalse(self.event(self.payload(event="opened")).json()["tracked"])
        bad_type = self.payload()
        bad_type["event"] = ["delivered"]
        self.assertFalse(self.event(bad_type).json()["tracked"])
        bad = self.payload()
        bad["ts_event"] = "invalid"
        self.assertEqual(self.event(bad).status_code, 400)
        self.assertEqual(EmailDeliveryEvent.objects.count(), 0)
