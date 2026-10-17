"""Debt Desk read-only Hubtel verification: fast first check, throttled follow-ups.

No test sends a live payment request or calls a provider.
"""
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.tests import Fixtures
from . import client, debt_momo
from .models import DebtMomoCollection


class DebtMomoFastVerificationTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.row = DebtMomoCollection.objects.create(
            request_key=uuid.uuid4(),
            branch=self.branch,
            party=self.customer,
            requested_by=self.user,
            amount=Decimal("2.00"),
            channel="mtn-gh",
            phone_encrypted="unused-test-value",
            phone_suffix="4567",
            wallet_name="Test Wallet",
            reference=uuid.uuid4().hex,
            status="pending",
            provider_transaction_id="provider-original-id",
            next_check_at=timezone.now() + debt_momo.CHECK_WAIT,
        )

    def age_request(self, seconds):
        now = timezone.now()
        DebtMomoCollection.objects.filter(pk=self.row.pk).update(
            created_at=now - timedelta(seconds=seconds),
            next_check_at=now - timedelta(seconds=1),
        )
        self.row.refresh_from_db()

    @patch("hubtel_direct.client.check")
    def test_first_read_only_check_never_runs_during_initial_wait(self, provider):
        self.assertFalse(debt_momo.ready_for_check(self.row))
        self.assertIsNone(debt_momo.reconcile(self.row.pk).checked_at)
        provider.assert_not_called()

    @patch("hubtel_direct.client.check", return_value=("pending", "provider-original-id"))
    def test_first_check_at_twenty_seconds_then_throttle_repeat_checks(self, provider):
        self.age_request(25)
        self.assertTrue(debt_momo.ready_for_check(self.row))
        checked = debt_momo.reconcile(self.row.pk)
        self.assertEqual(checked.status, "pending")
        self.assertIsNotNone(checked.checked_at)
        self.assertIsNone(checked.receipt_id)
        self.assertGreaterEqual(checked.next_check_at, checked.checked_at + debt_momo.RECHECK_WAIT)
        self.assertFalse(debt_momo.ready_for_check(checked))
        debt_momo.reconcile(self.row.pk)
        provider.assert_called_once()

    @patch("hubtel_direct.client.check", side_effect=client.StatusUnconfirmed("no matching provider record"))
    def test_missing_status_record_does_not_invent_failure_or_receipt(self, provider):
        self.age_request(25)
        checked = debt_momo.reconcile(self.row.pk)
        self.assertEqual(checked.status, "pending")
        self.assertIsNone(checked.receipt_id)
        self.assertGreaterEqual(checked.next_check_at, checked.checked_at + debt_momo.RECHECK_WAIT)
        provider.assert_called_once()

    @patch("hubtel_direct.client.check", return_value=("failed", "provider-original-id"))
    def test_confirmed_failed_payment_stays_failed_without_receipt(self, provider):
        self.age_request(25)
        checked = debt_momo.reconcile(self.row.pk)
        self.assertEqual(checked.status, "failed")
        self.assertIsNone(checked.receipt_id)
        self.assertIsNone(checked.next_check_at)
        provider.assert_called_once()

    @patch("hubtel_direct.client.submit")
    def test_paid_finance_attention_can_be_reverified_after_book_reopens(self, send):
        """Only a second independently verified PAID lookup posts the receipt."""
        from django.core.exceptions import ValidationError
        from core.models import Document, Payment

        Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("20.00"), paid=Decimal("0.00"),
            created_by=self.user, reference="OFFLINE-STAFF-DEBT-RECOVERY",
        )
        self.age_request(35)
        with patch("hubtel_direct.client.check", return_value=("success", "hubtel-paid-id")) as verify, \
             patch("core.debts.post_customer_payment", side_effect=ValidationError("Today is closed.")):
            held = debt_momo.reconcile(self.row.pk)
        verify.assert_called_once()
        self.assertEqual(held.status, "attention")
        self.assertIsNone(held.receipt_id)
        send.assert_not_called()

        # An old attention item can still be verified using GET; the 72-hour
        # cutoff must not make a real provider-paid receipt unrecoverable.
        old = timezone.now() - timedelta(hours=75)
        DebtMomoCollection.objects.filter(pk=self.row.pk).update(
            created_at=old, checked_at=old, next_check_at=None,
        )
        with patch("hubtel_direct.client.check", return_value=("success", "hubtel-paid-id")) as recheck:
            settled = debt_momo.reconcile(self.row.pk)
        recheck.assert_called_once()
        send.assert_not_called()
        self.assertEqual(settled.status, "success")
        self.assertIsNotNone(settled.receipt_id)
        self.assertEqual(settled.receipt.total, Decimal("2.00"))
        self.assertEqual(
            Payment.objects.filter(document=settled.receipt, method="momo").count(), 1,
        )
        self.assertEqual(DebtMomoCollection.objects.get(pk=self.row.pk).receipt_id, settled.receipt_id)
        self.assertEqual(debt_momo.reconcile(self.row.pk).receipt_id, settled.receipt_id)

    @patch("hubtel_direct.client.submit")
    def test_attention_mismatch_cannot_create_receipt_or_trigger_second_charge(self, send):
        """Rechecking a mismatched provider response leaves the finance hold."""
        from django.core.exceptions import ValidationError
        from core import debts
        self.age_request(75)
        DebtMomoCollection.objects.filter(pk=self.row.pk).update(status="attention")
        with patch("hubtel_direct.client.check", side_effect=ValidationError("amount mismatch")) as verify, \
             patch.object(debts, "post_customer_payment") as post:
            held = debt_momo.reconcile(self.row.pk)
        verify.assert_called_once()
        post.assert_not_called()
        send.assert_not_called()
        self.assertEqual(held.status, "attention")
        self.assertIsNone(held.receipt_id)
        self.assertFalse(debt_momo.ready_for_check(held))

    def test_finance_staff_can_request_read_only_recheck_of_attention_payment(self):
        from django.core.exceptions import ValidationError
        self.authenticate_client()
        self.age_request(75)
        DebtMomoCollection.objects.filter(pk=self.row.pk).update(status="attention")
        detail_url = reverse("hubtel_debt_momo_status", args=[self.row.pk])
        check_url = reverse("hubtel_debt_momo_check", args=[self.row.pk])
        page = self.client.get(detail_url)
        self.assertTrue(page.context["can_check"])
        self.assertContains(page, "Recheck original payment")
        with patch("hubtel_direct.client.check", side_effect=ValidationError("provider mismatch")) as verify, \
             patch("hubtel_direct.client.submit") as charge:
            response = self.client.post(check_url)
        self.assertEqual(response.status_code, 302)
        verify.assert_called_once()
        charge.assert_not_called()
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, "attention")
        self.assertFalse(self.client.get(detail_url).context["can_check"])

    def test_staff_button_uses_same_cooldown_as_service(self):
        self.authenticate_client()
        self.age_request(25)
        url = reverse("hubtel_debt_momo_status", args=[self.row.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_check"])
        self.row.checked_at = timezone.now()
        self.row.save(update_fields=["checked_at"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_check"])
