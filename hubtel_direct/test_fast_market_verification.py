"""Read-only instant status checks for a real pending market payment.

No test sends a payment request or contacts the live provider.
"""
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from marketplace.models import MarketPaymentAttempt, OnlineOrder
from marketplace.tests import MarketFixtures
from . import client, service
from .models import DirectTransaction


class FastMarketVerificationTests(MarketFixtures):
    def setUp(self):
        super().setUp()
        self.market_order = self.order()
        self.ref = "direct-original-reference-00000001"
        self.tx = DirectTransaction.objects.create(
            client_reference=self.ref, kind="receive",
            customer=self.customer, order=self.market_order,
            amount=self.market_order.total,
            channel="mtn-gh", status="pending", provider_code="0001",
            destination_encrypted=client.seal("233241234567"),
            destination_suffix="4567",
            hubtel_transaction_id="provider-tx-id-123",
            next_check_at=timezone.now() + timedelta(seconds=20),
        )
        self.attempt = MarketPaymentAttempt.objects.create(
            order=self.market_order, provider="hubtel_direct", reference=self.ref,
            amount=self.tx.amount, currency="GHS", status="pending",
        )
        self.market_order.payment_reference = self.ref
        self.market_order.payment_status = "pending"
        self.market_order.save(update_fields=["payment_reference", "payment_status"])
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def ready(self):
        past = timezone.now() - timedelta(seconds=55)
        DirectTransaction.objects.filter(pk=self.tx.pk).update(
            created_at=past, next_check_at=past + timedelta(seconds=20),
        )

    @patch("hubtel_direct.client.check", return_value=("pending", "provider-tx-id-123"))
    def test_first_check_is_fast_and_subsequent_refreshes_do_not_repeat_network_get(self, provider):
        self.ready()
        url = f"/market/orders/{self.market_order.pk}/payment/verification/?format=json"
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["paid"])
        self.assertEqual(first.json()["provider_status"], "unpaid")
        self.assertIsNotNone(first.json()["last_checked_at"])
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.provider_status, "unpaid")
        self.assertEqual(self.tx.check_count, 1)
        self.market_order.refresh_from_db()
        self.assertNotEqual(self.market_order.payment_status, "paid")
        self.client.get(url)
        provider.assert_called_once()

    @patch("hubtel_direct.client.check", side_effect=client.StatusUnconfirmed("provider status unavailable"))
    def test_unconfirmed_network_status_never_marks_customer_payment_as_failed(self, provider):
        self.ready()
        url = f"/market/orders/{self.market_order.pk}/payment/verification/?format=json"
        result = self.client.get(url)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["provider_status"], "unavailable")
        self.assertFalse(result.json()["paid"])
        self.assertFalse(result.json()["failed"])
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, "pending")
        provider.assert_called_once()

    @patch("hubtel_direct.client.check")
    def test_never_checks_before_first_safe_network_window(self, provider):
        result = self.client.get(
            f"/market/orders/{self.market_order.pk}/payment/verification/?format=json",
        )
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()["last_checked_at"])
        provider.assert_not_called()

    def test_paid_label_needs_both_verified_transaction_and_matching_order_reference(self):
        from .views import _receive_payment_snapshot
        self.tx.status = "success"
        self.tx.provider_status = "paid"
        self.tx.save(update_fields=["status", "provider_status"])
        self.market_order.payment_status = "paid"
        self.market_order.payment_reference = "different-reference"
        self.market_order.save(update_fields=["payment_status", "payment_reference"])
        result = _receive_payment_snapshot(self.tx, self.market_order)
        self.assertFalse(result["paid"])
        self.assertEqual(result["status"], "attention")
        self.market_order.payment_reference = self.ref
        self.market_order.save(update_fields=["payment_reference"])
        confirmed = _receive_payment_snapshot(self.tx, self.market_order)
        self.assertTrue(confirmed["paid"])
        self.assertEqual(confirmed["provider_status"], "paid")

    @patch("hubtel_direct.client.check", return_value=("pending", "provider-tx-id-123"))
    def test_staff_original_reference_check_does_not_claim_payment_is_paid(self, provider):
        self.ready()
        # Do not reuse the logged-in customer browser session for staff tests:
        # KOFAD intentionally separates staff and customer session zones.
        from django.test import Client
        staff_client = Client()
        staff_client.force_login(self.staff)
        # AccessMiddleware revokes sessions with an absent access version;
        # the real staff sign-in writes this value after authentication.
        session = staff_client.session
        session["access_version"] = self.staff.access.session_version
        session.save()
        response = staff_client.post(
            f"/online-orders/{self.market_order.pk}/",
            {"form_type": "payment_check", "payment_reference": self.ref},
        )
        self.assertEqual(response["Location"], f"/online-orders/{self.market_order.pk}/")
        self.assertEqual(response.status_code, 302)
        self.market_order.refresh_from_db()
        self.assertNotEqual(self.market_order.payment_status, "paid")
        provider.assert_called_once()
