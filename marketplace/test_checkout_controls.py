from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import override_settings
from . import services
from .forms import CheckoutForm
from .models import MarketPaymentAttempt, OnlineOrder, PaymentConfiguration
from .tests import MarketFixtures


@override_settings(HUBTEL_API_ID="test-id", HUBTEL_API_KEY="private-test-key",
                   HUBTEL_COLLECTION_ACCOUNT="12345", HUBTEL_CHECKOUT_ENABLED=False,
                   PAYSTACK_SECRET_KEY="")
class CheckoutControlsTests(MarketFixtures):
    def customer_session(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session["market_cart"] = {str(self.listing.pk): 1}
        session.save()

    def staff_session(self, user=None):
        user = user or self.staff
        self.client.force_login(user)
        user.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = user.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    def pickup_data(self):
        return {"fulfilment": "pickup", "recipient_name": self.customer.full_name,
                "phone": self.customer.phone, "email": self.customer.email}

    @patch("marketplace.hubtel.requests.post")
    def test_unavailable_checkout_preserves_cart_and_does_not_create_order(self, post):
        self.customer_session()
        response = self.client.post("/market/checkout/", self.pickup_data())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Online checkout is awaiting activation.")
        self.assertContains(response, "Your cart has been kept.")
        self.assertFalse(OnlineOrder.objects.exists())
        self.assertEqual(self.client.session["market_cart"], {str(self.listing.pk): 1})
        post.assert_not_called()

    def test_malformed_cart_values_cannot_crash_checkout_calculations(self):
        # Python treats superscript digits as isdigit() yet int() rejects them.
        # A stale or manipulated session must not cause a 500 on cart/checkout.
        valid_id = str(self.listing.pk)
        rows = services.cart_rows({
            valid_id: "2",
            "²": "1",
            "9" * 40: "1",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["listing"].pk, self.listing.pk)
        self.assertEqual(rows[0]["quantity"], 2)

        for corrupt_cart in (
            {valid_id: "²"},
            {"²": "1"},
            {valid_id: "9" * 5000},
            {valid_id: None},
            {valid_id: "-1"},
            {"9223372036854775808": "1"},
            ["unexpected-session-cart"],
            "unexpected-session-cart",
        ):
            with self.subTest(cart=repr(corrupt_cart)[:64]):
                self.assertEqual(services.cart_rows(corrupt_cart), [])

    def test_valid_cart_quantity_is_still_capped_at_999(self):
        rows = services.cart_rows({str(self.listing.pk): "999999"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 999)

    def test_checkout_dropdowns_have_clear_mobile_selection_controls(self):
        self.customer_session()
        response = self.client.get("/market/checkout/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose delivery or pickup")
        self.assertNotContains(response, "Tap to select delivery or collection")
        self.assertContains(response, "marketplace/select-affordances.")
        self.assertContains(response, 'name="fulfilment"')

    def test_checkout_collects_wallet_after_order_and_never_uses_hosted_provider(self):
        self.customer_session()
        page = self.client.get("/market/checkout/")
        self.assertContains(page, "Choose your payment number after placing the order.")
        self.assertContains(page, "Continue to payment")
        self.assertNotContains(page, 'name="direct_momo_phone"')
        self.assertNotContains(page, 'name="direct_momo_channel"')
        self.assertNotContains(page, "checkout.paystack.com")

    def test_pickup_needs_no_address_or_coordinates(self):
        data = self.pickup_data()
        data.update(town="Old town", address_line="Old delivery address", latitude="5.61", longitude="-0.18")
        form = CheckoutForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["address_line"], "")
        self.assertIsNone(form.cleaned_data["latitude"])
        order = services.create_order(
            self.customer, {str(self.listing.pk): 1}, form.cleaned_data)
        self.assertEqual(order.delivery_fee, Decimal("0"))

    def test_delivery_still_requires_address_and_pin(self):
        data = self.pickup_data()
        data["fulfilment"] = "delivery"
        form = CheckoutForm(data)
        self.assertFalse(form.is_valid())

    def test_owner_sees_direct_only_status_without_legacy_provider_selector(self):
        self.staff_session()
        response = self.client.get("/settings/online-payments/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Direct Mobile Money")
        self.assertNotContains(response, "Save checkout provider")
        self.assertContains(response, "Where does the money go?")
        self.assertNotContains(response, "private-test-key")
        self.client.post("/settings/online-payments/", {"provider": "paystack"})
        self.assertFalse(PaymentConfiguration.objects.exists())

    def test_payment_settings_deny_unprivileged_staff(self):
        user = User.objects.create_user("ordinary", password="test-only-strong-password")
        user.access.branches.add(self.branch)
        self.staff_session(user)
        response = self.client.post("/settings/online-payments/", {"provider": "paystack"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PaymentConfiguration.objects.exists())

    def test_payment_attempts_are_scoped_to_current_branch(self):
        from core.models import Branch
        self.staff_session()
        first = self.order()
        MarketPaymentAttempt.objects.create(order=first, provider="hubtel", reference="visible-payment", amount=first.total)
        second = self.order()
        second.branch = Branch.objects.create(name="Other", code="other")
        second.save(update_fields=["branch"])
        MarketPaymentAttempt.objects.create(order=second, provider="hubtel", reference="private-other-payment", amount=second.total)
        response = self.client.get("/settings/online-payments/")
        self.assertContains(response, "visible-payment")
        self.assertNotContains(response, "private-other-payment")

    def test_receiving_details_are_saved_without_changing_provider(self):
        self.staff_session()
        PaymentConfiguration.objects.create(provider="hubtel")
        response = self.client.post("/settings/online-payments/", {
            "action": "receiving_accounts", "bank_account_name": "Example business",
            "bank_account_number": "0001234567", "bank_name": "Example bank",
            "bank_branch": "Main", "bank_branch_code": "001", "receiving_momo": "0241234567",
        })
        self.assertEqual(response.status_code, 302)
        config = PaymentConfiguration.objects.get(pk=1)
        self.assertEqual(config.provider, "hubtel")
        self.assertEqual(config.bank_account_number, "0001234567")
        self.assertEqual(config.receiving_momo, "+233241234567")

    def test_customer_payment_progress_reference_is_private_to_the_order_owner(self):
        self.customer_session()
        order = self.order()
        MarketPaymentAttempt.objects.create(
            order=order, provider="hubtel", reference="private-progress-reference",
            amount=order.total, status="pending",
        )
        response = self.client.get(f"/market/orders/{order.pk}/")
        self.assertContains(response, "Payment verification in progress")
        self.assertNotContains(response, "Check Hubtel payment")
        self.assertContains(response, "private-progress-reference")  # Visible only to this signed-in owner
        status = self.client.get(f"/market/orders/{order.pk}/payment-status/")
        details = status.json()
        for key, expected in {
            "payment_status": order.payment_status, "order_status": order.status,
            "waiting": True, "paid": False, "message": "", "attention": False,
            "needs_otp": False, "reference": "private-progress-reference",
        }.items():
            self.assertEqual(details[key], expected)
        self.assertIn("provider_status", details)
        self.assertIn("last_checked_at", details)
        self.assertIn("no-store", status["Cache-Control"])
        other = self.order()
        from .models import CustomerAccount
        other.customer = CustomerAccount.objects.create(phone="+233249999999", full_name="Other")
        other.save(update_fields=["customer"])
        self.assertEqual(self.client.get(f"/market/orders/{other.pk}/payment-status/").status_code, 404)
        self.assertEqual(self.client.get(f"/market/orders/{other.pk}/").status_code, 404)
