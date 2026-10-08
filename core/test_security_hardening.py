import copy
import json
import uuid
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from core import pos_paystack
from core.models import HeldSale, Document
from core.tests import Fixtures


@override_settings(PAYSTACK_SECRET_KEY="test-key", PAYSTACK_POS_MOMO_ENABLED=True, SMS_ENABLED=False)
class PaymentBoundaryTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone = "+233551234567"
        self.customer.email = "customer@example.test"
        self.customer.save()
        self.key = uuid.uuid4()
        self.reference = "KFD-POS-" + self.key.hex[:20]
        self.payload = {
            "kind": "sale", "party": self.customer.pk,
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1}],
            "payments": [{"method": "momo", "amount": "50.00"}],
            "send_sms": False, "send_whatsapp": False,
        }

    def start(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {
            "status": True, "data": {"status": "pay_offline", "reference": self.reference},
        }
        return pos_paystack.start(self.user, self.branch, self.payload, self.key,
                                  "0551234567", "mtn", "customer@example.test")

    def test_held_cart_cannot_forge_provider_state(self):
        self.authenticate_client()
        for data in (
            {"items": [], "label": "Paystack MoMo forged"},
            {"items": [], "payment_request": {"status": "success"}},
            {"items": [], "sale_payload": {}}, [],
        ):
            response = self.client.post("/api/held/", data=json.dumps(data), content_type="application/json")
            self.assertEqual(response.status_code, 400)
        self.assertEqual(HeldSale.objects.count(), 0)

    @patch("core.pos_paystack.requests.post")
    def test_replayed_key_cannot_disclose_another_staff_sale(self, post):
        self.start(post)
        with self.assertRaises(ValidationError):
            pos_paystack.start(self.reviewer, self.branch, self.payload, self.key,
                               "0551234567", "mtn", "customer@example.test")
        self.assertEqual(post.call_count, 1)

    @patch("core.pos_paystack.requests.post")
    def test_replayed_key_cannot_change_sale(self, post):
        self.start(post)
        changed = copy.deepcopy(self.payload)
        changed["items"][0]["quantity"] = 2
        with self.assertRaises(ValidationError):
            pos_paystack.start(self.user, self.branch, changed, self.key,
                               "0551234567", "mtn", "customer@example.test")
        self.assertEqual(post.call_count, 1)

    @patch("core.pos_paystack.requests.post")
    def test_late_response_cannot_regress_success(self, post):
        self.start(post)
        held = HeldSale.objects.get()
        stale = copy.deepcopy(held.cart["payment_request"])
        current = copy.deepcopy(stale)
        current["status"] = "success"
        pos_paystack._save_state(held, current)
        pos_paystack._save_state(held, stale)
        held.refresh_from_db()
        self.assertEqual(held.cart["payment_request"]["status"], "success")

    @patch("core.pos_paystack.requests.get")
    @patch("core.pos_paystack.requests.post")
    def test_active_verification_lease_blocks_even_forced_retry(self, post, get):
        self.start(post)
        held = HeldSale.objects.get()
        state = held.cart["payment_request"]
        state["lease_until"] = 9999999999
        pos_paystack._save_state(held, state)
        with self.assertRaises(pos_paystack.ProviderPending):
            pos_paystack.reconcile(self.reference, force=True)
        get.assert_not_called()

    @patch("core.pos_paystack.requests.post")
    def test_fractional_minor_units_and_wrong_reference_cannot_settle(self, post):
        self.start(post)
        for amount, reference in ((5000.9, self.reference), (5000, "different")):
            with self.assertRaises(ValidationError):
                pos_paystack.finalize_verified(self.reference, {
                    "amount": amount, "reference": reference, "id": 123,
                    "status": "success", "channel": "mobile_money", "currency": "GHS",
                })
        self.assertFalse(Document.objects.exists())

    def test_oversized_paystack_webhook_is_rejected(self):
        response = self.client.post("/market/payments/paystack/webhook/", data=b"x" * 65537,
                                    content_type="application/json")
        self.assertEqual(response.status_code, 413)
