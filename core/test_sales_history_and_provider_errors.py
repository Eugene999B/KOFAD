"""Regression checks for trustworthy sales badges and actionable declines."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from .models import HeldSale
from .pos_paystack import LABEL_PREFIX
from .payment_failure_guidance import explain_provider_error
from .sale_history import decorate_sales
from .tests import Fixtures


class SaleHistoryEvidenceTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()

    def test_staff_recorded_momo_is_not_mislabelled_gateway_verified(self):
        doc = self.sale(payments=[{"method": "momo", "amount": "50.00"}])
        rows = decorate_sales(self.branch, [doc])
        self.assertEqual(rows[0].transaction_type, "Paid sale")
        self.assertFalse(rows[0].paystack_verified_reference)
        result = self.client.get("/documents/?kind=sale")
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, doc.reference)
        self.assertContains(result, "Payment recorded by staff")
        self.assertNotContains(result, "Gateway verified")
        self.assertContains(self.client.get(f"/documents/{doc.pk}/"), "Payment recorded by staff")

    def test_part_sale_shows_debt_and_customer_details(self):
        doc = self.sale(
            payments=[{"method": "cash", "amount": "20.00"}],
            due_date=str(timezone.localdate() + timedelta(days=1)),
        )
        result = self.client.get("/documents/?kind=sale")
        self.assertContains(result, "Part payment + debt")
        self.assertContains(result, "30.00")
        details = self.client.get(f"/documents/{doc.pk}/")
        self.assertContains(details, "Current outstanding")
        self.assertContains(details, "GHS 30.00")

    def test_untrusted_history_flag_without_matching_payment_never_proves_provider(self):
        doc = self.sale()
        fake_ref = "KFD-POS-UNMATCHED"
        HeldSale.objects.create(
            user=self.user, branch=self.branch, label=LABEL_PREFIX + fake_ref,
            cart={"payment_request": {
                "status": "success", "document_id": str(doc.pk), "reference": fake_ref,
            }},
        )
        doc = decorate_sales(self.branch, [doc])[0]
        self.assertFalse(doc.paystack_verified_reference)


class PaymentFailureGuidanceTests(TestCase):
    def test_wallet_limit_has_actionable_message_without_false_confirmation(self):
        text = explain_provider_error("Payer has reached their limit")
        self.assertIn("spending", text)
        self.assertIn("network", text)
        self.assertNotIn("Payment confirmed", text)

    def test_unrelated_provider_response_keeps_diagnostic(self):
        self.assertEqual(explain_provider_error("Processor unavailable"), "Processor unavailable")
