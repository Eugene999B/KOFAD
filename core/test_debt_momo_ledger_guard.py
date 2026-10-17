"""Manual Debt Desk receipts must not bypass Hubtel Mobile Money verification.

All tests are offline and do not send any provider POST.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import debts, services
from core.models import Document
from core.tests import Fixtures


class DebtMomoLedgerGuardTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.invoice = Document.objects.create(
            branch=self.branch,
            party=self.customer,
            kind="sale",
            reference="OFFLINE-DEBT-MOMO-LEDGER",
            total=Decimal("50.00"),
            paid=Decimal("0.00"),
            due_date=timezone.localdate() + timedelta(days=1),
            created_by=self.user,
        )

    def payment(self, method, *, provider_verified_receipt=False):
        return debts.post_customer_payment(
            self.user, self.branch,
            {"party": str(self.customer.pk), "amount": "10.00",
             "method": method, "reference": "staff-observation-only"},
            uuid.uuid4(), provider_verified_receipt=provider_verified_receipt,
        )

    def test_unverified_momo_cannot_create_collection_or_reduce_balance(self):
        with self.assertRaisesRegex(ValidationError, "Hubtel verification"):
            self.payment("momo")
        self.assertFalse(Document.objects.filter(kind="collection").exists())
        self.assertEqual(services.balance(self.invoice), Decimal("50.00"))

    def test_customer_profile_hides_manual_momo_and_rejects_forged_post(self):
        self.authenticate_client()
        url = reverse("customer_profile", args=[self.customer.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, '<option value="momo">')
        self.assertContains(page, "Hubtel-verified Debt Desk")
        attempt = self.client.post(url, {
            "key": str(uuid.uuid4()), "method": "momo",
            "amount": "10.00", "reference": "no-provider-proof",
        })
        self.assertEqual(attempt.status_code, 200)
        self.assertEqual(services.balance(self.invoice), Decimal("50.00"))
        self.assertFalse(Document.objects.filter(kind="collection").exists())

    def test_normal_cash_collection_still_posts(self):
        receipt = self.payment("cash")
        self.assertEqual(receipt.total, Decimal("10.00"))
        self.assertEqual(services.balance(self.invoice), Decimal("40.00"))

    def test_internal_verified_hubtel_receipt_can_post_once(self):
        receipt = self.payment("momo", provider_verified_receipt=True)
        self.assertEqual(receipt.total, Decimal("10.00"))
        self.assertEqual(services.balance(self.invoice), Decimal("40.00"))
