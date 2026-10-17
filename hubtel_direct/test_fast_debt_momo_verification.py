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
