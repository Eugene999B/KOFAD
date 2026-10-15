"""Regression: Direct Debit authorizes future installments but NEVER posts a sale."""
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import services
from core.models import Document, Movement, Stock
from core.tests import Fixtures
from . import sale_intents, debit_api
from .models import DirectDebitSaleIntent, DebtMandate

READY = {
    "HUBTEL_API_ID": "offline-id",
    "HUBTEL_API_KEY": "offline-key",
    "HUBTEL_COLLECTION_ACCOUNT": "123456",
    "HUBTEL_DIRECT_EGRESS_CONFIRMED": True,
    "HUBTEL_DEBT_PREAPPROVAL_ENABLED": True,
    "HUBTEL_DEBT_AUTO_DEBIT_ENABLED": False,
    "HUBTEL_DIRECT_RECEIVE_ENABLED": False,
    "MARKET_SITE_ORIGIN": "https://market.kofadimpex.com",
    "SMS_ENABLED": False,
}


@override_settings(**READY)
class V2SaleIntentSafetyTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()
        self.customer.consent = True
        self.customer.phone = "0241234567"
        self.customer.save(update_fields=["consent", "phone"])
        self.first = timezone.localdate() + timedelta(days=7)
        self.end = self.first + timedelta(days=14)

    def request_data(self):
        return {
            "sale": {
                "kind": "sale", "party": self.customer.pk,
                "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 2}],
                "payments": [{"method": "cash", "amount": "20.00"}],
                "due_date": self.first.isoformat(),
                "direct_debit_v2": True, "send_sms": False,
                "send_whatsapp": False, "customer_consent": False,
            },
            "direct_debit": {
                "phone": "0241234567", "channel": "mtn-gh-direct-debit",
                "frequency": "weekly", "first_due_date": self.first.isoformat(),
                "final_due_date": self.end.isoformat(),
                "customer_requested_prompt": "yes", "customer_reviewed_schedule": "yes",
            }
        }

    def historical_pending_intent(self):
        # Emulates a pending provider authorization created by the PREVIOUS
        # production implementation. Reconciliation must NEVER post its sale.
        intent, created = sale_intents.prepare_intent(
            self.user, self.branch, self.request_data(), uuid.uuid4(),
        )
        self.assertTrue(created)
        with patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
            "provider_id": "provider-preapproved", "verification_type": "USSD",
            "otp_prefix": "", "status": "PENDING",
        }):
            intent = sale_intents.begin_preapproval(intent)
        self.assertEqual(intent.status, "pending")
        return intent

    def test_new_sale_is_simple_and_has_no_inline_direct_debit_payment(self):
        response = self.client.get("/sales/new/")
        self.assertEqual(response.status_code, 200)
        markup = response.content.decode("utf-8")
        self.assertIn("pos-simple-v3.css", markup)
        self.assertIn("Receipt messages", markup)
        self.assertIn("Record sale", markup)
        self.assertIn("Set up Direct Debit for this unpaid invoice", markup)
        self.assertNotIn('id="credit-repayment-mode"', markup)
        self.assertNotIn('id="credit-direct-debit-fields"', markup)
        self.assertNotIn('id="sale-debit-authorization"', markup)
        self.assertNotIn("Complete Sale &amp; Generate Receipt", markup)

    def test_combined_pos_endpoint_retired_without_side_effects(self):
        with patch("hubtel_direct.debit_api.initiate_preapproval") as gateway, \
             patch("hubtel_direct.debit_api.debit_charge") as charge:
            response = self.client.post(
                reverse("hubtel_v2_prepare_sale"),
                json.dumps(self.request_data()), content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(response.json()["sale_issued"])
        self.assertFalse(DirectDebitSaleIntent.objects.exists())
        self.assertFalse(Document.objects.filter(kind="sale").exists())
        self.assertFalse(Movement.objects.exists())
        gateway.assert_not_called()
        charge.assert_not_called()

    def test_old_pending_authorization_approval_never_posts_sale_or_receipt(self):
        older = Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("90.00"), paid=Decimal("0.00"),
            reference="PREEXISTING-OLD-INVOICE",
            due_date=self.first, created_by=self.user,
        )
        intent = self.historical_pending_intent()
        stock = Stock.objects.get(branch=self.branch, product=self.product).quantity
        with patch("hubtel_direct.debit_api.preapproval_status", return_value="APPROVED") as status, \
             patch("hubtel_direct.debit_api.debit_charge") as debit:
            result = sale_intents.reconcile_one(intent, force=True)
            result_again = sale_intents.reconcile_one(
                DirectDebitSaleIntent.objects.get(pk=intent.pk), force=True,
            )
        self.assertEqual(result.status, "approved")
        self.assertEqual(result_again.status, "approved")
        self.assertIsNotNone(result.provider_approved_at)
        self.assertIsNone(result.document_id)
        self.assertIsNone(result.mandate_id)
        self.assertEqual(Document.objects.filter(kind="sale").count(), 1)
        self.assertEqual(Movement.objects.count(), 0)
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, stock)
        self.assertEqual(services.balance(older), Decimal("90.00"))
        status.assert_called_once()
        debit.assert_not_called()

    def test_old_pending_rejected_does_not_issue_sale(self):
        intent = self.historical_pending_intent()
        with patch("hubtel_direct.debit_api.preapproval_status", return_value="REJECTED"):
            row = sale_intents.reconcile_one(intent, force=True)
        self.assertEqual(row.status, "rejected")
        self.assertFalse(Document.objects.filter(kind="sale").exists())
        self.assertFalse(Movement.objects.exists())

    def test_callback_is_only_a_hint_not_authorization(self):
        intent = self.historical_pending_intent()
        response = self.client.post(
            reverse("hubtel_v2_callback"),
            json.dumps({
                "ClientReferenceId": intent.client_reference,
                "HubtelPreapprovalId": intent.hubtel_preapproval_id,
                "PreapprovalStatus": "APPROVED",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        intent.refresh_from_db()
        self.assertEqual(intent.status, "pending")
        self.assertIsNone(intent.provider_approved_at)
        self.assertFalse(Document.objects.filter(kind="sale").exists())

    def test_normal_sale_endpoint_blocks_combined_legacy_flag(self):
        response = self.client.post(
            "/api/trades/",
            json.dumps(self.request_data()["sale"]),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Document.objects.count(), 0)

    def test_invoice_specific_collection_does_not_pay_unrelated_older_invoice(self):
        from core import debts as ledger
        older = Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("70.00"), paid=Decimal("0.00"),
            due_date=self.first, reference="OLD-FIRST", created_by=self.user,
        )
        newer = Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("100.00"), paid=Decimal("0.00"),
            due_date=self.end, reference="SIGNED-INVOICE", created_by=self.user,
        )
        document = ledger.post_customer_payment(self.user, self.branch, {
            "party": self.customer.pk, "invoice_id": str(newer.pk),
            "amount": "30.00", "method": "cash",
        }, key=str(uuid.uuid4()))
        self.assertEqual(document.allocations.count(), 1)
        self.assertEqual(document.allocations.get().invoice_id, newer.pk)
        self.assertEqual(services.balance(older), Decimal("70.00"))
        self.assertEqual(services.balance(newer), Decimal("70.00"))

    def test_direct_debit_for_existing_invoice_never_creates_second_sale(self):
        invoice = Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("100.00"), paid=Decimal("0.00"),
            due_date=self.end, reference="UNPAID-INVOICE", created_by=self.user,
        )
        initial_stock = Stock.objects.get(branch=self.branch, product=self.product).quantity
        with patch("hubtel_direct.debit_api.initiate_preapproval") as provider:
            response = self.client.post(
                reverse("hubtel_v2_attach_invoice", kwargs={"invoice_id": invoice.pk}), {
                    "phone": "0241234567", "channel": "mtn-gh-direct-debit",
                    "frequency": "weekly", "first_due_date": self.first.isoformat(),
                    "final_due_date": self.end.isoformat(),
                    "accepted_schedule": "yes", "customer_requested_prompt": "yes",
                },
            )
        self.assertEqual(response.status_code, 302)
        mandate = DebtMandate.objects.get()
        self.assertEqual(mandate.status, "draft")
        self.assertEqual(mandate.terms_snapshot["source_document_id"], str(invoice.pk))
        self.assertEqual(Document.objects.filter(kind="sale").count(), 1)
        self.assertFalse(Movement.objects.exists())
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, initial_stock)
        provider.assert_not_called()
