"""POS MoMo: recipient review, manual verification, history, and permissions."""
import json
import uuid
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from . import pos_paystack
from .models import HeldSale, Document
from .tests import Fixtures
from marketplace.models import PaymentConfiguration


@override_settings(PAYSTACK_SECRET_KEY="sk_test_pos", PAYSTACK_POS_MOMO_ENABLED=True,
                   PAYSTACK_TIMEOUT_SECONDS=5, SMS_ENABLED=False)
class PosMomoPaymentFlowTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()
        PaymentConfiguration.objects.update_or_create(pk=1, defaults={"provider": "paystack"})
        self.customer.phone = "+233551234567"
        self.customer.save(update_fields=["phone"])

    def _payload(self):
        return {
            "kind": "sale",
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1}],
            "party": self.customer.pk,
            "customer_consent": False,
            "send_sms": False,
            "send_whatsapp": False,
            "payments": [{"method": "momo", "amount": "50.00"}],
        }

    def _review(self, key, phone="0551234567"):
        return self.client.post("/api/pos/paystack-momo/recipient-review/",
            data=json.dumps({
                "phone": phone, "provider": "mtn", "request_key": str(key),
                "party": self.customer.pk, "name": self.customer.name,
            }), content_type="application/json")

    def _start(self, key, *, token=None, phone="0551234567", confirmed=True):
        return self.client.post("/api/pos/paystack-momo/start/",
            data=json.dumps({
                "sale": self._payload(), "phone": phone, "email": "",
                "provider": "mtn", "payment_gateway": "paystack",
                "request_key": str(key), "recipient_confirmed": confirmed,
                "recipient_review_token": token or "",
            }), content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(key))

    @patch("core.pos_paystack.requests.post")
    def test_verify_customer_never_claims_paystack_wallet_name(self, charge):
        key = uuid.uuid4()
        response = self._review(key)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["customer_name"], self.customer.name)
        self.assertIsNone(data["registered_wallet_name"])
        self.assertFalse(data["wallet_name_verified"])
        self.assertTrue(data["review_token"])
        charge.assert_not_called()

    @patch("core.pos_paystack.requests.post")
    def test_charge_requires_unexpired_matching_review_and_customer_consent(self, charge):
        key = uuid.uuid4()
        token = self._review(key).json()["review_token"]
        self.assertEqual(self._start(key, token=None).status_code, 400)
        self.assertEqual(self._start(key, token=token, confirmed=False).status_code, 400)
        self.assertEqual(self._start(key, token=token, phone="0241234567").status_code, 400)
        self.assertEqual(self._start(uuid.uuid4(), token=token).status_code, 400)
        charge.assert_not_called()
        self.assertEqual(Document.objects.count(), 0)

    @patch("core.pos_paystack.requests.post")
    def test_pending_manual_verify_and_paid_receipts(self, charge):
        key = uuid.uuid4()
        reference = pos_paystack._reference_from_key(key)
        charge.return_value = Mock(status_code=200, json=lambda: {
            "status": True, "data": {"reference": reference, "status": "pay_offline",
                                    "display_text": "Approve on your phone."},
        })
        token = self._review(key).json()["review_token"]
        started = self._start(key, token=token)
        self.assertEqual(started.status_code, 200, started.content)
        self.assertEqual(started.json()["reference"], reference)
        self.assertEqual(charge.call_count, 1)
        self.assertEqual(Document.objects.count(), 0)
        status_page = self.client.get(f"/payments/momo/{reference}/")
        self.assertEqual(status_page.status_code, 200)
        self.assertContains(status_page, "Awaiting approval")
        self.assertContains(status_page, 'class="momo-receipt hidden"')
        history = self.client.get("/payments/momo/?status=pending")
        self.assertContains(history, reference)
        self.assertContains(self.client.get("/online-payments/"), reference)

        with patch("core.pos_paystack.verify", return_value={
            "id": 123456, "status": "success", "reference": reference,
            "amount": 5000, "currency": "GHS", "channel": "mobile_money",
        }), patch("core.paystack_challenges.charge_step",
                  side_effect=ValidationError("Awaiting network reply.")):
            verification = self.client.post(f"/payments/momo/{reference}/verify/")
        self.assertEqual(verification.status_code, 302)
        self.assertEqual(Document.objects.count(), 1)
        paid_page = self.client.get(f"/payments/momo/{reference}/")
        self.assertContains(paid_page, "Paid and posted")
        self.assertContains(paid_page, "/pdf/thermal80/")
        self.assertEqual(
            self.client.get(f"/payments/momo/{reference}/?format=json").json()["paid"], True
        )
        self.assertContains(self.client.get("/payments/momo/?status=paid"), reference)
        with patch("core.pos_paystack.verify") as verify_again:
            self.client.post(f"/payments/momo/{reference}/verify/")
        verify_again.assert_not_called()
        self.assertEqual(charge.call_count, 1)
        self.assertEqual(Document.objects.count(), 1)

    def test_history_is_scoped_to_active_branch(self):
        key = uuid.uuid4()
        reference = pos_paystack._reference_from_key(key)
        HeldSale.objects.create(
            user=self.user, branch=self.other, label=pos_paystack.LABEL_PREFIX+reference,
            cart={"sale_payload": self._payload(), "payment_request": {
                "reference": reference, "status": "pending", "amount": "50.00",
                "phone": "+233551234567", "network": "mtn",
            }},
        )
        self.assertNotContains(self.client.get("/payments/momo/"), reference)
        self.assertEqual(self.client.get(f"/payments/momo/{reference}/").status_code, 404)

    def test_terminal_failure_is_not_represented_as_payment_or_receipt(self):
        key = uuid.uuid4()
        reference = pos_paystack._reference_from_key(key)
        HeldSale.objects.create(user=self.user, branch=self.branch,
            label=pos_paystack.LABEL_PREFIX+reference,
            cart={"sale_payload": self._payload(), "payment_request": {
                "reference": reference, "status": "failed", "amount": "50.00",
                "phone": "+233551234567", "network": "mtn",
                "message": "Customer declined the payment.",
            }})
        status = self.client.get(f"/payments/momo/{reference}/?format=json")
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["paid"])
        self.assertFalse(status.json()["receipt"])
        self.assertEqual(status.json()["display_status"], "Failed")
        self.assertContains(self.client.get("/payments/momo/?status=failed"), reference)
