"""Offline provider contract and no-duplicate-money regression tests."""
from datetime import timedelta
import uuid
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from marketplace.models import MarketPaymentAttempt, VerifiedMomoPhone
from marketplace.tests import MarketFixtures
from . import client, service
from .models import DirectTransaction

READY = dict(
    HUBTEL_API_ID="test-api-id", HUBTEL_API_KEY="test-api-key",
    HUBTEL_COLLECTION_ACCOUNT="12345", HUBTEL_DISBURSEMENT_ACCOUNT="67890",
    HUBTEL_DIRECT_EGRESS_CONFIRMED=True, HUBTEL_DIRECT_RECEIVE_ENABLED=True,
    HUBTEL_DIRECT_SEND_MOMO_ENABLED=True, HUBTEL_DIRECT_SEND_BANK_ENABLED=True,
    HUBTEL_DIRECT_MAX_PAYOUT_GHS="500.00",
    MARKET_SITE_ORIGIN="https://market.kofadimpex.com",
)


class ContractTests(SimpleTestCase):
    def test_all_money_functions_are_disabled_by_default(self):
        with override_settings(
            HUBTEL_DIRECT_EGRESS_CONFIRMED=False,
            HUBTEL_DIRECT_RECEIVE_ENABLED=False,
            HUBTEL_DIRECT_SEND_MOMO_ENABLED=False,
            HUBTEL_DIRECT_SEND_BANK_ENABLED=False,
        ):
            self.assertFalse(client.enabled("receive"))
            self.assertFalse(client.enabled("send_momo"))
            self.assertFalse(client.enabled("send_bank"))

    def test_money_and_bank_codes_are_exact(self):
        for bad in ("NaN", "-1", "1.234", "Infinity", "0", None):
            with self.assertRaises(ValidationError):
                client.money(bad)
        self.assertEqual(client.money("0.50"), Decimal("0.50"))
        self.assertIn("300311", client.BANK_CODES)
        self.assertNotIn("300399", client.BANK_CODES)

    @override_settings(**READY)
    def test_destination_round_trip_is_encrypted(self):
        encrypted = client.seal("233241234567")
        self.assertNotIn("233241234567", encrypted)
        self.assertEqual(client.unseal(encrypted), "233241234567")

    @override_settings(**READY)
    def test_callback_cannot_use_external_host(self):
        with override_settings(MARKET_SITE_ORIGIN="https://evil.example"):
            with self.assertRaises(ValidationError):
                client._callback()


@override_settings(**READY)
class DirectWorkflowTests(MarketFixtures):
    def setUp(self):
        super().setUp()
        VerifiedMomoPhone.objects.create(customer=self.customer, phone=self.customer.phone)

    @staticmethod
    def accepted(url, **kwargs):
        payload = kwargs["json"]
        return Mock(status_code=200, json=lambda: {
            "ResponseCode": "0001",
            "Data": {
                "ClientReference": payload["ClientReference"],
                "TransactionId": "provider-123",
            },
        })

    @patch("hubtel_direct.client.requests.post")
    def test_customer_registered_phone_is_locked_and_repeat_post_never_charges_twice(self, post):
        post.side_effect = self.accepted
        order = self.order()
        first = service.create_collection(self.customer, order.pk, "mtn-gh")
        second = service.create_collection(self.customer, order.pk, "mtn-gh")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, "pending")
        post.assert_called_once()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["CustomerMsisdn"], "233241234567")
        self.assertEqual(payload["Amount"], float(order.total))
        self.assertEqual(payload["PrimaryCallbackURL"], "https://market.kofadimpex.com/market/payments/hubtel-direct/callback/")
        self.assertEqual(MarketPaymentAttempt.objects.filter(reference=first.client_reference).count(), 1)

    @patch("hubtel_direct.client.requests.post", side_effect=requests.Timeout())
    def test_lost_response_never_resends(self, post):
        order = self.order()
        tx = service.create_collection(self.customer, order.pk, "vodafone-gh")
        self.assertEqual(tx.status, "submission_unknown")
        self.assertEqual(service.create_collection(self.customer, order.pk, "vodafone-gh").pk, tx.pk)
        post.assert_called_once()

    @patch("hubtel_direct.client.requests.post")
    def test_unverified_number_never_receives_prompt(self, post):
        VerifiedMomoPhone.objects.all().delete()
        with self.assertRaises(ValidationError):
            service.create_collection(self.customer, self.order().pk, "mtn-gh")
        post.assert_not_called()

    @patch("hubtel_direct.client.requests.post")
    def test_spoofed_callback_does_not_settle_order(self, post):
        post.side_effect = self.accepted
        order = self.order()
        tx = service.create_collection(self.customer, order.pk, "mtn-gh")
        service.note_callback({"ResponseCode": "0000", "Data": {
            "ClientReference": tx.client_reference, "Amount": 9000000,
        }})
        tx.refresh_from_db()
        order.refresh_from_db()
        self.assertIsNotNone(tx.callback_seen_at)
        self.assertNotEqual(order.payment_status, "paid")

    @patch("hubtel_direct.client.requests.get")
    @patch("hubtel_direct.client.requests.post")
    def test_independent_paid_status_posts_order_once(self, post, get):
        post.side_effect = self.accepted
        order = self.order()
        tx = service.create_collection(self.customer, order.pk, "mtn-gh")
        DirectTransaction.objects.filter(pk=tx.pk).update(
            created_at=timezone.now() - timedelta(minutes=6),
            next_check_at=timezone.now() - timedelta(seconds=1),
        )
        get.return_value = Mock(status_code=200, json=lambda: {
            "responseCode": "0000", "data": {
                "clientReference": tx.client_reference, "transactionId": "provider-123",
                "status": "Paid", "amount": str(tx.amount), "paymentMethod": "mobilemoney",
            },
        })
        service.reconcile(tx.client_reference)
        service.reconcile(tx.client_reference)
        tx.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(tx.status, "success")
        self.assertEqual(order.payment_status, "paid")
        get.assert_called_once()

    @patch("hubtel_direct.client.requests.get")
    @patch("hubtel_direct.client.requests.post")
    def test_mismatched_amount_never_marks_paid(self, post, get):
        post.side_effect = self.accepted
        order = self.order()
        tx = service.create_collection(self.customer, order.pk, "mtn-gh")
        DirectTransaction.objects.filter(pk=tx.pk).update(
            created_at=timezone.now() - timedelta(minutes=6),
            next_check_at=timezone.now() - timedelta(seconds=1),
        )
        get.return_value = Mock(status_code=200, json=lambda: {
            "responseCode": "0000", "data": {
                "clientReference": tx.client_reference, "transactionId": "provider-123",
                "status": "Paid", "amount": str(tx.amount + Decimal("1.00")),
            },
        })
        service.reconcile(tx.client_reference)
        tx.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(tx.status, "attention")
        self.assertNotEqual(order.payment_status, "paid")

    @patch("hubtel_direct.client.requests.post")
    def test_payout_requires_second_person_and_uses_distinct_disbursement_account(self, post):
        post.side_effect = self.accepted
        second = User.objects.create_superuser("second-controller", "second@example.test", "another-long-password")
        key = uuid.uuid4()
        tx = service.create_payout(
            self.staff, "send_bank", "10.00", "0123456789", "Supplier One",
            bank_code="300311", idempotency_key=key, purpose="Approved test supplier invoice",
        )
        repeated = service.create_payout(
            self.staff, "send_bank", "10.00", "0123456789", "Supplier One",
            bank_code="300311", idempotency_key=key, purpose="Approved test supplier invoice",
        )
        self.assertEqual(tx.pk, repeated.pk)
        with self.assertRaises(ValidationError):
            service.create_payout(
                self.staff, "send_bank", "11.00", "0123456789", "Supplier One",
                bank_code="300311", idempotency_key=key, purpose="Approved test supplier invoice",
            )
        with self.assertRaises(PermissionDenied):
            service.approve_payout(tx.pk, self.staff)
        self.assertEqual(tx.status, "approval_pending")
        post.assert_not_called()
        done = service.approve_payout(tx.pk, second)
        self.assertEqual(done.status, "pending")
        self.assertIn("/api/merchants/67890/send/bank/gh/300311", post.call_args.args[0])
        self.assertNotIn("0123456789", done.destination_encrypted)
        with self.assertRaises(ValidationError):
            service.approve_payout(tx.pk, second)
        post.assert_called_once()

    @patch("hubtel_direct.client.requests.post")
    def test_second_approver_cannot_exceed_company_daily_limit(self, post):
        approver = User.objects.create_superuser(
            "bank-approver", "bank-approver@example.test", "other-strong-test-password"
        )
        tx = service.create_payout(
            self.staff, "send_bank", "10.00", "0123456789", "Supplier One",
            bank_code="300311", idempotency_key=uuid.uuid4(),
            purpose="Approved supplier invoice",
        )
        with override_settings(HUBTEL_DIRECT_DAILY_PAYOUT_GHS="5.00"):
            with self.assertRaises(ValidationError):
                service.approve_payout(tx.pk, approver)
        tx.refresh_from_db()
        self.assertEqual(tx.status, "approval_pending")
        post.assert_not_called()

    def test_protected_internal_reconciliation_denies_missing_secret(self):
        response = self.client.post("/internal/hubtel-direct/reconcile/", content_type="application/json")
        self.assertEqual(response.status_code, 403)

    @patch("hubtel_direct.service.reconcile_due", return_value=2)
    def test_internal_reconcile_uses_shared_secret_not_customer_cookie(self, reconcile):
        secret = "secure-reconcile-test-secret-" + ("x" * 60)
        with override_settings(HUBTEL_DIRECT_RECONCILE_TOKEN=secret):
            wrong = self.client.post(
                "/internal/hubtel-direct/reconcile/",
                HTTP_X_KOFAD_DIRECT_RECONCILE="wrong", content_type="application/json",
            )
            self.assertEqual(wrong.status_code, 403)
            good = self.client.post(
                "/internal/hubtel-direct/reconcile/",
                HTTP_X_KOFAD_DIRECT_RECONCILE=secret, content_type="application/json",
            )
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.json()["checked"], 2)
        reconcile.assert_called_once_with(limit=10)

    @patch("hubtel_direct.client.requests.post")
    def test_bank_payout_invalid_code_and_limit_blocked(self, post):
        for params in [
            ("send_bank", "10.00", "0123456789", "Name", "", "999999"),
            ("send_bank", "501.00", "0123456789", "Name", "", "300311"),
            ("send_momo", "10.00", "123", "Name", "mtn-gh", ""),
        ]:
            with self.assertRaises(ValidationError):
                service.create_payout(self.staff, *params, idempotency_key=uuid.uuid4(), purpose="Approved test supplier invoice")
        post.assert_not_called()
