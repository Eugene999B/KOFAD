"""Hubtel cashier Direct Receive, verified wallet-name guard, and legacy history."""
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.core import signing
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core import pos_hubtel
from core.models import Document, HeldSale, Movement, Payment
from core.tests import Fixtures

DIRECT_READY = dict(
    HUBTEL_API_ID="test-id", HUBTEL_API_KEY="test-secret",
    HUBTEL_COLLECTION_ACCOUNT="123456", HUBTEL_CHECKOUT_ENABLED=False,
    HUBTEL_DIRECT_RECEIVE_ENABLED=True, HUBTEL_DIRECT_EGRESS_CONFIRMED=True,
    HUBTEL_TIMEOUT_SECONDS=5, MARKET_SITE_ORIGIN="https://market.kofadimpex.com",
    SMS_ENABLED=False,
)


@override_settings(**DIRECT_READY)
class StaffHubtelPaymentTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone = "+233551234567"
        self.customer.email = "customer@example.test"
        self.customer.save(update_fields=["phone", "email"])
        p = patch("core.pos_hubtel.hubtel.selected_provider", return_value="hubtel")
        p.start()
        self.addCleanup(p.stop)

    def proof(self, *, phone="233551234567", channel="mtn-gh", name="Verified Wallet Owner"):
        return signing.dumps({
            "user": self.user.pk, "branch": self.branch.pk, "phone": phone,
            "channel": channel, "name": name,
        }, salt="kofad-hubtel-staff-wallet-verify-v1")

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

    @staticmethod
    def accepted(url, **kwargs):
        payload = kwargs["json"]
        return Mock(status_code=200, json=lambda: {
            "ResponseCode": "0001",
            "Data": {"ClientReference": payload["ClientReference"], "TransactionId": "txn-test-123"},
        })

    def start(self, key=None, **kwargs):
        key = key or uuid.uuid4()
        result = pos_hubtel.start(
            self.user, self.branch, self.payload(), key,
            kwargs.get("phone", "0551234567"), kwargs.get("network", "mtn"),
            "customer@example.test", wallet_proof=kwargs.get("proof", self.proof()),
            customer_authorized=kwargs.get("customer_authorized", True),
        )
        return key, result

    def mature(self, key):
        held = HeldSale.objects.get(label=pos_hubtel.LABEL_PREFIX + key.hex)
        state = held.cart["payment_request"]
        state["created_at"] = (timezone.now() - timedelta(minutes=6)).timestamp()
        state["next_check_at"] = 0
        held.save(update_fields=["cart"])

    @patch("hubtel_direct.client.requests.post")
    def test_direct_start_sends_prompt_without_checkout_link_or_receipt(self, post):
        post.side_effect = self.accepted
        key, result = self.start()
        self.assertEqual(result["provider"], "hubtel")
        self.assertEqual(result["reference"], key.hex)
        self.assertEqual(result["authorization_url"], "")
        self.assertTrue(result["waiting"])
        self.assertFalse(result["paid"])
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(Movement.objects.count(), 0)
        self.assertIn("/receive/mobilemoney", post.call_args.args[0])
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["ClientReference"], key.hex)
        self.assertEqual(body["CustomerMsisdn"], "233551234567")
        self.assertEqual(body["CustomerName"], "Verified Wallet Owner")
        self.assertEqual(body["Channel"], "mtn-gh")
        self.assertEqual(body["Amount"], 50.0)

    @patch("hubtel_direct.client.requests.get")
    @patch("hubtel_direct.client.requests.post")
    def test_verified_walkin_full_payment_waits_for_confirmation(self, post, get):
        post.side_effect = self.accepted
        sale = self.payload()
        sale["party"] = None
        key = uuid.uuid4()
        result = pos_hubtel.start(
            self.user, self.branch, sale, key, "0551234567", "mtn", "",
            wallet_proof=self.proof(), customer_authorized=True,
        )
        self.assertTrue(result["waiting"])
        self.assertEqual(Document.objects.filter(kind="sale").count(), 0)
        held = HeldSale.objects.get(label=pos_hubtel.LABEL_PREFIX + key.hex)
        self.assertIsNone(held.cart["sale_payload"]["party"])
        self.mature(key)
        get.return_value = Mock(status_code=200, json=lambda: {
            "responseCode": "0000",
            "data": {"clientReference": key.hex, "transactionId": "txn-test-123",
                     "status": "Paid", "amount": 50.0, "paymentMethod": "mobilemoney"},
        })
        confirmed = pos_hubtel.reconcile(key.hex, force=True)
        self.assertTrue(confirmed["paid"])
        self.assertIsNone(Document.objects.get(kind="sale").party_id)
        self.assertEqual(Payment.objects.filter(document__kind="sale", method="momo").count(), 1)

    @patch("hubtel_direct.client.requests.post")
    def test_direct_receive_has_separate_read_only_verification_page(self, post):
        post.side_effect = self.accepted
        key, state = self.start()
        self.authenticate_client()
        url = "/payments/momo/" + key.hex + "/"
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Secure payment")
        self.assertContains(page, 'data-kpay-mode="payment"')
        self.assertContains(page, key.hex)
        self.assertNotContains(page, "Send Hubtel Direct")
        status = self.client.get(url + "?format=json")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "pending")
        self.assertFalse(status.json()["paid"])
        post.assert_called_once()

    @patch("hubtel_direct.client.requests.post")
    def test_duplicate_submit_idempotency_and_uncertain_outcome(self, post):
        post.side_effect = self.accepted
        key, _ = self.start()
        self.start(key)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(HeldSale.objects.filter(label=pos_hubtel.LABEL_PREFIX + key.hex).count(), 1)

    @patch("hubtel_direct.client.requests.post")
    def test_refuses_number_or_network_mismatch_before_provider(self, post):
        for data in ({"phone": "0241234567"}, {"network": "vod"},
                     {"proof": "not-a-signed-proof"}):
            with self.assertRaises(ValidationError):
                self.start(**data)
        self.assertEqual(post.call_count, 0)
        self.assertEqual(HeldSale.objects.count(), 0)

    @patch("hubtel_direct.client.requests.get")
    @patch("hubtel_direct.client.requests.post")
    def test_verified_paid_direct_status_posts_receipt_once(self, post, get):
        post.side_effect = self.accepted
        key, _ = self.start()
        self.mature(key)
        get.return_value = Mock(status_code=200, json=lambda: {
            "responseCode": "0000",
            "data": {"clientReference": key.hex, "transactionId": "txn-test-123",
                     "status": "Paid", "amount": 50.0, "paymentMethod": "mobilemoney"},
        })
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
        post.assert_called_once()
        get.assert_called_once()

    @patch("hubtel_direct.client.requests.get")
    @patch("hubtel_direct.client.requests.post")
    def test_amount_mismatch_never_posts_and_requires_manager(self, post, get):
        post.side_effect = self.accepted
        key, _ = self.start()
        self.mature(key)
        get.return_value = Mock(status_code=200, json=lambda: {
            "responseCode": "0000",
            "data": {"clientReference": key.hex, "transactionId": "txn-test-123",
                     "status": "Paid", "amount": 49.0, "paymentMethod": "mobilemoney"},
        })
        result = pos_hubtel.reconcile(key.hex, force=True)
        self.assertEqual(result["status"], "attention")
        self.assertEqual(Document.objects.count(), 0)

    @patch("hubtel_direct.client.requests.get")
    @patch("hubtel_direct.client.requests.post")
    def test_unpaid_remains_pending_without_receipt(self, post, get):
        post.side_effect = self.accepted
        key, _ = self.start()
        self.mature(key)
        get.return_value = Mock(status_code=200, json=lambda: {
            "responseCode": "0000",
            "data": {"clientReference": key.hex, "transactionId": "txn-test-123",
                     "status": "Unpaid", "amount": 50.0, "paymentMethod": "mobilemoney"},
        })
        result = pos_hubtel.reconcile(key.hex, force=True)
        self.assertFalse(result["paid"])
        self.assertTrue(result["waiting"])
        self.assertEqual(Document.objects.count(), 0)

    @patch("hubtel_direct.client.requests.post")
    def test_staff_start_endpoint_requires_verification_proof(self, post):
        post.side_effect = self.accepted
        self.authenticate_client()
        key = uuid.uuid4()
        payload = {
            "sale": self.payload(), "phone": "0551234567", "provider": "mtn",
            "payment_gateway": "hubtel", "request_key": str(key),
        }
        response = self.client.post(
            "/api/pos/paystack-momo/start/",
            data=json.dumps(payload), content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(key),
        )
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()
        payload["wallet_proof"] = self.proof()
        payload["customer_authorized"] = True
        response = self.client.post(
            "/api/pos/paystack-momo/start/", data=json.dumps(payload),
            content_type="application/json", HTTP_IDEMPOTENCY_KEY=str(key),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["authorization_url"], "")
        post.assert_called_once()

    @patch("hubtel_direct.verification.requests.get")
    def test_staff_auto_name_lookup_requires_no_sms_code(self, get):
        self.authenticate_client()
        get.return_value = Mock(status_code=200, json=lambda: {
            "ResponseCode": "0000",
            "Data": {"IsRegistered": True, "Name": "NAME FROM HUBTEL", "Status": "active"},
        })
        result = self.client.post(
            "/api/pos/hubtel/wallet-name/",
            data=json.dumps({"phone": "0551234567", "channel": "mtn-gh"}),
            content_type="application/json",
        )
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["name"], "NAME FROM HUBTEL")
        self.assertTrue(result.json()["verification_token"])
        self.assertIn("rnv.hubtel.com", get.call_args.args[0])

    @patch("hubtel_direct.client.requests.post")
    def test_staff_cannot_send_charge_without_explicit_customer_approval(self, post):
        with self.assertRaises(ValidationError):
            self.start(customer_authorized=False)
        post.assert_not_called()

    @patch("hubtel_direct.verification.requests.get")
    def test_wallet_lookup_rejects_unknown_network(self, get):
        self.authenticate_client()
        response = self.client.post(
            "/api/pos/hubtel/wallet-name/",
            data=json.dumps({"phone": "0551234567", "channel": "wrong"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        get.assert_not_called()
