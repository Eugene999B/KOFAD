from unittest.mock import Mock, patch
import requests
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from .tests import MarketFixtures
from .models import MarketPaymentAttempt, PaymentConfiguration
from . import paystack_momo, services
from .forms import CheckoutPaymentForm
from .paystack_reconciliation import reconcile_due


@override_settings(PAYSTACK_SECRET_KEY="test-key", PAYSTACK_CUSTOMER_MOMO_ENABLED=True)
class CustomerMomoTests(MarketFixtures):
    def login_customer(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def response(self, post, status="pay_offline"):
        def charge(*args, **kwargs):
            return Mock(status_code=200, json=lambda: {"status": True, "data": {
                "reference": kwargs["json"]["reference"], "status": status,
                "display_text": "Approve the payment on your phone.",
            }})
        post.side_effect = charge

    @patch("marketplace.paystack_momo.requests.post")
    def test_direct_prompt_uses_saved_total_and_local_phone(self, post):
        self.response(post)
        order = self.order()
        attempt = paystack_momo.initialize(order, "0551234567", "mtn")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["amount"], str(int(order.total * 100)))
        self.assertEqual(payload["mobile_money"], {"phone": "0551234567", "provider": "mtn"})
        self.assertEqual(attempt.status, "pending")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "pending")
        self.assertIsNone(order.sale_document_id)

    @patch("marketplace.paystack_momo.requests.post")
    def test_double_submit_cannot_charge_twice(self, post):
        self.response(post)
        order = self.order()
        first = paystack_momo.initialize(order, "0551234567", "mtn")
        second = paystack_momo.initialize(order, "0201234567", "atl")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(post.call_count, 1)

    @patch("marketplace.paystack_momo.requests.post", side_effect=requests.Timeout)
    def test_lost_response_keeps_original_charge_intent(self, post):
        order = self.order()
        first = paystack_momo.initialize(order, "0551234567", "mtn")
        second = paystack_momo.initialize(order, "0551234567", "mtn")
        self.assertEqual(first.status, "submission_unknown")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(post.call_count, 1)

    @patch("marketplace.paystack_momo.requests.post")
    def test_malformed_provider_body_remains_unconfirmed(self, post):
        post.return_value = Mock(status_code=200, json=lambda: [])
        attempt = paystack_momo.initialize(self.order(), "0551234567", "mtn")
        self.assertEqual(attempt.status, "submission_unknown")

    @patch("marketplace.paystack_momo.requests.post")
    def test_unresolved_hubtel_cannot_be_replaced(self, post):
        order = self.order()
        MarketPaymentAttempt.objects.create(order=order, provider="hubtel", reference="original-hubtel",
            amount=order.total, status="pending")
        with self.assertRaises(ValidationError):
            paystack_momo.initialize(order, "0551234567", "mtn")
        post.assert_not_called()

    @patch("marketplace.paystack_momo.requests.post")
    def test_success_in_charge_response_is_not_payment_confirmation(self, post):
        self.response(post, "success")
        order = self.order()
        paystack_momo.initialize(order, "0551234567", "mtn")
        order.refresh_from_db()
        self.assertNotEqual(order.payment_status, "paid")
        self.assertIsNone(order.sale_document_id)

    @patch("marketplace.paystack_momo.requests.post")
    def test_callback_cannot_be_overwritten_by_slow_charge_response(self, post):
        order = self.order()
        def charge(*args, **kwargs):
            MarketPaymentAttempt.objects.filter(reference=kwargs["json"]["reference"]).update(status="success")
            return Mock(status_code=200, json=lambda: {"status": True, "data": {
                "reference": kwargs["json"]["reference"], "status": "pay_offline"}})
        post.side_effect = charge
        attempt = paystack_momo.initialize(order, "0551234567", "mtn")
        self.assertEqual(attempt.status, "success")

    def test_payment_form_requires_valid_number_and_network(self):
        form = CheckoutPaymentForm({"payment_method": "momo", "momo_phone": "wrong", "momo_network": ""},
                                   momo_available=True)
        self.assertFalse(form.is_valid())
        self.assertIn("momo_phone", form.errors)
        self.assertIn("momo_network", form.errors)
        self.assertFalse(CheckoutPaymentForm({"payment_method": "momo"}, momo_available=False).is_valid())

    def test_customer_sees_momo_choices_when_paystack_is_selected(self):
        PaymentConfiguration.objects.create(provider="paystack")
        self.login_customer()
        session = self.client.session
        session["market_cart"] = {str(self.listing.pk): 1}
        session.save()
        response = self.client.get("/market/checkout/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mobile Money number")
        self.assertContains(response, "Telecel Cash")

    def test_pending_paystack_is_polled_and_cannot_be_cancelled(self):
        self.login_customer()
        order = self.order()
        MarketPaymentAttempt.objects.create(order=order, provider="paystack", reference="pending-paystack",
            amount=order.total, status="pending")
        status = self.client.get(f"/market/orders/{order.pk}/payment-status/")
        self.assertTrue(status.json()["waiting"])
        self.client.post(f"/market/orders/{order.pk}/cancel/")
        order.refresh_from_db()
        self.assertEqual(order.status, "awaiting_payment")

    @patch("marketplace.services.requests.get")
    def test_verified_failure_unlocks_a_new_payment_without_marking_paid(self, get):
        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(order=order, provider="paystack", reference="failed-momo",
            amount=order.total, status="pending", next_check_at=timezone.now())
        order.payment_reference = attempt.reference
        order.payment_status = "pending"
        order.save()
        get.return_value = Mock(status_code=200, json=lambda: {"status": True, "data": {
            "reference": attempt.reference, "status": "failed"}})
        reconcile_due()
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(order.payment_status, "failed")
        self.assertEqual(attempt.status, "failed")
        self.assertIsNone(order.sale_document_id)

    def test_direct_payment_rejects_wrong_verified_channel(self):
        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(order=order, provider="paystack", reference="wrong-channel",
            amount=order.total, status="pending", verification_summary={"flow": "mobile_money"})
        with self.assertRaises(ValidationError):
            services.finalize_payment(attempt.reference, {"status": "success", "id": 12,
                "amount": int(order.total * 100), "currency": "GHS", "channel": "card"})
        order.refresh_from_db()
        self.assertNotEqual(order.payment_status, "paid")

    def test_pending_verification_does_not_change_order_to_failed(self):
        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(order=order, provider="paystack", reference="still-pending",
            amount=order.total, status="pending")
        order.payment_status = "pending"
        order.save()
        with self.assertRaises(ValidationError):
            services.finalize_payment(attempt.reference, {"status": "pending"})
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "pending")

    def test_receiving_account_name_is_preserved(self):
        from .forms import ReceivingAccountForm
        config = PaymentConfiguration.objects.create(provider="paystack")
        form = ReceivingAccountForm({"bank_account_name": "KOFAD IMPEX ENTERPRISE",
            "bank_account_number": "7011440002041", "bank_name": "GCB BANK",
            "bank_branch": "SUNYANI MAIN", "bank_branch_code": "701",
            "receiving_momo": "0538812780",
            "receiving_momo_name": "KOFAD IMPEX ENTERPRISE/ERNEST AMOAH KOFFIE"}, instance=config)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.receiving_momo, "+233538812780")
        self.assertEqual(saved.receiving_momo_name, "KOFAD IMPEX ENTERPRISE/ERNEST AMOAH KOFFIE")

    @patch("marketplace.services.requests.get")
    def test_staff_can_verify_a_paystack_order_without_charging(self, get):
        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(order=order, provider="paystack", reference="staff-check",
            amount=order.total, status="pending", verification_summary={"flow": "mobile_money"})
        get.return_value = Mock(status_code=200, json=lambda: {"status": True, "data": {
            "reference": attempt.reference, "status": "success", "id": 123,
            "amount": int(order.total * 100), "currency": "GHS", "channel": "mobile_money"}})
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()
        self.assertContains(self.client.get(f"/online-orders/{order.pk}/"), "Verify with Paystack")
        response = self.client.post(f"/online-orders/{order.pk}/", {
            "form_type": "payment_check", "payment_reference": attempt.reference})
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(get.call_count, 1)
