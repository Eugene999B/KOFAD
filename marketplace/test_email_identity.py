import re
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import override_settings

from core import email_identity
from core.views import resolve_login_identifier
from .models import EmailIdentity, EmailNotice
from .tests import MarketFixtures


EMAIL_TEST = override_settings(
    KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_NOTIFICATIONS_ENABLED=True,
    EMAIL_HOST="smtp.gmail.com",
    EMAIL_HOST_USER="test@kofad.example",
    EMAIL_HOST_PASSWORD="test-only-no-live-credentials",
    DEFAULT_FROM_EMAIL="test@kofad.example",
)


@EMAIL_TEST
class VerifiedEmailIntegrationTests(MarketFixtures):
    @staticmethod
    def code_from(send):
        return re.search(r"code is (\d{6})", send.call_args.args[1]).group(1)

    @patch("core.email_identity.send_mail")
    def test_customer_can_link_verified_email_and_login_without_losing_phone(self, send):
        original_phone = self.customer.phone
        email_identity.request_code("customer", self.customer.pk, "Test.Customer@Example.com")
        code = self.code_from(send)
        self.assertIsNone(email_identity.verified_identity("customer", "test.customer@example.com"))
        self.assertIsNone(email_identity.confirm_code("customer", self.customer.pk, "111111"))
        identity = EmailIdentity.objects.get(kind="customer", owner_id=self.customer.pk)
        self.assertEqual(identity.code_attempts, 1)
        email_identity.confirm_code("customer", self.customer.pk, code)
        self.assertEqual(email_identity.verified_identity(
            "customer", "test.customer@example.com"
        ).owner_id, self.customer.pk)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.phone, original_phone)
        self.assertEqual(self.customer.email, "test.customer@example.com")
        response = self.client.post("/market/account/login/", {
            "phone": "Test.Customer@Example.com",
            "password": "Very-strong-customer-password-42!",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get("market_customer_id"), self.customer.pk)

    @patch("core.email_identity.send_mail")
    def test_staff_verified_email_resolves_to_existing_staff_account(self, send):
        email_identity.request_code("staff", self.staff.pk, "owner@example.com")
        code = self.code_from(send)
        identifier, _ = resolve_login_identifier("owner@example.com")
        self.assertEqual(identifier, "owner@example.com")
        email_identity.confirm_code("staff", self.staff.pk, code)
        identifier, lock = resolve_login_identifier("OWNER@EXAMPLE.COM")
        self.assertEqual(identifier, self.staff.username)
        self.assertEqual(lock, "user:" + self.staff.username.casefold())
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.email, "owner@example.com")

    @patch("core.email_identity.send_mail")
    def test_invalid_codes_are_bounded_and_cannot_verify(self, send):
        email_identity.request_code("customer", self.customer.pk, "verify@example.com")
        for _ in range(5):
            self.assertIsNone(email_identity.confirm_code("customer", self.customer.pk, "000000"))
        identity = EmailIdentity.objects.get(kind="customer", owner_id=self.customer.pk)
        self.assertEqual(identity.code_attempts, 5)
        self.assertEqual(identity.code_digest, "")
        self.assertIsNone(identity.verified_at)
        with self.assertRaises(ValidationError):
            email_identity.confirm_code("customer", self.customer.pk, self.code_from(send))

    @patch("core.email_identity.send_mail")
    def test_approval_and_outbox_are_opt_in_and_idempotent(self, send):
        email_identity.request_code("customer", self.customer.pk, "notify@example.com")
        email_identity.confirm_code("customer", self.customer.pk, self.code_from(send))
        self.assertIsNone(email_identity.enqueue_notice(
            "customer", self.customer.pk, "order-test:1", "Test order", "Order ready"
        ))
        email_identity.set_notifications("customer", self.customer.pk, True)
        first = email_identity.enqueue_notice(
            "customer", self.customer.pk, "order-test:1", "Test order", "Order ready"
        )
        second = email_identity.enqueue_notice(
            "customer", self.customer.pk, "order-test:1", "Test order", "Order ready"
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(EmailNotice.objects.count(), 1)
        self.assertEqual(email_identity.deliver_pending(limit=10), 1)
        first.refresh_from_db()
        self.assertEqual(first.status, "sent")
        self.assertIsNotNone(first.sent_at)
        self.assertEqual(email_identity.deliver_pending(limit=10), 0)

    @patch("core.email_identity.send_mail")
    def test_refuses_to_link_same_verified_email_to_two_customers(self, send):
        from .models import CustomerAccount
        email_identity.request_code("customer", self.customer.pk, "shared@example.com")
        email_identity.confirm_code("customer", self.customer.pk, self.code_from(send))
        duplicate = CustomerAccount.objects.create(phone="+233249001122", full_name="Another customer")
        duplicate.set_password("Second-Account-Test-Password-2026!")
        duplicate.save(update_fields=["password_hash"])
        with self.assertRaises(ValidationError):
            email_identity.request_code("customer", duplicate.pk, "shared@example.com")

    def test_no_smtp_configuration_never_sends(self):
        with override_settings(KOFAD_EMAIL_ENABLED=False):
            with self.assertRaises(ValidationError):
                email_identity.request_code("staff", self.staff.pk, "owner@example.com")
        self.assertEqual(EmailIdentity.objects.count(), 0)
