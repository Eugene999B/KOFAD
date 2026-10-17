"""Creditor running balances must follow the business dates printed in statements."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from . import creditors
from .models import Allocation, Document
from .tests import Fixtures


class CreditorStatementDateOrderingTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()

    def document(self, reference, kind, amount, *, business_date=None, original=None):
        return Document.objects.create(
            branch=self.branch,
            party=self.supplier,
            kind=kind,
            reference=reference,
            total=Decimal(amount),
            paid=Decimal("0.00") if kind == "creditor_charge" else Decimal(amount),
            document_date=business_date,
            original=original,
            created_by=self.user,
        )

    def test_backdated_bills_sort_by_displayed_date_and_running_balance_reconciles(self):
        today = timezone.localdate()
        older_date = today - timedelta(days=5)
        newer_date = today - timedelta(days=3)
        oldest = self.document("STAT-OLD", "creditor_charge", "100.00", business_date=older_date)
        paid = self.document("STAT-PAY", "supplier_payment", "60.00", original=oldest)
        Allocation.objects.create(
            payment_document=paid, invoice=oldest, amount=Decimal("60.00")
        )
        later = self.document("STAT-LATE", "creditor_charge", "30.00", business_date=newer_date)

        # Both bills were entered on the same day as the payment, but the
        # displayed business dates put the bills first.
        rows = creditors.creditor_statement_rows(self.supplier)
        self.assertEqual(
            [r["reference"] for r in rows],
            ["STAT-OLD", "STAT-LATE", "STAT-PAY"],
        )
        self.assertEqual([r["date"] for r in rows], [
            older_date, newer_date, today,
        ])
        self.assertEqual([r["running"] for r in rows], [
            Decimal("100.00"), Decimal("130.00"), Decimal("70.00"),
        ])
        self.assertEqual(rows[-1]["running"], Decimal("70.00"))

        # The same chronology must appear in downloaded creditor statements.
        response = self.client.get(f"/creditors/{self.supplier.pk}/statement/csv/")
        self.assertEqual(response.status_code, 200)
        export_body = response.content.decode("utf-8-sig")
        self.assertLess(export_body.index("STAT-OLD"), export_body.index("STAT-LATE"))
        self.assertLess(export_body.index("STAT-LATE"), export_body.index("STAT-PAY"))

    def test_supplier_payment_reversal_restores_balance_after_payment(self):
        today = timezone.localdate()
        invoice = self.document(
            "STAT-REV-BILL", "creditor_charge", "100.00",
            business_date=today - timedelta(days=2),
        )
        payment = self.document("STAT-REV-PAY", "supplier_payment", "40.00", original=invoice)
        Allocation.objects.create(
            payment_document=payment, invoice=invoice, amount=Decimal("40.00"),
        )
        reversal = self.document("STAT-REVERSAL", "reversal", "40.00", original=payment)
        rows = creditors.creditor_statement_rows(self.supplier)
        self.assertEqual([r["reference"] for r in rows], [
            "STAT-REV-BILL", "STAT-REV-PAY", "STAT-REVERSAL",
        ])
        self.assertEqual([r["running"] for r in rows], [
            Decimal("100.00"), Decimal("60.00"), Decimal("100.00"),
        ])
        self.assertEqual(rows[-1]["running"], invoice.total)
