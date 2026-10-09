"""Regression tests for all-inclusive product prices across online payment channels."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import override_settings

from . import services
from .models import OnlineOrder, PaymentConfiguration
from .pricing import all_in_unit_price, parse_rate
from .tests import MarketFixtures


class AllInclusiveOnlinePricingTests(MarketFixtures):
    def staff_session(self, user=None):
        user = user or self.staff
        self.client.force_login(user)
        user.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = user.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    def customer_session(self):
        s = self.client.session
        s["market_customer_id"] = self.customer.pk
        s["market_cart"] = {str(self.listing.pk): 2}
        s.save()

    def test_strict_numeric_decimal_input_and_rounding(self):
        for raw, rate in [
            ("0", "0.000"), ("2", "2.000"), ("1.95", "1.950"),
            ("0.5", "0.500"), (".125", "0.125"), ("100", "100.000"),
        ]:
            self.assertEqual(parse_rate(raw), Decimal(rate))
        for raw in ["-1", "+2", "1,95", "1.2.3", "1e2", "5%", " 2", "2 ", "",
                    "100.001", "2.1234", "abc", "1/2", "Infinity"]:
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                parse_rate(raw)
        self.assertEqual(all_in_unit_price(Decimal("10.00"), Decimal("1.95")), Decimal("10.20"))
        self.assertEqual(all_in_unit_price(Decimal("0.01"), Decimal("50")), Decimal("0.02"))
        self.assertEqual(all_in_unit_price(Decimal("100"), Decimal("0")), Decimal("100"))

    def test_staff_can_save_percentage_and_invalid_values_leave_it_unchanged(self):
        self.staff_session()
        response = self.client.post("/settings/online-payments/", {
            "action": "online_price_markup", "percentage": "1.95",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PaymentConfiguration.objects.get(pk=1).online_price_markup_percent, Decimal("1.950"))
        self.assertContains(self.client.get("/settings/online-payments/"), "1.950")
        for bad in ["2,50", "2%","-5","99.1234","999",""]:
            self.client.post("/settings/online-payments/", {
                "action": "online_price_markup", "percentage": bad,
            })
            self.assertEqual(PaymentConfiguration.objects.get(pk=1).online_price_markup_percent, Decimal("1.950"))
        self.client.post("/settings/online-payments/", {
            "action": "online_price_markup", "percentage": "0",
        })
        self.assertEqual(PaymentConfiguration.objects.get(pk=1).online_price_markup_percent, Decimal("0"))

    def test_unprivileged_staff_cannot_change_percentage(self):
        employee = User.objects.create_user("unprivileged-payment-operator", password="TestPasswordSecure2026!")
        employee.access.branches.add(self.branch)
        self.staff_session(employee)
        response = self.client.post("/settings/online-payments/", {
            "action": "online_price_markup", "percentage": "6.5",
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PaymentConfiguration.objects.exists())

    def test_market_and_cart_prices_are_inclusive_from_first_display(self):
        PaymentConfiguration.objects.create(online_price_markup_percent=Decimal("2.500"))
        self.assertEqual(self.listing.market_price, Decimal("102.50"))
        cart = services.cart_rows({str(self.listing.pk): 2})
        self.assertEqual(cart[0]["price"], Decimal("102.50"))
        self.assertEqual(cart[0]["total"], Decimal("205.00"))
        self.customer_session()
        for path in ("/market/", "/market/cart/", "/market/checkout/",
                     f"/market/products/{self.listing.pk}/"):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200, path)
            self.assertContains(page, "102.50", msg_prefix=path)
            self.assertNotContains(page, "2.5%")
            self.assertNotContains(page, "processing charge")
        suggestions = self.client.get("/market/search/suggestions/?q=hydraulic")
        self.assertEqual(suggestions.status_code, 200)
        if suggestions.json()["results"]:
            self.assertEqual(suggestions.json()["results"][0]["price"], "102.50")

    def test_existing_orders_are_unchanged_after_percentage_edit(self):
        PaymentConfiguration.objects.create(online_price_markup_percent=Decimal("2.500"))
        initial = self.order()
        self.assertEqual(initial.total, Decimal("205.00"))
        line = initial.lines.first()
        self.assertEqual(line.unit_price, Decimal("102.50"))
        PaymentConfiguration.objects.filter(pk=1).update(online_price_markup_percent=Decimal("4.000"))
        initial.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(initial.total, Decimal("205.00"))
        self.assertEqual(line.unit_price, Decimal("102.50"))
        next_order = self.order()
        self.assertEqual(next_order.total, Decimal("208.00"))
        self.assertEqual(OnlineOrder.objects.count(), 2)

    @patch("marketplace.hubtel.selected_provider", return_value="hubtel")
    def test_first_percentage_save_keeps_existing_selected_hubtel_gateway(self, selected):
        self.staff_session()
        self.assertFalse(PaymentConfiguration.objects.exists())
        response = self.client.post("/settings/online-payments/", {
            "action": "online_price_markup", "percentage": "1.95",
        })
        self.assertEqual(response.status_code, 302)
        config = PaymentConfiguration.objects.get(pk=1)
        self.assertEqual(config.provider, "hubtel")
        self.assertEqual(config.online_price_markup_percent, Decimal("1.950"))
        selected.assert_called()

    def test_provider_configuration_update_does_not_erase_saved_percentage(self):
        self.staff_session()
        PaymentConfiguration.objects.create(provider="hubtel", online_price_markup_percent=Decimal("1.950"))
        self.client.post("/settings/online-payments/", {"provider": "paystack"})
        config = PaymentConfiguration.objects.get(pk=1)
        self.assertEqual(config.provider, "paystack")
        self.assertEqual(config.online_price_markup_percent, Decimal("1.950"))
