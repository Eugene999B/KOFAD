"""Regression tests for KOFAD finance controls and accounting source integrity."""
import csv
import io
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import TestCase
from django.utils import timezone

from . import accounting_engine, finance_integrity, services as s
from .models import Allocation, Closing, Document, Payment
from .tests import Fixtures


class FinancialIntegrityTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()
        self.today = timezone.localdate()

    def checks(self):
        return finance_integrity.financial_controls(self.branch, self.today, self.today)

    def statement_rows(self, party):
        captured = {}

        def capture(rows, *args, **kwargs):
            captured["rows"] = rows
            return HttpResponse("statement captured")

        with patch("core.export_views.export", side_effect=capture):
            response = self.client.get(f"/parties/{party.pk}/statement/export/csv/")
        self.assertEqual(response.status_code, 200, response.content)
        return captured["rows"]

    def test_clean_split_tender_sale_reconciles_and_channels_are_signed(self):
        sale = self.sale(payments=[
            {"method": "cash", "amount": "20.00"},
            {"method": "momo", "amount": "30.00"},
        ])
        checks, summary = self.checks()
        failures = [row for row in checks if row["status"] != "OK"]
        self.assertFalse(failures, failures)
        self.assertGreater(summary["Checks passed"], 0)
        signed, _ = finance_integrity.payment_channel_rows(self.branch, self.today, self.today)
        self.assertEqual(sum((r["amount"] for r in signed), Decimal("0")), sale.total)
        self.assertEqual({r["channel"] for r in signed}, {"Cash", "MoMo"})

    def test_inconsistent_legacy_sale_is_reported_without_editing_records(self):
        # Real KOFAD ledger records are immutable at the DATABASE level.
        # Reproduce a legacy/imported discrepancy by creating a new source
        # record with conflicting amounts, never by weakening that protection.
        from .models import Line
        sale = Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            reference="LEGACY-RECONCILIATION-CASE", total=Decimal("50.00"),
            paid=Decimal("40.00"), created_by=self.user,
        )
        Line.objects.create(
            document=sale, product=self.product, description=self.product.name,
            mode="retail_unit", quantity=1, factor=1,
            list_price=Decimal("50.00"), unit_price=Decimal("50.00"),
            unit_cost=Decimal("20.00"), total=Decimal("49.00"),
        )
        Payment.objects.create(
            document=sale, method="cash", amount=Decimal("30.00"), direction=1,
        )
        checks, summary = self.checks()
        failures = [row for row in checks if row["status"] == "REVIEW"]
        self.assertGreaterEqual(len(failures), 2, failures)
        self.assertTrue(any(row["source"] == "Payment" for row in failures))
        self.assertTrue(any(row["source"] == "Product line" for row in failures))
        self.assertGreater(summary["Sum of absolute flagged differences (GHS)"], 0)
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal("50"))

    def test_approved_expense_reversal_nets_to_zero_without_phantom_balance(self):
        expense = s.post_expense(self.user, self.branch, {
            "amount": "25", "category": "fuel", "note": "Misclassified fuel entry",
            "funding_source": "today_sales_receipts", "affects_daily_closing": "1",
            "method": "cash",
        }, uuid.uuid4())
        correction = s.request_correction(self.user, self.branch, expense.pk, "Incorrect expense entered")
        s.review_correction(self.reviewer, self.branch, correction.pk, True)
        trail = accounting_engine.ledger(self.branch, self.today, self.today)
        for code in ("1000", "6010"):
            self.assertEqual(
                sum((r["debit"] - r["credit"] for r in trail if r["account_code"] == code), Decimal("0")),
                Decimal("0"),
                code,
            )
        self.assertEqual(accounting_engine.statements(self.branch, self.today, self.today)["profit"], Decimal("0"))
        checks, _ = self.checks()
        self.assertFalse([r for r in checks if r["status"] != "OK"], checks)

    def test_debt_statement_matches_allocation_aware_balance_and_reversal(self):
        sale = self.sale(2, payments=[], due_date=self.today.isoformat())
        payment = s.post_payment(self.user, self.branch, {
            "invoice": str(sale.pk), "amount": "30", "method": "momo",
        }, uuid.uuid4())
        rows = self.statement_rows(self.customer)
        self.assertEqual([r["change"] for r in rows], [Decimal("100"), Decimal("-30")])
        self.assertEqual(rows[-1]["running"], Decimal("70"))
        self.assertEqual(rows[-1]["running"], s.party_debt(self.customer))

        correction = s.request_correction(self.user, self.branch, payment.pk, "Customer payment wrongly entered")
        s.review_correction(self.reviewer, self.branch, correction.pk, True)
        rows = self.statement_rows(self.customer)
        self.assertEqual([r["change"] for r in rows], [Decimal("100"), Decimal("-30"), Decimal("30")])
        self.assertEqual(rows[-1]["running"], Decimal("100"))
        self.assertEqual(rows[-1]["running"], s.party_debt(self.customer))
        tb = accounting_engine.trial_balance(self.branch, self.today, self.today)
        ar = next(r for r in tb if r["code"] == "1100")
        self.assertEqual(ar["balance"], Decimal("100"))

    def test_screen_and_export_use_identical_reconciled_customer_balance(self):
        # Paid-at-sale money is not customer debt. Previously the screen
        # incorrectly showed the full invoice total as outstanding.
        sale = self.sale(2, payments=[{"method": "cash", "amount": "25"}],
                         due_date=self.today.isoformat())
        pay = s.post_payment(self.user, self.branch, {
            "invoice": str(sale.pk), "amount": "35", "method": "momo",
        }, uuid.uuid4())
        screen = self.client.get(f"/parties/{self.customer.pk}/statement/")
        exported = self.statement_rows(self.customer)
        self.assertEqual(screen.status_code, 200)
        self.assertEqual(screen.context["balance"], Decimal("40"))
        self.assertEqual(screen.context["balance"], s.party_debt(self.customer))
        self.assertEqual([row["running"] for row in screen.context["rows"]],
                         [row["running"] for row in exported])
        self.assertEqual([row["change"] for row in exported],
                         [Decimal("75"), Decimal("-35")])
        self.assertContains(screen, pay.reference)

    def test_supplier_statement_uses_bill_balance_and_reversal(self):
        bill = Document.objects.create(
            branch=self.branch, party=self.supplier, kind="creditor_charge",
            reference="PAYABLE-TEST-1", total=Decimal("150"), paid=Decimal("0"),
            payable_category="utilities", created_by=self.user,
        )
        payment = s.post_payment(self.user, self.branch, {
            "invoice": str(bill.pk), "amount": "40", "method": "bank",
        }, uuid.uuid4(), supplier=True)
        rows = self.statement_rows(self.supplier)
        self.assertEqual([r["change"] for r in rows], [Decimal("150"), Decimal("-40")])
        correction = s.request_correction(self.user, self.branch, payment.pk, "Supplier payment entered by mistake")
        s.review_correction(self.reviewer, self.branch, correction.pk, True)
        rows = self.statement_rows(self.supplier)
        self.assertEqual([r["change"] for r in rows], [Decimal("150"), Decimal("-40"), Decimal("40")])
        self.assertEqual(rows[-1]["running"], s.party_debt(self.supplier))

    def test_daily_closing_variance_is_flagged_and_not_silently_hidden(self):
        Closing.objects.create(
            branch=self.branch, date=self.today,
            expected={"cash": "100", "momo": "20", "bank": "0", "card": "0"},
            counted={"cash": "98.50", "momo": "20", "bank": "0", "card": "0"},
            submitted_by=self.user,
        )
        checks, summary = self.checks()
        warnings = [r for r in checks if r["status"] == "VARIANCE"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["difference"], Decimal("-1.50"))
        self.assertEqual(summary["Closing channel variances"], 1)

    def test_closing_expected_recalculation_detects_incorrect_source_snapshot(self):
        sale = self.sale()
        Closing.objects.create(
            branch=self.branch, date=self.today,
            expected={"cash": "0", "momo": "0", "bank": "0", "card": "0"},
            counted={"cash": "0", "momo": "0", "bank": "0", "card": "0"},
            submitted_by=self.user,
        )
        checks, summary = self.checks()
        matches = [r for r in checks if r["source"] == "Daily closing"
                   and "stored expected versus recomputed" in r["check"]
                   and r["reference"] == str(self.today)
                   and "CASH" in r["check"]]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["expected"], sale.total)
        self.assertEqual(matches[0]["actual"], Decimal("0"))
        self.assertEqual(matches[0]["status"], "REVIEW")
        self.assertGreater(summary["Exceptions for investigation"], 0)

    def test_export_and_accounting_page_include_full_audit_scope(self):
        self.sale()
        page = self.client.get("/accounting/", {"view": "integrity"})
        self.assertEqual(page.status_code, 200, page.content[:400])
        self.assertContains(page, "Financial integrity &amp; reconciliation")
        self.assertContains(page, "Controls examined")
        response = self.client.get("/exports/download/csv/", {"dataset": "financial_integrity"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Document total")
        channel = self.client.get("/exports/download/csv/", {"dataset": "cash_channels"})
        self.assertEqual(channel.status_code, 200)
        self.assertContains(channel, "Inflow")

    def test_statement_refuses_cross_store_allocations_with_clear_error(self):
        sale = self.sale()
        payment = Document.objects.create(
            branch=self.other, party=self.customer, kind="collection",
            reference="WRONG-BRANCH-PAYMENT", total=Decimal("10"), paid=Decimal("10"),
            created_by=self.user,
        )
        Allocation.objects.create(payment_document=payment, invoice=sale, amount=Decimal("10"))
        screen = self.client.get(f"/parties/{self.customer.pk}/statement/")
        self.assertEqual(screen.status_code, 400)
        self.assertContains(screen, "does not agree", status_code=400)
        result = self.client.get(f"/parties/{self.customer.pk}/statement/export/csv/")
        self.assertEqual(result.status_code, 400)
        self.assertContains(result, "does not agree", status_code=400)
        self.assertNotIn("attachment", result.get("Content-Disposition", ""))

    def test_oversized_audit_does_not_crash_finance_pages(self):
        with patch("core.finance_integrity.financial_controls", side_effect=ValidationError("Narrow the date range")):
            page = self.client.get("/accounting/", {"view": "integrity"})
            self.assertEqual(page.status_code, 302)
            self.assertIn("/accounting/", page.url)
            export = self.client.get("/accounting/export/csv/", {"view": "integrity"})
            self.assertEqual(export.status_code, 400)
            self.assertContains(export, "Narrow the date range", status_code=400)

    def test_csv_keeps_signed_money_as_exact_numbers_and_escapes_formula_text(self):
        from .exports import export
        from .models import Company
        response = export([
            {"name": "=2+2", "net": Decimal("-1234.56"), "gross": Decimal("1234.56")},
            {"name": "-cmd|malicious", "net": Decimal("0.01"), "gross": Decimal("0")},
        ], "csv", "Financial cash flow", Company.objects.first(),
            [("name", "External memo"), ("net", "Signed amount"), ("gross", "Gross amount")])
        reader = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(reader[1], ["'=2+2", "-1234.56", "1234.56"])
        self.assertEqual(reader[2], ["'-cmd|malicious", "0.01", "0"])
        self.assertEqual(sum(Decimal(row[1]) for row in reader[1:]), Decimal("-1234.55"))

    def test_scope_excludes_other_store_finances(self):
        other_sale = Document.objects.create(
            branch=self.other, kind="sale", reference="PRIVATE-OTHER-BRANCH",
            total=Decimal("300"), paid=Decimal("0"), created_by=self.user,
        )
        self.sale()
        checks, _ = self.checks()
        self.assertFalse(any(other_sale.reference == row["reference"] for row in checks))
        for dataset in ("financial_integrity", "cash_channels"):
            response = self.client.get("/exports/download/csv/", {"dataset": dataset})
            self.assertNotContains(response, "PRIVATE-OTHER-BRANCH")
