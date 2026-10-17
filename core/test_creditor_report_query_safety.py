"""The Creditors workspace must not crash or silently understate payables."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core import creditors as creditor_service
from core.models import Document, Party
from core.tests import Fixtures


class CreditorReportQuerySafetyTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()

    def test_invalid_dates_are_friendly_client_errors_for_page_and_export(self):
        for path in ("/creditors/", "/creditors/export/csv/"):
            with self.subTest(path=path):
                bad_date = self.client.get(path, {"due_from": "not-a-date"})
                self.assertEqual(bad_date.status_code, 400)
                self.assertIn(b"valid creditor due-date", bad_date.content)

                reversed_range = self.client.get(path, {
                    "due_from": "2026-10-18", "due_to": "2026-10-17",
                })
                self.assertEqual(reversed_range.status_code, 400)
                self.assertIn(b"Due-from date", reversed_range.content)

    def test_invalid_payment_and_creditor_query_params_do_not_crash(self):
        for params in (
            {"payment": "not-a-uuid"},
            {"payment": "x" * 5000},
            {"creditor": "9" * 5000},
            {"creditor": "\u00b2"},
        ):
            with self.subTest(params=list(params)):
                response = self.client.get("/creditors/", params)
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context["payment_doc"])

    def test_more_than_600_billed_creditors_never_silently_undercount(self):
        suppliers = Party.objects.bulk_create([
            Party(
                branch=self.branch, kind="supplier",
                name=f"AuditVendor{i:04d}", phone=f"024{i:08d}",
            )
            for i in range(601)
        ])
        business_day = timezone.localdate()
        Document.objects.bulk_create([
            Document(
                branch=self.branch, party=supplier, kind="creditor_charge",
                reference=f"AUD-BILL-{i:04d}", total=Decimal("2.00"),
                paid=Decimal("0.00"), document_date=business_day,
                due_date=business_day + timedelta(days=7),
                payable_category="utilities", created_by=self.user,
            )
            for i, supplier in enumerate(suppliers)
        ])
        with self.assertRaisesMessage(ValidationError, "More than 600 creditor accounts"):
            creditor_service.creditor_accounts(self.branch)

        for path in ("/creditors/", "/creditors/export/csv/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"Narrow the creditor search", response.content)

        # Narrowing to one creditor must still produce a complete usable report.
        response = self.client.get("/creditors/", {"q": "AuditVendor0000"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["overview"]["total_payables"], Decimal("2.00"))

    def test_unbilled_suppliers_are_not_mistaken_for_report_truncation(self):
        Party.objects.bulk_create([
            Party(
                branch=self.branch, kind="supplier",
                name=f"NeverBilled{i:04d}", phone="0240000000",
            )
            for i in range(605)
        ])
        self.assertEqual(creditor_service.creditor_accounts(self.branch), [])
        response = self.client.get("/creditors/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["overview"]["total_payables"], Decimal("0.00"))
