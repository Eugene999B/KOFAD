"""Direct Debit V2: offline regression, NEVER an actual Hubtel money movement."""
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
from . import sale_intents, mandates, debit_api
from .models import DirectDebitSaleIntent, DebtMandate, DebtDebitAttempt

READY = {
    "HUBTEL_API_ID": "offline-api-id",
    "HUBTEL_API_KEY": "offline-api-key",
    "HUBTEL_COLLECTION_ACCOUNT": "123456",
    "HUBTEL_DIRECT_EGRESS_CONFIRMED": True,
    "HUBTEL_DEBT_PREAPPROVAL_ENABLED": True,
    "HUBTEL_DEBT_AUTO_DEBIT_ENABLED": False,
    "HUBTEL_DIRECT_RECEIVE_ENABLED": False,
    "MARKET_SITE_ORIGIN": "https://market.kofadimpex.com",
    "SMS_ENABLED": False,
}


@override_settings(**READY)
class V2SaleIntentTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()
        self.customer.consent = True
        self.customer.phone = "0241234567"
        self.customer.save(update_fields=["consent", "phone"])
        self.key = str(uuid.uuid4())
        self.first = timezone.localdate() + timedelta(days=7)
        self.end = self.first + timedelta(days=14)

    def data(self):
        return {
            "sale": {
                "kind": "sale", "party": self.customer.pk,
                "items": [{"product": self.product.pk, "mode": "retail_unit",
                           "quantity": 2}],
                "payments": [{"method": "cash", "amount": "20.00"}],
                "due_date": self.first.isoformat(),
                "customer_consent": True, "send_sms": False, "send_whatsapp": False,
                "direct_debit_v2": True,
            },
            "direct_debit": {
                "phone": "0241234567", "channel": "mtn-gh-direct-debit",
                "frequency": "weekly", "first_due_date": self.first.isoformat(),
                "final_due_date": self.end.isoformat(),
                "customer_requested_prompt": "yes", "customer_reviewed_schedule": "yes",
            }
        }

    def submit(self, data=None, key=None):
        return self.client.post(
            reverse("hubtel_v2_prepare_sale"),
            json.dumps(data or self.data()),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key or self.key,
        )

    def test_preapproval_does_not_issue_sale_stock_or_receipt_and_is_idempotent(self):
        with patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
            "provider_id": "offline-hubtel-id", "verification_type": "USSD",
            "otp_prefix": "", "status": "PENDING"
        }) as initiate, patch("hubtel_direct.debit_api.debit_charge") as debit:
            a = self.submit()
            b = self.submit()
        self.assertEqual(a.status_code, 202, a.content)
        self.assertEqual(b.status_code, 202, b.content)
        self.assertEqual(a.json()["intent_id"], b.json()["intent_id"])
        self.assertEqual(a.json()["status"], "pending")
        self.assertFalse(a.json()["sale_issued"])
        self.assertEqual(DirectDebitSaleIntent.objects.count(), 1)
        self.assertFalse(Document.objects.filter(kind="sale").exists())
        self.assertFalse(Movement.objects.exists())
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 240)
        initiate.assert_called_once()
        debit.assert_not_called()

    def test_prior_older_invoice_does_not_block_new_scoped_sale_or_receive_its_collections(self):
        older = Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("90.00"), paid=Decimal("0.00"),
            due_date=self.first, reference="OLD-INVOICE-V2",
            created_by=self.user,
        )
        with patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
            "provider_id": "offline-authority", "verification_type": "USSD",
            "otp_prefix": "", "status": "PENDING",
        }):
            response = self.submit()
        self.assertEqual(response.status_code, 202, response.content)
        intent = DirectDebitSaleIntent.objects.get()
        self.assertEqual(intent.outstanding, Decimal("80.00"))
        self.assertFalse(intent.document_id)
        with patch("hubtel_direct.debit_api.preapproval_status", return_value="APPROVED"), \
             patch("hubtel_direct.debit_api.debit_charge") as charge:
            result = sale_intents.reconcile_one(intent, force=True)
        self.assertEqual(result.status, "issued")
        self.assertTrue(result.document_id)
        self.assertTrue(result.mandate_id)
        mandate = DebtMandate.objects.get(pk=result.mandate_id)
        self.assertEqual(mandate.maximum_total, Decimal("80.00"))
        self.assertEqual(mandate.status, "approved")
        self.assertIsNotNone(mandate.provider_approved_at)
        self.assertEqual(mandate.client_reference, intent.client_reference)
        self.assertEqual(mandates.mandate_outstanding(mandate), Decimal("80.00"))
        self.assertEqual(services.balance(older), Decimal("90.00"))
        self.assertEqual(Document.objects.filter(kind="sale").count(), 2)
        self.assertEqual(Movement.objects.count(), 1)
        charge.assert_not_called()
        again = sale_intents.reconcile_one(DirectDebitSaleIntent.objects.get(pk=intent.pk), force=True)
        self.assertEqual(again.status, "issued")
        self.assertEqual(Movement.objects.count(), 1)

    def test_new_customer_preview_does_not_create_customer_before_wallet_approval(self):
        from core.models import Party
        data = self.data()
        data["sale"]["party"] = None
        data["sale"]["customer_name"] = "Fresh V2 Customer"
        data["sale"]["customer_phone"] = "0559876543"
        with patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
            "provider_id": "new-customer-preapproval", "verification_type": "USSD",
            "otp_prefix": "", "status": "PENDING",
        }):
            result = self.submit(data)
        self.assertEqual(result.status_code, 202, result.content)
        intent = DirectDebitSaleIntent.objects.get()
        self.assertIsNone(intent.party_id)
        self.assertFalse(Party.objects.filter(name="Fresh V2 Customer").exists())
        self.assertFalse(Document.objects.filter(kind="sale").exists())

    def test_invoice_specific_collection_does_not_clear_older_customer_debt(self):
        from core import debts as ledger
        older = Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("70.00"), paid=Decimal("0.00"),
            due_date=self.first, reference="V2-OLD-FIRST",
            created_by=self.user,
        )
        new = Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("100.00"), paid=Decimal("0.00"),
            due_date=self.end, reference="V2-NEW-SIGNED",
            created_by=self.user,
        )
        paid = ledger.post_customer_payment(
            self.user, self.branch, {
                "party": self.customer.pk, "invoice_id": str(new.pk),
                "amount": "30.00", "method": "cash",
                "note": "Specific invoice only",
            }, key=str(uuid.uuid4()),
        )
        self.assertEqual(services.balance(older), Decimal("70.00"))
        self.assertEqual(services.balance(new), Decimal("70.00"))
        self.assertEqual(paid.allocations.count(), 1)
        self.assertEqual(paid.allocations.get().invoice_id, new.pk)

    def test_existing_invoice_recovery_never_issues_another_sale(self):
        existing = Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("100.00"), paid=Decimal("0.00"),
            due_date=self.end, reference="RECOVER-PREVIOUSLY-ISSUED",
            created_by=self.user,
        )
        before = Stock.objects.get(branch=self.branch, product=self.product).quantity
        with patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
            "provider_id": "recover-authority", "verification_type": "USSD",
            "otp_prefix": "", "status": "PENDING",
        }) as hubtel:
            response = self.client.post(
                reverse("hubtel_v2_attach_invoice", kwargs={"invoice_id": existing.pk}),
                {
                    "phone": "0241234567", "channel": "mtn-gh-direct-debit",
                    "frequency": "weekly", "first_due_date": self.first.isoformat(),
                    "final_due_date": self.end.isoformat(),
                    "accepted_schedule": "yes", "customer_requested_prompt": "yes",
                },
            )
        self.assertEqual(response.status_code, 302)
        plan = DebtMandate.objects.get()
        self.assertEqual(plan.maximum_total, Decimal("100.00"))
        self.assertEqual(plan.terms_snapshot["source_document_id"], str(existing.pk))
        self.assertEqual(Document.objects.filter(kind="sale").count(), 1)
        self.assertEqual(Movement.objects.count(), 0)
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, before)
        hubtel.assert_not_called()
        self.assertEqual(plan.status, "draft")
        self.assertIn(str(plan.pk), response.url)

    def test_refused_preapproval_does_not_issue_sale(self):
        with patch("hubtel_direct.debit_api.initiate_preapproval",
                   side_effect=debit_api.ProviderDenied("Hubtel IP not allowlisted")):
            response = self.submit()
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json()["status"], "rejected")
        self.assertFalse(Document.objects.filter(kind="sale").exists())
        self.assertEqual(Movement.objects.count(), 0)

    def test_unconfirmed_outcome_keeps_reference_and_never_repeats_post(self):
        with patch("hubtel_direct.debit_api.initiate_preapproval",
                   side_effect=Exception("Transport interruption")) as prompt:
            a = self.submit()
            b = self.submit()
        self.assertEqual(a.json()["status"], "unknown")
        self.assertEqual(b.json()["status"], "unknown")
        prompt.assert_called_once()
        self.assertFalse(Document.objects.filter(kind="sale").exists())

    def test_ordinary_pos_api_forbids_v2_fallback_without_sale(self):
        response = self.client.post(
            "/api/trades/", json.dumps(self.data()["sale"]),
            content_type="application/json", HTTP_IDEMPOTENCY_KEY=self.key,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Document.objects.filter(kind="sale").exists())

    def test_callback_is_only_advisory_not_wallet_approval(self):
        with patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
            "provider_id": "hubtel-callback-id", "verification_type": "USSD",
            "otp_prefix": "", "status": "PENDING"
        }):
            self.submit()
        intent = DirectDebitSaleIntent.objects.get()
        callback = self.client.post(
            reverse("hubtel_v2_callback"),
            json.dumps({"ClientReferenceId": intent.client_reference,
                        "HubtelPreapprovalId": "hubtel-callback-id",
                        "PreapprovalStatus": "APPROVED"}),
            content_type="application/json",
        )
        self.assertEqual(callback.status_code, 200)
        intent.refresh_from_db()
        self.assertEqual(intent.status, "pending")
        self.assertIsNone(intent.provider_approved_at)
        self.assertFalse(Document.objects.filter(kind="sale").exists())
