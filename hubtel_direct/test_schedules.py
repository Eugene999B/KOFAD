"""Offline Direct Debit dates and amounts; NO merchant wallet operations."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.models import Document
from core.tests import Fixtures
from . import mandates, schedules
from .models import DebtMandate, DebtDebitAttempt, OnlineDebtSettings


class AutomaticScheduleArithmeticTests(SimpleTestCase):
    def test_three_payments_round_last_without_overcharging(self):
        result = schedules.preview(Decimal("800.00"), "weekly", date(2026, 10, 16), "3")
        self.assertEqual(result["amount_per_period"], Decimal("266.67"))
        self.assertEqual(result["last_amount"], Decimal("266.66"))
        self.assertEqual([r["date"] for r in result["schedule"]],
                         ["2026-10-16", "2026-10-23", "2026-10-30"])
        self.assertEqual(sum(Decimal(x["amount"]) for x in result["schedule"]), Decimal("800.00"))

    def test_month_end_and_leap_year_recurrence(self):
        r = schedules.preview("9.00", "monthly", date(2028, 1, 31), 3)
        self.assertEqual([x["date"] for x in r["schedule"]], [
            "2028-01-31", "2028-02-29", "2028-03-29",
        ])

    def test_single_payment_and_invalid_payment_count(self):
        one = schedules.preview("123.45", "once", date(2026, 10, 20), 1)
        self.assertEqual(one["schedule"][0]["amount"], "123.45")
        for count, frequency in [(0,"weekly"),(121,"weekly"),(2,"once"),(4,"unknown"),(2.5,"weekly")]:
            with self.subTest(count=count, frequency=frequency):
                with self.assertRaises(ValidationError):
                    schedules.preview("123.45", frequency, date(2026, 10, 20), count)

    def test_no_more_payments_than_pennies(self):
        with self.assertRaises(ValidationError):
            schedules.preview("0.02", "daily", date(2026, 10, 20), 3)


class AutomaticScheduleIntegrationTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.consent = True
        self.customer.phone = "+233241234567"
        self.customer.save(update_fields=["consent", "phone"])
        Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("800.00"), paid=Decimal("0.00"),
            due_date=timezone.localdate() + timedelta(days=14),
            reference="AUTO-SCHEDULE-TEST", created_by=self.user,
        )
        OnlineDebtSettings.objects.get_or_create(branch=self.branch)

    def test_staff_count_creates_signed_preview_not_wallet_charge(self):
        first = timezone.localdate() + timedelta(days=7)
        with patch("hubtel_direct.debit_api.initiate_preapproval") as prompt, \
             patch("hubtel_direct.debit_api.debit_charge") as charge:
            plan = mandates.create_plan(
                self.user, self.branch, party_id=self.customer.pk,
                phone="0241234567", channel="mtn-gh-direct-debit",
                frequency="weekly", first_due_date=first, installment_count="3",
            )
        self.assertEqual(plan.amount_per_period, Decimal("266.67"))
        self.assertEqual(plan.maximum_total, Decimal("800.00"))
        self.assertEqual(plan.terms_snapshot["installment_count"], 3)
        self.assertEqual(plan.terms_snapshot["final_installment"], "266.66")
        self.assertEqual(plan.terms_snapshot["last_due_date"], (first + timedelta(days=14)).isoformat())
        self.assertEqual(plan.status, "draft")
        self.assertFalse(DebtDebitAttempt.objects.exists())
        prompt.assert_not_called()
        charge.assert_not_called()

    def test_staff_waiting_page_and_draft_edit_show_exact_dates(self):
        plan = mandates.create_plan(
            self.user, self.branch, party_id=self.customer.pk,
            phone="0241234567", channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=timezone.localdate() + timedelta(days=7),
            installment_count="4",
        )
        self.authenticate_client()
        from django.urls import reverse
        response = self.client.get(reverse("hubtel_debt_staff_status", args=[plan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Waiting for the customer")
        self.assertContains(response, "GHS 200.00")
        self.assertContains(response, "Hubtel preapproval has not been activated")
        self.assertContains(response, "Open customer approval page")
        self.assertContains(response, "4 scheduled payments")

        edit = mandates.update_draft_plan(
            self.user, self.branch, plan.pk, frequency="monthly",
            first_due_date=timezone.localdate() + timedelta(days=14),
            installment_count="2",
        )
        self.assertEqual(edit.terms_snapshot["installment_count"], 2)
        self.assertEqual(edit.amount_per_period, Decimal("400.00"))
        DebtMandate.objects.filter(pk=plan.pk).update(status="pending")
        with self.assertRaisesRegex(ValidationError, "Cancel it"):
            mandates.update_draft_plan(
                self.user, self.branch, plan.pk, installment_count="6",
            )
