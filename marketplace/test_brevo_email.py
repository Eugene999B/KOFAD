from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from core import email_identity
from core.brevo_email import send_brevo
from .test_email_identity import VerifiedEmailIntegrationTests
from .tests import MarketFixtures


BREVO = override_settings(
    KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_NOTIFICATIONS_ENABLED=True,
    KOFAD_EMAIL_PROVIDER="brevo",
    KOFAD_BREVO_API_KEY="CI-do-not-send-live",
    KOFAD_BREVO_SECURITY_FROM_EMAIL="security@kofadimpex.com",
    KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
    KOFAD_SUPPORT_REPLY_TO_EMAIL="support@kofadimpex.com",
)


@BREVO
class BrevoEmailProviderTests(MarketFixtures):
    @patch("core.brevo_email.requests.post")
    def test_uses_purpose_specific_business_address_with_private_api_key(self, post):
        post.return_value = Mock(status_code=201)
        self.assertTrue(email_identity.delivery_ready())
        self.assertEqual(email_identity._send_kofad_mail(
            "Verify email", "123456", ["customer@example.com"], purpose="security"
        ), 1)
        first = post.call_args
        self.assertEqual(first.kwargs["json"]["sender"]["email"], "security@kofadimpex.com")
        self.assertEqual(first.kwargs["json"]["replyTo"]["email"], "support@kofadimpex.com")
        self.assertEqual(first.kwargs["headers"]["api-key"], "CI-do-not-send-live")
        self.assertEqual(first.args[0], "https://api.brevo.com/v3/smtp/email")
        self.assertFalse(first.kwargs["allow_redirects"])
        self.assertEqual(email_identity._send_kofad_mail(
            "Payment confirmed", "GHS 1.00", ["customer@example.com"], purpose="transaction"
        ), 1)
        self.assertEqual(post.call_args.kwargs["json"]["sender"]["email"], "transactions@kofadimpex.com")
        self.assertNotIn("CI-do-not-send-live", str(post.call_args.kwargs["json"]))

    @patch("core.brevo_email.requests.post")
    def test_http_failure_does_not_claim_delivery(self, post):
        post.return_value = Mock(status_code=429)
        with self.assertRaises(ValidationError):
            send_brevo(
                subject="Payment", body="test", recipient="buyer@example.com",
                purpose="transaction",
            )
        with override_settings(KOFAD_BREVO_API_KEY=""):
            self.assertFalse(email_identity.delivery_ready())

    @patch("core.brevo_email.requests.post")
    def test_verification_code_uses_security_sender_and_rate_limit(self, post):
        post.return_value = Mock(status_code=201)
        identity = email_identity.request_code("customer", self.customer.pk, "newbuyer@gmail.com")
        self.assertEqual(identity.pending_email, "newbuyer@gmail.com")
        self.assertTrue(identity.code_digest)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["sender"]["email"], "security@kofadimpex.com")
        with self.assertRaises(ValidationError):
            email_identity.request_code("customer", self.customer.pk, "newbuyer@gmail.com")
        self.assertEqual(post.call_count, 1)

    @patch("core.brevo_email.requests.post")
    def test_opt_in_order_notifications_use_transactions_address(self, post):
        post.return_value = Mock(status_code=201)
        identity = email_identity.EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk,
            email="buyer@gmail.com", verified_at=timezone.now(),
            notifications_enabled=True,
        )
        notice = email_identity.enqueue_notice(
            "customer", self.customer.pk, "order-test-240", "Order created", "Paid",
        )
        self.assertIsNotNone(notice)
        self.assertEqual(email_identity.deliver_pending(limit=1), 1)
        self.assertEqual(post.call_args.kwargs["json"]["sender"]["email"], "transactions@kofadimpex.com")
