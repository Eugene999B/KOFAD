import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from . import inventory_exceptions as ix
from . import services as s
from .models import Document, QuarantineItem, Stock, SupplierReturn, TransferReceipt
from .tests import Fixtures


class InventoryExceptionTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def purchase(self, quantity=10, price="20", payments=None):
        return s.post_trade(self.user, self.branch, {
            "party": self.supplier.pk,
            "items": [{
                "product": self.product.pk,
                "mode": "retail_unit",
                "quantity": quantity,
                "price": price,
            }],
            "payments": [] if payments is None else payments,
            "due_date": timezone.localdate().isoformat(),
        }, uuid.uuid4(), "purchase")

    def test_supplier_return_reduces_stock_and_supplier_debt_after_independent_review(self):
        purchase = self.purchase()
        self.assertEqual(s.balance(purchase), Decimal("200"))
        request = ix.request_supplier_return(
            self.user, self.branch, purchase.lines.get().pk, 4,
            "Supplier accepted four incorrect units", "cash",
        )
        with self.assertRaisesRegex(ValidationError, "different authorized colleague"):
            ix.review_supplier_return(self.user, self.branch, request.pk, True)

        result = ix.review_supplier_return(self.reviewer, self.branch, request.pk, True)
        self.assertEqual(result.status, "approved")
        self.assertEqual(result.posted.kind, "supplier_return")
        self.assertEqual(result.posted.total, Decimal("80"))
        self.assertEqual(result.posted.paid, Decimal("0"))
        self.assertEqual(s.balance(purchase), Decimal("120"))
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 246)

    def test_paid_supplier_return_records_inbound_refund(self):
        purchase = self.purchase(5, payments=[{"method": "bank", "amount": "100"}])
        request = ix.request_supplier_return(
            self.user, self.branch, purchase.lines.get().pk, 2,
            "Supplier refund for returned paid stock", "bank",
        )
        result = ix.review_supplier_return(self.reviewer, self.branch, request.pk, True)
        payment = result.posted.payments.get()
        self.assertEqual(payment.direction, 1)
        self.assertEqual(payment.amount, Decimal("40"))
        self.assertEqual(result.posted.paid, Decimal("40"))
        self.assertEqual(s.channel_totals(self.branch, timezone.localdate())["bank"], Decimal("-60"))

    def test_supplier_return_reservations_cannot_exceed_purchase(self):
        purchase = self.purchase(3)
        ix.request_supplier_return(
            self.user, self.branch, purchase.lines.get().pk, 2,
            "First supplier return reservation", "cash",
        )
        with self.assertRaisesRegex(ValidationError, "exceeds"):
            ix.request_supplier_return(
                self.reviewer, self.branch, purchase.lines.get().pk, 2,
                "Second supplier return reservation", "cash",
            )
        self.assertEqual(SupplierReturn.objects.count(), 1)

    def test_quarantine_requires_independent_review_and_release_restores_stock(self):
        item = ix.request_quarantine(
            self.user, self.branch, self.product.pk, 10,
            "Packaging damaged during warehouse handling",
        )
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 240)
        with self.assertRaisesRegex(ValidationError, "different authorized colleague"):
            ix.review_quarantine(self.user, self.branch, item.pk, True)

        ix.review_quarantine(self.reviewer, self.branch, item.pk, True)
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 230)
        ix.resolve_quarantine(
            self.reviewer, self.branch, item.pk, "release",
            "Inspection confirmed the product itself is sellable",
        )
        item.refresh_from_db()
        self.assertEqual(item.status, "released")
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 240)

    def test_quarantine_writeoff_creates_accounting_loss_without_restoring_stock(self):
        item = ix.request_quarantine(
            self.user, self.branch, self.product.pk, 5,
            "Five units crushed and unsuitable for sale",
        )
        ix.review_quarantine(self.reviewer, self.branch, item.pk, True)
        ix.resolve_quarantine(
            self.reviewer, self.branch, item.pk, "writeoff",
            "Inspection confirmed permanent physical damage",
        )
        item.refresh_from_db()
        self.assertEqual(item.status, "written_off")
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 235)
        self.assertIsNotNone(item.loss_document)
        self.assertEqual(item.loss_document.kind, "inventory_writeoff")
        self.assertEqual(item.loss_document.total, Decimal("100"))
        self.assertEqual(item.loss_document.lines.get().quantity, 5)

    def test_transfer_loss_posts_inventory_loss_document(self):
        op = s.request_operation(self.user, self.branch, {
            "kind": "transfer", "product": self.product.pk, "quantity": 12,
            "destination": self.other.pk, "reason": "Replenish second location",
        })
        s.advance_operation(self.reviewer, op.pk, "approve")
        s.advance_operation(self.user, op.pk, "dispatch")
        s.advance_operation(self.user, op.pk, "receive", 7, "Five units missing from delivered transfer")
        from .transfers import resolve_transfer
        resolve_transfer(
            self.reviewer, op.pk, "loss",
            "Carrier confirmed the five missing units were lost",
        )
        receipt = TransferReceipt.objects.get(operation=op)
        self.assertEqual(receipt.resolution, "loss")
        self.assertIsNotNone(receipt.loss_document)
        self.assertEqual(receipt.loss_document.kind, "inventory_writeoff")
        self.assertEqual(receipt.loss_document.total, Decimal("100"))
        self.assertEqual(Stock.objects.get(branch=self.other, product=self.product).quantity, 7)

    def test_inventory_report_separates_sellable_and_quarantine(self):
        item = ix.request_quarantine(
            self.user, self.branch, self.product.pk, 8,
            "Eight units awaiting damage inspection",
        )
        ix.review_quarantine(self.reviewer, self.branch, item.pk, True)
        from .reporting import build_report
        rows, columns = build_report(
            self.branch, timezone.localdate(), timezone.localdate(), "inventory"
        )
        row = next(r for r in rows if r["sku"] == self.product.sku)
        self.assertEqual(row["units"], 232)
        self.assertEqual(row["quarantine"], 8)
        self.assertEqual(row["physical"], 240)
        self.assertIn(("quarantine", "Quarantined units"), columns)

    def test_exception_and_statement_exports_render(self):
        purchase = self.purchase()
        item = ix.request_quarantine(
            self.user, self.branch, self.product.pk, 2,
            "Two units isolated for quality inspection",
        )
        ix.review_quarantine(self.reviewer, self.branch, item.pk, True)
        ix.request_supplier_return(
            self.user, self.branch, purchase.lines.get().pk, 1,
            "One incorrect unit prepared for supplier return", "cash",
        )
        self.authenticate_client()
        for dataset in ("operations", "supplier_returns", "quarantine", "closings", "losses"):
            with self.subTest(dataset=dataset):
                response = self.client.get(f"/exports/download/csv/?dataset={dataset}")
                self.assertEqual(response.status_code, 200)
        for format in ("pdf", "xlsx", "docx", "csv"):
            with self.subTest(format=format):
                response = self.client.get(
                    f"/parties/{self.supplier.pk}/statement/export/{format}/"
                )
                self.assertEqual(response.status_code, 200)

    def test_new_exception_pages_render(self):
        self.authenticate_client()
        self.assertEqual(self.client.get("/supplier-returns/").status_code, 200)
        self.assertEqual(self.client.get("/quarantine/").status_code, 200)
