"""Cashier Hubtel payments require independent merchant verification."""
import json
import uuid
from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from core import pos_hubtel
from core.models import Document, HeldSale, Movement, Payment
from core.tests import Fixtures


@override_settings(
    HUBTEL_API_ID="test-id", HUBTEL_API_KEY="test-secret",
    HUBTEL_COLLECTION_ACCOUNT="123456", HUBTEL_CHECKOUT_ENABLED=True,
    HUBTEL_TIMEOUT_SECONDS=5, SMS_ENABLED=False,
)
class StaffHubtelPaymentTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone = "+233551234567"
        self.customer.email = "customer@example.test"
        self.customer.save(update_fields=["phone", "email"])
        for target, value in [
            ("core.pos_hubtel.hubtel.selected_provider", "hubtel"),
            ("core.pos_hubtel.hubtel.ready", True),
        ]:
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def payload(self):
        return {
            "kind": "sale",
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1}],
            "party": self.customer.pk, "customer_consent": False,
            "send_sms": False, "send_whatsapp": False, "due_date": "",
            "payments": [
                {"method": "cash", "amount": "0"},
                {"method": "momo", "amount": "50.00"},
                {"method": "bank", "amount": "0"},
                {"method": "card", "amount": "0"},
            ],
        }

    def initiate(self, reference):
        obj = Mock(status_code=200)
        obj.json.return_value = {"responseCode": "0000", "data": {
            "checkoutId": "checkout-test", "clientReference": reference,
            "checkoutUrl": "https://pay.hubtel.com/secure/" + reference,
        }}
        return obj

    def verified(self, reference, *, amount="50.00", channel="MobileMoney"):
        return {
            "status": "Paid", "amount": amount, "currencyCode": "GHS",
            "transactionId": "txn-test-123", "clientReference": reference,
            "paymentMethod": channel,
        }

    @patch("core.pos_hubtel.hubtel.headers", return_value={"Authorization": "Basic test"})
    @patch("core.pos_hubtel.requests.post")
    def test_start_creates_link_but_no_sale(self, post, _):
        key = uuid.uuid4()
        post.return_value = self.initiate(key.hex)
        result = pos_hubtel.start(self.user, self.branch, self.payload(), key,
                                  "0551234567", "mtn", "customer@example.test")
        self.assertEqual(result["provider"], "hubtel")
        self.assertEqual(result["reference"], key.hex)
        self.assertTrue(result["authorization_url"].startswith("https://pay.hubtel.com/"))
        self.assertTrue(result["waiting"])
        self.assertFalse(result["paid"])
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(Movement.objects.count(), 0)
        self.assertEqual(post.call_args.kwargs["json"]["clientReference"], key.hex)
        self.assertEqual(post.call_args.kwargs["json"]["totalAmount"], 50.0)

    @patch("core.pos_hubtel.hubtel.headers", return_value={})
    @patch("core.pos_hubtel.requests.post")
    def test_same_key_never_creates_second_checkout(self, post, _):
        key = uuid.uuid4()
        post.return_value = self.initiate(key.hex)
        for unused in range(2):
            pos_hubtel.start(self.user, self.branch, self.payload(), key,
                             "0551234567", "mtn", "customer@example.test")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(HeldSale.objects.filter(label=pos_hubtel.LABEL_PREFIX + key.hex).count(), 1)

    @patch("core.pos_hubtel.hubtel.verify")
    @patch("core.pos_hubtel.hubtel.headers", return_value={})
    @patch("core.pos_hubtel.requests.post")
    def test_verified_payment_posts_once_and_references_provider(self, post, _, verify):
        key = uuid.uuid4()
        post.return_value = self.initiate(key.hex)
        pos_hubtel.start(self.user, self.branch, self.payload(), key,
                         "0551234567", "mtn", "customer@example.test")
        verify.return_value = self.verified(key.hex)
        first = pos_hubtel.reconcile(key.hex, force=True)
        second = pos_hubtel.reconcile(key.hex, force=True)
        self.assertTrue(first["paid"])
        self.assertTrue(second["paid"])
        self.assertEqual(Document.objects.filter(kind="sale").count(), 1)
        self.assertEqual(Movement.objects.count(), 1)
        receipt = Document.objects.get(kind="sale")
        payment = Payment.objects.get(document=receipt, method="momo")
        self.assertEqual(payment.amount, Decimal("50.00"))
        self.assertEqual(payment.reference, key.hex)

    @patch("core.pos_hubtel.hubtel.verify")
    @patch("core.pos_hubtel.hubtel.headers", return_value={})
    @patch("core.pos_hubtel.requests.post")
    def test_mismatched_amount_or_card_channel_never_posts(self, post, _, verify):
        for amount, method in [("49.00", "MobileMoney"), ("50.00", "Card")]:
            key = uuid.uuid4()
            post.return_value = self.initiate(key.hex)
            pos_hubtel.start(self.user, self.branch, self.payload(), key,
                             "0551234567", "mtn", "customer@example.test")
            verify.return_value = self.verified(key.hex, amount=amount, channel=method)
            with self.assertRaises(ValidationError):
                pos_hubtel.reconcile(key.hex, force=True)
            held = HeldSale.objects.get(label=pos_hubtel.LABEL_PREFIX + key.hex)
            self.assertEqual(held.cart["payment_request"]["status"], "attention")
        self.assertEqual(Document.objects.count(), 0)

    @patch("core.pos_hubtel.hubtel.verify")
    @patch("core.pos_hubtel.hubtel.headers", return_value={})
    @patch("core.pos_hubtel.requests.post")
    def test_worker_recovers_payment_without_staff_browser(self, post, _, verify):
        key = uuid.uuid4()
        post.return_value = self.initiate(key.hex)
        pos_hubtel.start(self.user, self.branch, self.payload(), key,
                         "0551234567", "mtn", "customer@example.test")
        held = HeldSale.objects.get(label=pos_hubtel.LABEL_PREFIX + key.hex)
        held.cart["payment_request"]["next_check_at"] = 0
        held.save(update_fields=["cart"])
        verify.return_value = self.verified(key.hex)
        self.assertEqual(pos_hubtel.reconcile_due(), 1)
        self.assertEqual(Document.objects.filter(kind="sale").count(), 1)

    @patch("core.pos_hubtel.hubtel.headers", return_value={})
    @patch("core.pos_hubtel.requests.post")
    def test_rejects_untrusted_checkout_redirect_domain(self, post, _):
        key = uuid.uuid4()
        obj = self.initiate(key.hex)
        obj.json.return_value["data"]["checkoutUrl"] = "https://attacker.example/checkout"
        post.return_value = obj
        result = pos_hubtel.start(self.user, self.branch, self.payload(), key,
                                  "0551234567", "mtn", "customer@example.test")
        self.assertEqual(result["status"], "submission_unknown")
        self.assertFalse(result["authorization_url"])
        self.assertEqual(Document.objects.count(), 0)

    @patch("core.pos_hubtel.hubtel.headers", return_value={})
    @patch("core.pos_hubtel.requests.post")
    def test_staff_start_api_respects_shared_gateway(self, post, _):
        self.authenticate_client()
        key = uuid.uuid4()
        post.return_value = self.initiate(key.hex)
        response = self.client.post(
            "/api/pos/paystack-momo/start/",
            data=json.dumps({
                "sale": self.payload(), "phone": "0551234567",
                "provider": "mtn", "payment_gateway": "hubtel",
                "email": "", "request_key": str(key),
            }),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(key),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["provider"], "hubtel")
        self.assertTrue(response.json()["authorization_url"].startswith("https://pay.hubtel.com/"))
        self.assertFalse(response.json()["paid"])
        self.assertEqual(Document.objects.count(), 0)

    @patch("core.pos_hubtel.hubtel.headers", return_value={})
    @patch("core.pos_hubtel.requests.post")
    def test_existing_hubtel_checkout_remains_hubtel_after_settings_switch(self, post, _):
        self.authenticate_client()
        key = uuid.uuid4()
        post.return_value = self.initiate(key.hex)
        request = {
            "sale": self.payload(), "phone": "0551234567",
            "provider": "mtn", "payment_gateway": "hubtel",
            "email": "", "request_key": str(key),
        }
        first = self.client.post(
            "/api/pos/paystack-momo/start/", data=json.dumps(request),
            content_type="application/json", HTTP_IDEMPOTENCY_KEY=str(key),
        )
        self.assertEqual(first.status_code, 200, first.content)
        with patch("marketplace.hubtel.selected_provider", return_value="paystack"):
            second = self.client.post(
                "/api/pos/paystack-momo/start/", data=json.dumps(request),
                content_type="application/json", HTTP_IDEMPOTENCY_KEY=str(key),
            )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()["reference"], key.hex)
        self.assertEqual(second.json()["provider"], "hubtel")
        self.assertEqual(post.call_count, 1)
