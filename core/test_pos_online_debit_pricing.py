"""Staff cash/MoMo/Direct Debit prices use the company percentage safely."""
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import Document, Payment
from core.tests import Fixtures
from marketplace.models import PaymentConfiguration


class PosOnlineDebitPricingTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()
        self.customer.phone = "+233241234567"
        self.customer.save(update_fields=["phone"])
        PaymentConfiguration.objects.create(online_price_markup_percent=Decimal("2.500"))

    def payload(self, *, route="direct_debit", due_date=True, payments=None):
        result = {
            "kind": "sale",
            "party": self.customer.pk,
            "customer_consent": False,
            "send_sms": False,
            "send_whatsapp": False,
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 2}],
            "payments": [{"method": "cash", "amount": "0"}] if payments is None else payments,
            "due_date": (timezone.localdate() + timedelta(days=7)).isoformat() if due_date else "",
        }
        if route:
            result["payment_pricing"] = route
        return result

    def post(self, payload):
        return self.client.post(
            "/api/trades/", data=json.dumps(payload), content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

    @patch("hubtel_direct.debit_api.initiate_preapproval")
    def test_direct_debit_selection_uses_online_price_without_collecting_money(self, provider):
        result = self.post(self.payload())
        self.assertEqual(result.status_code, 200, result.content)
        row = Document.objects.get(pk=result.json()["document_id"])
        self.assertEqual(row.total, Decimal("102.50"))
        self.assertEqual(row.paid, Decimal("0.00"))
        self.assertEqual(row.lines.get().unit_price, Decimal("51.25"))
        self.assertEqual(Payment.objects.filter(document=row).count(), 0)
        self.assertFalse(result.json()["sms_requested"])
        provider.assert_not_called()

    def test_cash_and_manual_credit_remain_normal_prices(self):
        cash = self.post(self.payload(route="", due_date=False, payments=[
            {"method": "cash", "amount": "100.00"},
        ]))
        self.assertEqual(cash.status_code, 200, cash.content)
        self.assertEqual(Decimal(cash.json()["total"]), Decimal("100.00"))
        manual_credit = self.post(self.payload(route=""))
        self.assertEqual(manual_credit.status_code, 200, manual_credit.content)
        self.assertEqual(Decimal(manual_credit.json()["total"]), Decimal("100.00"))
        self.assertEqual(Decimal(manual_credit.json()["paid"]), Decimal("0.00"))

    def test_debit_pricing_never_allows_a_fake_cash_or_momo_payment(self):
        for method in ("cash", "momo", "bank", "card"):
            with self.subTest(method=method):
                result = self.post(self.payload(payments=[
                    {"method": method, "amount": "1.00"},
                ]))
                self.assertEqual(result.status_code, 400, result.content)
        self.assertEqual(Document.objects.count(), 0)

    def test_debit_route_requires_identified_customer_and_due_date(self):
        wrong = self.payload()
        wrong["party"] = None
        self.assertEqual(self.post(wrong).status_code, 400)
        self.assertEqual(self.post(self.payload(due_date=False)).status_code, 400)
        self.assertEqual(self.post(self.payload(route="other")).status_code, 400)
        self.assertEqual(Document.objects.count(), 0)

    def test_existing_credit_invoice_is_frozen_after_percentage_change(self):
        first = self.post(self.payload())
        self.assertEqual(first.status_code, 200, first.content)
        key = first.json()["document_id"]
        PaymentConfiguration.objects.filter(pk=1).update(
            online_price_markup_percent=Decimal("4.000")
        )
        old = Document.objects.get(pk=key)
        self.assertEqual(old.total, Decimal("102.50"))
        next_sale = self.post(self.payload())
        self.assertEqual(next_sale.status_code, 200, next_sale.content)
        self.assertEqual(Decimal(next_sale.json()["total"]), Decimal("104.00"))

    def test_staff_screen_exposes_online_rate_and_debit_selection(self):
        response = self.client.get("/sales/new/")
        self.assertEqual(response.status_code, 200, response.content[:250])
        self.assertContains(response, 'data-online-markup-percent="2.500"')
        self.assertContains(response, 'value="direct_debit"')
        self.assertContains(response, "Direct Debit / Pay later")
