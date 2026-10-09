"""Receivables email tests: checkout consent, debt cadence, review and idempotency."""
import uuid
from datetime import timedelta, time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from core import services
from core.debt_email import (queue_credit_sale, queue_debt_payment,
                             run_debt_email_reminders, email_still_allowed)
from core.email_models import EmailLetter, EmailMailbox
from core.models import Access, DebtSettings, Document, Party
from core.tests import Fixtures

PROVIDER = dict(
    KOFAD_EMAIL_ENABLED=True, KOFAD_EMAIL_CENTER_ENABLED=True,
    KOFAD_EMAIL_PROVIDER="brevo", KOFAD_BREVO_API_KEY="TEST-NO-LIVE-KEY",
    KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
)


@override_settings(**PROVIDER)
class DebtEmailAutomationTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.mailbox = EmailMailbox.objects.get(address="accounts@kofadimpex.com")
        self.policy = DebtSettings.objects.first() or DebtSettings.objects.create()
        self.policy.email_delivery_mode = "draft"
        self.policy.reminder_time = time(0, 0)
        self.policy.save(update_fields=["email_delivery_mode", "reminder_time"])
        self.customer = Party.objects.create(
            branch=self.branch, kind="customer", name="Debt Email Buyer",
            phone="+233241234567", email="buyer@example.org", debt_email_opt_in=True,
        )
        self.invoice = Document.objects.create(
            reference="SALE-DEBTMAIL-1", branch=self.branch, party=self.customer,
            kind="sale", total=Decimal("150.00"), paid=Decimal("20.00"),
            due_date=timezone.localdate() + timedelta(days=3), created_by=self.user,
        )

    def test_credit_notice_follows_draft_mode_and_is_idempotent(self):
        first = queue_credit_sale(self.invoice)
        second = queue_credit_sale(self.invoice)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, "draft")
        self.assertEqual(first.to_address, "buyer@example.org")
        self.assertEqual(EmailLetter.objects.filter(source_key__startswith="debtmail:").count(), 1)

    def test_opt_out_blocks_credit_notice_and_prevents_queued_send(self):
        msg = queue_credit_sale(self.invoice)
        self.customer.debt_email_opt_in = False
        self.customer.save(update_fields=["debt_email_opt_in"])
        self.assertFalse(email_still_allowed(msg.source_key, msg.to_address))
        second = Document.objects.create(
            reference="SALE-DEBTMAIL-2", branch=self.branch, party=self.customer,
            kind="sale", total=Decimal("90.00"), paid=Decimal("0.00"),
            due_date=timezone.localdate() + timedelta(days=4), created_by=self.user)
        self.assertIsNone(queue_credit_sale(second))

    def test_email_reminders_follow_debt_settings_and_frequency(self):
        self.policy.email_delivery_mode = "send"
        self.policy.due_soon_days = "3"
        self.policy.minimum_balance = Decimal("1.00")
        self.policy.save(update_fields=["email_delivery_mode", "due_soon_days", "minimum_balance"])
        with patch("core.debt_email.timezone.localtime", wraps=timezone.localtime):
            self.assertEqual(run_debt_email_reminders(), 1)
        self.assertEqual(run_debt_email_reminders(), 0)
        row = EmailLetter.objects.get(source_key__startswith="debtmail:", status="queued")
        self.assertIn("outstanding balance", row.body_text)
        self.customer.debt_email_opt_in = False
        self.customer.save(update_fields=["debt_email_opt_in"])
        self.assertFalse(email_still_allowed(row.source_key, row.to_address))

    def test_debt_payment_receipt_includes_reconciled_balance(self):
        payment = Document.objects.create(
            reference="DEBTMAIL-COLLECTION-1", branch=self.branch,
            party=self.customer, kind="collection", total=Decimal("40.00"),
            paid=Decimal("40.00"), created_by=self.user)
        notice = queue_debt_payment(payment)
        self.assertEqual(notice.status, "draft")
        self.assertIn("payment of GHS 40.00", notice.body_text)
        self.assertIn("Remaining recorded balance", notice.body_text)

    def test_settings_off_by_default_rejects_all_sends(self):
        self.policy.email_delivery_mode = "off"
        self.policy.save(update_fields=["email_delivery_mode"])
        self.assertIsNone(queue_credit_sale(self.invoice))
        self.assertEqual(run_debt_email_reminders(), 0)

    def test_saved_customer_email_mismatch_is_not_overwritten(self):
        from core.identity import resolve_sale_customer
        with self.assertRaises(ValidationError):
            resolve_sale_customer(self.user, self.branch, {
                "party": self.customer.pk,
                "customer_email": "different@example.org",
                "customer_debt_email_opt_in": True,
            }, services.audit)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.email, "buyer@example.org")

    def test_credit_checkout_choice_is_distinct_from_sms_consent(self):
        from core.identity import resolve_sale_customer
        self.customer.debt_email_opt_in = False
        self.customer.save(update_fields=["debt_email_opt_in"])
        result = resolve_sale_customer(self.user, self.branch, {
            "party": self.customer.pk,
            "customer_email": self.customer.email,
            "customer_debt_email_opt_in": True,
            "customer_consent": False,
        }, services.audit)
        self.assertTrue(result.debt_email_opt_in)
        self.assertFalse(result.consent)

    def test_owner_can_review_draft_and_audit_reviewer(self):
        admin = User.objects.create_superuser("debtmailowner", "debtmailadmin@example.org", "strong-test-password-123")
        draft = queue_credit_sale(self.invoice)
        access, _ = Access.objects.get_or_create(user=admin)
        self.client.force_login(admin)
        session = self.client.session
        session["access_version"] = access.session_version
        session.save()
        resp = self.client.post(reverse("email_center"), {
            "action": "approve_draft", "mailbox_id": self.mailbox.pk, "letter_id": draft.pk
        })
        self.assertEqual(resp.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "queued")
        self.assertEqual(draft.approved_by, admin)
        self.assertIsNotNone(draft.approved_at)

    def test_mail_history_search_is_scoped_to_mailbox(self):
        queue_credit_sale(self.invoice)
        other = EmailMailbox.objects.get(address="support@kofadimpex.com")
        EmailLetter.objects.create(
            mailbox=other, direction="inbound", status="received",
            from_address="secret@example.org", to_address=other.address,
            subject="Private support", body_text="confidential content", fingerprint="b"*64,
        )
        admin = User.objects.create_superuser("debtmailhistory", "history@example.org", "strong-test-password-123")
        access, _ = Access.objects.get_or_create(user=admin)
        self.client.force_login(admin)
        session = self.client.session
        session["access_version"] = access.session_version
        session.save()
        resp = self.client.get(reverse("email_center"), {
            "mailbox": self.mailbox.pk, "direction": "outbound", "q": "credit sale"
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "SALE-DEBTMAIL-1")
        self.assertNotContains(resp, "Private support")
        self.assertContains(resp, "Created by")
