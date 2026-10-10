"""No duplicate transactional receipt, OTP or order email after ambiguous delivery."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from marketplace.models import EmailNotice
from core.email_identity import deliver_pending
from core.brevo_email import UncertainEmailDelivery


@override_settings(
    KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_NOTIFICATIONS_ENABLED=True,
    KOFAD_EMAIL_PROVIDER="brevo",
    KOFAD_BREVO_API_KEY="test-key",
    KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
    KOFAD_BREVO_SECURITY_FROM_EMAIL="transactions@kofadimpex.com",
)
class UncertainTransactionalNoticeTests(TestCase):
    def notice(self, *, status="queued", reference="receipt-one"):
        return EmailNotice.objects.create(
            email="recipient@example.net", subject="KOFAD payment receipt",
            body="Your paid receipt", event_key=reference, status=status,
            attempts=1, next_attempt_at=timezone.now() - timedelta(minutes=25),
        )

    @patch("core.email_identity._send_kofad_mail")
    def test_abandoned_provider_request_quarantined_without_retry(self, send):
        row = self.notice(status="sending")
        self.assertEqual(deliver_pending(limit=5), 0)
        row.refresh_from_db()
        self.assertEqual(row.status, "uncertain")
        send.assert_not_called()
        self.assertEqual(deliver_pending(limit=5), 0)

    @patch("core.email_identity._send_kofad_mail", side_effect=UncertainEmailDelivery())
    def test_timeout_after_provider_submission_never_sends_twice(self, send):
        row = self.notice()
        self.assertEqual(deliver_pending(limit=5), 0)
        row.refresh_from_db()
        self.assertEqual(row.status, "uncertain")
        self.assertEqual(row.attempts, 2)
        self.assertEqual(deliver_pending(limit=5), 0)
        self.assertEqual(send.call_count, 1)

    @patch("core.email_identity._send_kofad_mail", return_value=1)
    def test_successful_delivery_remains_unchanged(self, send):
        row = self.notice()
        self.assertEqual(deliver_pending(limit=5), 1)
        row.refresh_from_db()
        self.assertEqual(row.status, "sent")
        self.assertIsNotNone(row.sent_at)
        send.assert_called_once()
