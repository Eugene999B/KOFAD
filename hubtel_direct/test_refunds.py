"""No-money regression tests for verified Hubtel full-order refund controls."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from core.models import CustomerReturnRequest, Document
from marketplace.models import MarketPaymentAttempt, MarketReturnRequest, MarketReturnRequestLine
from marketplace.tests import MarketFixtures
from . import client, refunds
from .models import HubtelRefundCase

API_READY = {
    "HUBTEL_REFUNDS_ENABLED": True,
    "HUBTEL_DIRECT_RECEIVE_ENABLED": True,
    "HUBTEL_DIRECT_EGRESS_CONFIRMED": True,
    "HUBTEL_API_ID": "offline-test-id",
    "HUBTEL_API_KEY": "offline-test-key",
    "HUBTEL_COLLECTION_ACCOUNT": "12345",
    "MARKET_SITE_ORIGIN": "https://market.kofadimpex.com",
}


@override_settings(**API_READY)
class HubtelRefundSafetyTests(MarketFixtures):
    def setUp(self):
        super().setUp()
        self.manager = User.objects.create_superuser(
            "refund-manager", "refund-manager@example.test", "refund-manager-password"
        )
        self.manager.access.branches.add(self.branch)
        self.order_row = self.order()
        self.order_row.payment_status = "paid"
        self.order_row.status = "picked_up"
        self.order_row.payment_reference = "HUBTEL-REFUND-ORIGINAL-001"
        self.order_row.payment_channel = "mobile_money"
        self.order_row.paid_at = timezone.now()
        self.order_row.save()
        self.payment = MarketPaymentAttempt.objects.create(
            order=self.order_row, provider="hubtel_direct",
            reference=self.order_row.payment_reference,
            amount=self.order_row.total, currency="GHS", status="success",
            verified_at=timezone.now(),
            verification_summary={
                "flow": "hubtel_direct",
                "hubtel_order_id": "a" * 32,
            },
        )
        original_sale = Document.objects.create(
            branch=self.branch, kind="sale", total=self.order_row.total,
            paid=self.order_row.total, reference="HUBTEL-ORIGINAL-SALE",
            created_by=self.staff,
        )
        core_return = CustomerReturnRequest.objects.create(
            branch=self.branch, sale=original_sale, status="approved",
            requested_by=self.staff, reviewed_by=self.manager, reason="Returned goods",
        )
        self.item = MarketReturnRequest.objects.create(
            order=self.order_row, customer=self.customer,
            core_return_request=core_return, status="processing",
            refund_amount=self.order_row.total, resolution="refund",
            reason="Incorrect product was received.",
        )
        self.line = MarketReturnRequestLine.objects.create(
            request=self.item, order_line=self.order_row.lines.first(),
            quantity=2, condition="sellable",
        )

    def test_case_is_review_only_and_idempotent(self):
        with patch("hubtel_direct.debit_api._request") as network:
            row = refunds.prepare_case(self.staff, self.item, branch=self.branch)
            duplicate = refunds.prepare_case(self.staff, self.item, branch=self.branch)
        self.assertEqual(row.pk, duplicate.pk)
        self.assertEqual(HubtelRefundCase.objects.count(), 1)
        self.assertEqual(row.status, "review_pending")
        self.assertEqual(row.original_hubtel_order_id, "a" * 32)
        network.assert_not_called()

    def test_partial_returns_are_never_sent_as_full_hubtel_refunds(self):
        self.line.quantity = 1
        self.line.save(update_fields=["quantity"])
        with self.assertRaisesRegex(ValidationError, "entire order"):
            refunds.prepare_case(self.staff, self.item, branch=self.branch)
        self.assertEqual(HubtelRefundCase.objects.count(), 0)

    def test_missing_verified_provider_id_may_be_reviewed_but_not_submitted(self):
        self.payment.verification_summary = {"flow": "hubtel_direct"}
        self.payment.save(update_fields=["verification_summary"])
        row = refunds.prepare_case(self.staff, self.item, branch=self.branch)
        self.assertEqual(row.status, "awaiting_order_id")
        with patch("hubtel_direct.debit_api.submit_refund") as send:
            with self.assertRaisesRegex(ValidationError, "not awaiting"):
                refunds.submit_approved_case(self.manager, row.pk, branch=self.branch)
        send.assert_not_called()

    def test_two_person_approval_and_one_time_submission(self):
        row = refunds.prepare_case(self.staff, self.item, branch=self.branch)
        with patch("hubtel_direct.debit_api.submit_refund", return_value={
            "order_id": "a" * 32, "status": "pending",
        }) as submit:
            with self.assertRaisesRegex(ValidationError, "different manager"):
                refunds.submit_approved_case(self.staff, row.pk, branch=self.branch)
            updated = refunds.submit_approved_case(self.manager, row.pk, branch=self.branch)
            with self.assertRaisesRegex(ValidationError, "not awaiting"):
                refunds.submit_approved_case(self.manager, row.pk, branch=self.branch)
        self.assertEqual(updated.status, "provider_pending")
        self.assertIsNotNone(updated.submitted_at)
        self.assertEqual(submit.call_count, 1)

    def test_timeout_is_permanent_unknown_and_never_reposted(self):
        row = refunds.prepare_case(self.staff, self.item, branch=self.branch)
        with patch("hubtel_direct.debit_api.submit_refund", side_effect=client.StatusUnconfirmed("timeout")) as submit:
            updated = refunds.submit_approved_case(self.manager, row.pk, branch=self.branch)
            self.assertEqual(updated.status, "submission_unknown")
            with self.assertRaises(ValidationError):
                refunds.submit_approved_case(self.manager, row.pk, branch=self.branch)
        self.assertEqual(submit.call_count, 1)

    def test_callback_is_only_advisory_never_marks_refund_or_order_paid(self):
        row = refunds.prepare_case(self.staff, self.item, branch=self.branch)
        HubtelRefundCase.objects.filter(pk=row.pk).update(status="provider_pending")
        found = refunds.note_callback({
            "responseCode": "0000",
            "data": {"orderId": "a" * 32, "amount": "999999"},
        })
        self.assertTrue(found)
        row.refresh_from_db()
        self.item.refresh_from_db()
        self.order_row.refresh_from_db()
        self.assertEqual(row.status, "callback_received")
        self.assertIsNone(row.confirmed_at)
        self.assertEqual(self.item.status, "processing")
        self.assertEqual(self.order_row.payment_status, "paid")

    def test_expired_refund_window_blocks_provider_release(self):
        self.order_row.paid_at = timezone.now() - timedelta(days=46)
        self.order_row.save(update_fields=["paid_at"])
        with self.assertRaisesRegex(ValidationError, "45 days"):
            refunds.prepare_case(self.staff, self.item, branch=self.branch)
