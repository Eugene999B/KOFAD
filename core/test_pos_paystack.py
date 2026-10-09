import json
import uuid
from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from core import pos_paystack
from core.models import Document, HeldSale, Movement, Payment, Stock
from core.tests import Fixtures


SETTINGS = dict(
    PAYSTACK_SECRET_KEY="sk_test_pos",
    PAYSTACK_POS_MOMO_ENABLED=True,
    PAYSTACK_TIMEOUT_SECONDS=5,
    SMS_ENABLED=False,
)


@override_settings(**SETTINGS)
class PosPaystackMomoTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone = "+233551234567"
        self.customer.email = "customer@example.test"
        self.customer.save(update_fields=["phone", "email"])

    def payload(self):
        return {
            "kind": "sale",
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1}],
            "party": self.customer.pk,
            "customer_consent": False,
            "send_sms": False,
            "send_whatsapp": False,
            "due_date": "",
            "payments": [
                {"method": "cash", "amount": "0"},
                {"method": "momo", "amount": "50.00"},
                {"method": "bank", "amount": "0"},
                {"method": "card", "amount": "0"},
            ],
        }

    def charge_response(self, reference):
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": True,
            "message": "Charge attempted",
            "data": {
                "reference": reference,
                "status": "pay_offline",
                "display_text": "Please complete authorization process on your mobile phone",
            },
        }
        return response

    def verify_response(self, reference, *, amount=5000, status="success"):
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": True,
            "message": "Verification successful",
            "data": {
                "id": 12345,
                "reference": reference,
                "status": status,
                "amount": amount,
                "currency": "GHS",
                "channel": "mobile_money",
                "gateway_response": "Successful",
            },
        }
        return response

    @patch("core.pos_paystack.requests.post")
    def test_start_requests_momo_without_posting_sale(self, post):
        key = uuid.uuid4()
        reference = "KFD-POS-" + key.hex[:20]
        post.return_value = self.charge_response(reference)

        result = pos_paystack.start(
            self.user, self.branch, self.payload(), key,
            "0551234567", "mtn", "customer@example.test",
        )

        self.assertEqual(result["reference"], reference)
        self.assertTrue(result["waiting"])
        self.assertFalse(result["paid"])
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(Movement.objects.count(), 0)
        self.assertEqual(HeldSale.objects.filter(label=pos_paystack.LABEL_PREFIX + reference).count(), 1)
        self.assertEqual(post.call_args.kwargs["json"]["amount"], "5000")
        self.assertEqual(post.call_args.kwargs["json"]["currency"], "GHS")
        self.assertEqual(post.call_args.kwargs["json"]["mobile_money"]["provider"], "mtn")


    @patch("core.pos_paystack.requests.post")
    def test_walk_in_momo_without_customer_email_uses_processor_only_alias(self, post):
        self.customer.email = ""
        self.customer.save(update_fields=["email"])
        key = uuid.uuid4()
        reference = "KFD-POS-" + key.hex[:20]
        post.return_value = self.charge_response(reference)
        payload = self.payload()
        result = pos_paystack.start(
            self.user, self.branch, payload, key, "0551234567", "mtn", "",
        )
        sent_email = post.call_args.kwargs["json"]["email"]
        self.assertTrue(sent_email.startswith("momo-"))
        self.assertTrue(sent_email.endswith("@kofadimpex.com"))
        self.assertTrue(result["waiting"])
        stored = HeldSale.objects.get(label=pos_paystack.LABEL_PREFIX + reference)
        self.assertFalse(stored.cart["sale_payload"].get("customer_email"))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.email, "")
        # Retries must keep their original reference and never issue a second prompt.
        result_again = pos_paystack.start(
            self.user, self.branch, self.payload(), key, "0551234567", "mtn", "",
        )
        self.assertEqual(result_again["reference"], reference)
        self.assertEqual(post.call_count, 1)

    @patch("core.pos_paystack.requests.post")
    def test_invalid_explicit_customer_email_is_rejected_not_silently_replaced(self, post):
        with self.assertRaises(ValidationError):
            pos_paystack.start(
                self.user, self.branch, self.payload(), uuid.uuid4(),
                "0551234567", "mtn", "not-an-email",
            )
        post.assert_not_called()

    @patch("core.pos_paystack.requests.post")
    def test_real_customer_email_is_still_used_for_paystack(self, post):
        key = uuid.uuid4()
        reference = "KFD-POS-" + key.hex[:20]
        post.return_value = self.charge_response(reference)
        pos_paystack.start(
            self.user, self.branch, self.payload(), key,
            "0551234567", "mtn", "customer@example.test",
        )
        self.assertEqual(
            post.call_args.kwargs["json"]["email"], "customer@example.test"
        )

    @patch("core.pos_paystack.requests.post")
    def test_start_is_idempotent_for_same_sale_key(self, post):
        key = uuid.uuid4()
        reference = "KFD-POS-" + key.hex[:20]
        post.return_value = self.charge_response(reference)
        for _ in range(2):
            pos_paystack.start(
                self.user, self.branch, self.payload(), key,
                "0551234567", "mtn", "customer@example.test",
            )
        self.assertEqual(post.call_count, 1)
        self.assertEqual(HeldSale.objects.filter(label=pos_paystack.LABEL_PREFIX + reference).count(), 1)

    @patch("core.pos_paystack.requests.get")
    @patch("core.pos_paystack.requests.post")
    def test_verified_payment_posts_sale_once_with_provider_reference(self, post, get):
        key = uuid.uuid4()
        reference = "KFD-POS-" + key.hex[:20]
        post.return_value = self.charge_response(reference)
        pos_paystack.start(
            self.user, self.branch, self.payload(), key,
            "0551234567", "mtn", "customer@example.test",
        )
        held = HeldSale.objects.get(label=pos_paystack.LABEL_PREFIX + reference)
        state = held.cart["payment_request"]
        state["next_check_at"] = 0
        held.cart["payment_request"] = state
        held.save(update_fields=["cart"])
        get.return_value = self.verify_response(reference)

        first = pos_paystack.reconcile(reference, force=True)
        second = pos_paystack.reconcile(reference, force=True)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Document.objects.filter(kind="sale").count(), 1)
        self.assertEqual(Movement.objects.count(), 1)
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 239)
        payment = Payment.objects.get(document=first, method="momo")
        self.assertEqual(payment.amount, Decimal("50.00"))
        self.assertEqual(payment.reference, reference)

    @patch("core.pos_paystack.requests.get")
    @patch("core.pos_paystack.requests.post")
    def test_wrong_verified_amount_never_posts_sale(self, post, get):
        key = uuid.uuid4()
        reference = "KFD-POS-" + key.hex[:20]
        post.return_value = self.charge_response(reference)
        pos_paystack.start(
            self.user, self.branch, self.payload(), key,
            "0551234567", "mtn", "customer@example.test",
        )
        get.return_value = self.verify_response(reference, amount=4900)

        with self.assertRaises(ValidationError):
            pos_paystack.reconcile(reference, force=True)

        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(Movement.objects.count(), 0)
        held = HeldSale.objects.get(label=pos_paystack.LABEL_PREFIX + reference)
        self.assertEqual(held.cart["payment_request"]["status"], "attention")

    @patch("core.pos_paystack.requests.get")
    @patch("core.pos_paystack.requests.post")
    def test_background_reconciliation_finishes_without_browser(self, post, get):
        key = uuid.uuid4()
        reference = "KFD-POS-" + key.hex[:20]
        post.return_value = self.charge_response(reference)
        pos_paystack.start(
            self.user, self.branch, self.payload(), key,
            "0551234567", "mtn", "customer@example.test",
        )
        held = HeldSale.objects.get(label=pos_paystack.LABEL_PREFIX + reference)
        state = held.cart["payment_request"]
        state["next_check_at"] = 0
        held.cart["payment_request"] = state
        held.save(update_fields=["cart"])
        get.return_value = self.verify_response(reference)

        self.assertEqual(pos_paystack.reconcile_due(), 1)
        held.refresh_from_db()
        self.assertEqual(held.cart["payment_request"]["status"], "success")
        self.assertTrue(held.cart["payment_request"]["document_id"])

    @override_settings(PAYSTACK_SECRET_KEY="", PAYSTACK_POS_MOMO_ENABLED=False)
    def test_normal_pos_endpoint_rejects_unverified_momo_sale(self):
        self.authenticate_client()
        response = self.client.post(
            "/api/trades/",
            data=json.dumps(self.payload()),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Paystack activation", response.json()["error"])
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(Movement.objects.count(), 0)

    def test_normal_pos_endpoint_rejects_momo_even_when_paystack_is_ready(self):
        self.authenticate_client()
        response = self.client.post(
            "/api/trades/",
            data=json.dumps(self.payload()),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Paystack approval request", response.json()["error"])
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(Movement.objects.count(), 0)

    @patch("core.pos_paystack.requests.post")
    def test_direct_momo_rejects_split_or_partial_payment(self, post):
        payload = self.payload()
        payload["payments"][0]["amount"] = "10"
        payload["payments"][1]["amount"] = "40"
        with self.assertRaisesMessage(ValidationError, "one positive Mobile Money amount"):
            pos_paystack.start(
                self.user, self.branch, payload, uuid.uuid4(),
                "0551234567", "mtn", "customer@example.test",
            )
        post.assert_not_called()

    @patch("core.pos_paystack.requests.get")
    @patch("core.pos_paystack.requests.post")
    def test_verified_partial_momo_posts_deposit_and_customer_debt_once(self, post, get):
        from datetime import timedelta
        from django.utils import timezone
        from core import services
        key = uuid.uuid4()
        reference = pos_paystack._reference_from_key(key)
        payload = self.payload()
        payload["payments"][1]["amount"] = "20.00"
        payload["due_date"] = str(timezone.localdate() + timedelta(days=1))
        post.return_value = self.charge_response(reference)
        created = pos_paystack.start(
            self.user, self.branch, payload, key,
            "0551234567", "mtn", "customer@example.test",
        )
        self.assertTrue(created["waiting"])
        self.assertEqual(post.call_args.kwargs["json"]["amount"], "2000")
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(Movement.objects.count(), 0)
        get.return_value = self.verify_response(reference, amount=2000)
        doc = pos_paystack.reconcile(reference, force=True)
        self.assertEqual(doc.total, Decimal("50.00"))
        self.assertEqual(doc.paid, Decimal("20.00"))
        self.assertEqual(services.balance(doc), Decimal("30.00"))
        self.assertEqual(doc.party_id, self.customer.pk)
        self.assertIsNotNone(doc.due_date)
        self.assertEqual(Payment.objects.get(document=doc, method="momo").amount, Decimal("20.00"))
        self.assertEqual(pos_paystack.reconcile(reference, force=True).pk, doc.pk)
        self.assertEqual(Document.objects.filter(kind="sale").count(), 1)
        self.assertEqual(Movement.objects.count(), 1)

    @patch("core.pos_paystack.requests.post")
    def test_partial_momo_cannot_create_debt_without_due_date(self, post):
        payload = self.payload()
        payload["payments"][1]["amount"] = "20.00"
        with self.assertRaises(ValidationError):
            pos_paystack.start(self.user, self.branch, payload, uuid.uuid4(),
                               "0551234567", "mtn", "")
        post.assert_not_called()
        self.assertEqual(Document.objects.count(), 0)

    @patch("core.pos_paystack.requests.post")
    def test_deposit_not_accepted_over_sale_total(self, post):
        payload = self.payload()
        payload["payments"][1]["amount"] = "60.00"
        with self.assertRaises(ValidationError):
            pos_paystack.start(self.user, self.branch, payload, uuid.uuid4(),
                               "0551234567", "mtn", "")
        post.assert_not_called()
        self.assertEqual(Document.objects.count(), 0)

    @patch("core.pos_paystack.requests.post")
    @patch("core.paystack_challenges.charge_step")
    def test_staff_otp_keeps_sale_unposted_until_independent_verification(self, step, post):
        key = uuid.uuid4()
        reference = "KFD-POS-" + key.hex[:20]
        response = self.charge_response(reference)
        response.json.return_value["data"]["status"] = "send_otp"
        post.return_value = response
        result = pos_paystack.start(self.user, self.branch, self.payload(), key,
            "0551234567", "mtn", "customer@example.test")
        self.assertTrue(result["needs_otp"])
        step.return_value = {"status": "pay_offline", "reference": reference}
        result = pos_paystack.submit_otp(self.user, self.branch, reference, "988776")
        self.assertFalse(result["paid"])
        self.assertEqual(Document.objects.count(), 0)
        state = HeldSale.objects.get(label=pos_paystack.LABEL_PREFIX + reference).cart["payment_request"]
        self.assertNotIn("988776", str(state))
        self.assertEqual(post.call_count, 1)

    @patch("core.pos_paystack.requests.post")
    @patch("core.paystack_challenges.charge_step")
    def test_staff_cannot_submit_code_for_unknown_payment(self, step, post):
        with self.assertRaises(ValidationError):
            pos_paystack.submit_otp(self.user, self.branch, "KFD-POS-unknown", "988776")
        step.assert_not_called()
        post.assert_not_called()

    @patch("core.pos_paystack.requests.post")
    @patch("core.pos_paystack.verify")
    @patch("core.paystack_challenges.charge_step")
    def test_staff_active_code_challenge_is_not_prematurely_failed(self, step, verify, post):
        key = uuid.uuid4()
        reference = "KFD-POS-" + key.hex[:20]
        response = self.charge_response(reference)
        response.json.return_value["data"]["status"] = "send_otp"
        post.return_value = response
        pos_paystack.start(self.user, self.branch, self.payload(), key,
            "0551234567", "mtn", "customer@example.test")
        step.return_value = {"status": "send_otp", "reference": reference}
        verify.return_value = {"status": "failed", "reference": reference}
        with self.assertRaises(pos_paystack.ProviderPending):
            pos_paystack.reconcile(reference, force=True)
        state = HeldSale.objects.get(label=pos_paystack.LABEL_PREFIX + reference).cart["payment_request"]
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["charge_status"], "send_otp")
        self.assertEqual(Document.objects.count(), 0)
