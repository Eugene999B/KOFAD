from unittest.mock import Mock, patch
import requests
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from .tests import MarketFixtures
from .models import MarketPaymentAttempt, PaymentConfiguration, CustomerAccount
from . import paystack_momo, services
from .forms import CheckoutPaymentForm
from .paystack_reconciliation import reconcile_due


@override_settings(PAYSTACK_SECRET_KEY="test-key", PAYSTACK_CUSTOMER_MOMO_ENABLED=True)
class CustomerMomoTests(MarketFixtures):
    def setUp(self):
        super().setUp()
        charge_check = patch("core.paystack_challenges.charge_step", side_effect=ValidationError("Pending"))
        charge_check.start()
        self.addCleanup(charge_check.stop)

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

    @patch("marketplace.paystack_momo.requests.post")
    def test_provider_otp_challenge_is_shown_without_saving_code(self, post):
        self.response(post, "send_otp")
        order = self.order()
        attempt = paystack_momo.initialize(order, "0551234567", "mtn")
        self.assertEqual(attempt.verification_summary["charge_status"], "send_otp")
        self.login_customer()
        response = self.client.get(f"/market/orders/{order.pk}/")
        self.assertContains(response, 'name="otp"')
        self.assertContains(response, "Never enter your Mobile Money PIN.")

    @patch("marketplace.paystack_momo.requests.post")
    @patch("core.paystack_challenges.charge_step")
    def test_otp_submission_reuses_reference_and_does_not_mark_paid(self, step, post):
        self.response(post, "send_otp")
        order = self.order()
        attempt = paystack_momo.initialize(order, "0551234567", "mtn")
        step.return_value = {"status": "pay_offline", "reference": attempt.reference}
        paystack_momo.submit_otp(order, "988776")
        step.assert_called_once_with(attempt.reference, otp="988776")
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertNotEqual(order.payment_status, "paid")
        self.assertNotIn("988776", str(attempt.verification_summary))
        self.assertEqual(post.call_count, 1)

    @patch("marketplace.paystack_momo.requests.post")
    @patch("core.paystack_challenges.charge_step")
    def test_otp_submission_rate_limit(self, step, post):
        self.response(post, "send_otp")
        order = self.order()
        attempt = paystack_momo.initialize(order, "0551234567", "mtn")
        step.return_value = {"status": "send_otp", "reference": attempt.reference}
        paystack_momo.submit_otp(order, "988776")
        with self.assertRaises(ValidationError):
            paystack_momo.submit_otp(order, "988776")
        self.assertEqual(step.call_count, 1)

    @patch("core.paystack_challenges.charge_step")
    def test_other_customer_cannot_submit_payment_code(self, step):
        order = self.order()
        session = self.client.session
        other = CustomerAccount.objects.create(phone="+233551234568", full_name="Other", verified_at=timezone.now())
        session["market_customer_id"] = other.pk
        session.save()
        response = self.client.post(f"/market/orders/{order.pk}/payment-otp/", {"otp": "988776"})
        self.assertEqual(response.status_code, 404)
        step.assert_not_called()

    @patch("marketplace.services.verify_paystack")
    @patch("core.paystack_challenges.charge_step")
    def test_transaction_failure_cannot_fail_an_active_payment_challenge(self, step, verify):
        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(order=order, provider="paystack",
            reference="active-challenge", amount=order.total, status="pending",
            next_check_at=timezone.now(), verification_summary={"flow": "mobile_money"})
        order.payment_reference = attempt.reference
        order.payment_status = "pending"
        order.save()
        step.return_value = {"status": "send_otp", "reference": attempt.reference}
        verify.return_value = {"status": "failed", "reference": attempt.reference}
        reconcile_due()
        attempt.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(attempt.status, "pending")
        self.assertEqual(attempt.verification_summary["charge_status"], "send_otp")

    @patch("marketplace.services.verify_paystack")
    def test_unopened_hosted_checkout_keeps_original_payment_pending(self, verify):
        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(order=order, provider="paystack",
            reference="hosted-unopened", amount=order.total, status="pending",
            authorization_url="https://checkout.paystack.com/original", next_check_at=timezone.now())
        order.payment_reference = attempt.reference
        order.payment_status = "pending"
        order.save()
        verify.return_value = {"status": "abandoned", "reference": attempt.reference}
        reconcile_due()
        attempt.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(attempt.status, "pending")
        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(attempt.authorization_url, "https://checkout.paystack.com/original")
        self.assertIsNone(order.sale_document_id)

    @patch("marketplace.services.verify_paystack")
    def test_recent_hosted_checkout_premature_failure_is_recovered(self, verify):
        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(order=order, provider="paystack",
            reference="hosted-recover", amount=order.total, status="failed",
            provider_message="Provider confirmed unsuccessful payment.",
            authorization_url="https://checkout.paystack.com/original")
        order.payment_reference = attempt.reference
        order.payment_status = "failed"
        order.save()
        verify.return_value = {"status": "abandoned", "reference": attempt.reference}
        reconcile_due()
        attempt.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(attempt.status, "pending")
        self.assertEqual(order.payment_status, "pending")
        self.assertIsNone(order.sale_document_id)

    @patch("marketplace.services.verify_paystack")
    def test_replaced_hosted_checkout_cannot_be_recovered(self, verify):
        order = self.order()
        MarketPaymentAttempt.objects.create(order=order, provider="paystack",
            reference="hosted-replaced", amount=order.total, status="failed",
            provider_message="Provider confirmed unsuccessful payment.",
            authorization_url="https://checkout.paystack.com/original")
        order.payment_reference = "newer-payment"
        order.save()
        reconcile_due()
        verify.assert_not_called()

    @patch("marketplace.services.verify_paystack")
    def test_recovery_cannot_overwrite_concurrent_success(self, verify):
        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(order=order, provider="paystack",
            reference="hosted-race", amount=order.total, status="failed",
            provider_message="Provider confirmed unsuccessful payment.",
            authorization_url="https://checkout.paystack.com/original")
        order.payment_reference = attempt.reference
        order.payment_status = "failed"
        order.save()
        def provider_check(reference):
            MarketPaymentAttempt.objects.filter(pk=attempt.pk).update(status="success")
            type(order).objects.filter(pk=order.pk).update(payment_status="paid", status="paid")
            return {"status": "abandoned", "reference": reference}
        verify.side_effect = provider_check
        reconcile_due()
        attempt.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(attempt.status, "success")
        self.assertEqual(order.payment_status, "paid")
