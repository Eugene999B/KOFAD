"""Prevent Hubtel-paid orders from being refunded through Paystack."""
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from . import services


class HubtelDirectRefundRoutingTests(SimpleTestCase):
    @patch("marketplace.services.MarketPaymentAttempt.objects.filter")
    def test_hubtel_direct_cannot_trigger_paystack_refund(self, lookup):
        lookup.return_value.exists.return_value = True
        order = SimpleNamespace(payment_reference="hubtel-direct-paid-reference")
        item = SimpleNamespace(order=order)
        with self.assertRaisesRegex(ValidationError, "Hubtel refund"):
            services.initiate_paystack_refund(item)
        lookup.assert_called_once_with(
            order=order, reference=order.payment_reference,
            provider__in=["hubtel", "hubtel_direct"],
        )

    @patch("marketplace.services.MarketPaymentAttempt.objects.filter")
    def test_hubtel_direct_cannot_be_checked_as_paystack_refund(self, lookup):
        lookup.return_value.exists.return_value = True
        order = SimpleNamespace(payment_reference="hubtel-direct-paid-reference")
        item = SimpleNamespace(order=order)
        with self.assertRaisesRegex(ValidationError, "Hubtel refund"):
            services.refresh_paystack_refund(item)
        lookup.assert_called_once_with(
            order=order, reference=order.payment_reference,
            provider__in=["hubtel", "hubtel_direct"],
        )
