"""Ensure approved payroll snapshots never follow mutable beneficiary edits."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core import payroll_engine
from core.models import Worker
from core.tests import Fixtures
from . import client, payroll
from .models import PayrollTransferLine, WorkerTransferProfile


@override_settings(
    HUBTEL_DIRECT_MAX_PAYOUT_GHS="2000.00",
    HUBTEL_DIRECT_DAILY_PAYOUT_GHS="10000.00",
)
class PayrollSnapshotTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.worker = Worker.objects.create(
            branch=self.branch, employee_code="PAY-TEST-01",
            first_name="Test", last_name="Worker", phone="0000000000",
            department="Operations", job_title="Staff",
            hire_date=date(2026, 1, 1),
            base_salary=Decimal("1000.00"), created_by=self.user,
        )
        period = payroll_engine.create_period(self.user, self.branch, 2026, 10)
        period, _ = payroll_engine.prepare_period(self.user, period)
        period = payroll_engine.approve_period(self.reviewer, period)
        self.period = payroll_engine.lock_period(self.reviewer, period)
        self.profile = WorkerTransferProfile.objects.create(
            worker=self.worker, payment_type="bank", channel="",
            bank_code="030100", destination_encrypted=client.seal("000000"),
            destination_suffix="0000", provider_name="TEST WORKER",
            verified_at=timezone.now(), verified_by=self.user,
        )

    def test_prepared_batch_keeps_bank_rail_even_if_profile_changes(self):
        with patch("hubtel_direct.payroll.client.BANK_CODES", {"030100": "Test bank"}):
            batch = payroll.prepare_batch(self.user, self.period)
        line = PayrollTransferLine.objects.get(batch=batch)
        self.assertEqual(line.payment_type_snapshot, "bank")
        self.profile.payment_type = "momo"
        self.profile.save(update_fields=["payment_type"])
        line.refresh_from_db()
        self.assertEqual(line.payment_type_snapshot, "bank")

    def test_active_batch_prevents_changing_salary_destination(self):
        with patch("hubtel_direct.payroll.client.BANK_CODES", {"030100": "Test bank"}):
            payroll.prepare_batch(self.user, self.period)
        with patch("hubtel_direct.payroll.verification.inquiry",
                   return_value={"phone": "0000000000", "wallet_name": "TEST WORKER"}):
            with self.assertRaisesRegex(ValidationError, "active Hubtel salary batch"):
                payroll.save_recipient(self.user, self.worker, payment_type="momo",
                                       destination="0000000000", channel="mtn-gh")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.payment_type, "bank")
