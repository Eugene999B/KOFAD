"""Offline safety regression for customer-authorized collection mandates."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
import uuid

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Document
from core.tests import Fixtures
from . import client, mandates, verification
from .models import DebtMandate, DebtDebitAttempt


READY = {
    "HUBTEL_API_ID": "test-api-id",
    "HUBTEL_API_KEY": "test-api-key",
    "HUBTEL_COLLECTION_ACCOUNT": "12345",
    "HUBTEL_DIRECT_EGRESS_CONFIRMED": True,
    "HUBTEL_DIRECT_RECEIVE_ENABLED": True,
    "HUBTEL_DEBT_PREAPPROVAL_ENABLED": True,
    "HUBTEL_DEBT_AUTO_DEBIT_ENABLED": False,
    "HUBTEL_REFUNDS_ENABLED": False,
    "MARKET_SITE_ORIGIN": "https://market.kofadimpex.com",
}


@override_settings(**READY)
class DebtMandateTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone = "+233241234567"
        self.customer.consent = True
        self.customer.save(update_fields=["phone", "consent"])
        Document.objects.create(
            id=uuid.uuid4(), branch=self.branch, party=self.customer,
            kind="sale", total=Decimal("800.00"), paid=Decimal("0.00"),
            due_date=timezone.localdate() + timedelta(days=7),
            reference="DEBT-DIRECT-TEST", created_by=self.user,
        )

    def plan(self, **kwargs):
        kw = dict(
            party_id=self.customer.pk, phone="0241234567",
            channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=timezone.localdate() + timedelta(days=7),
            amount_per_period="200", maximum_total="800",
        )
        kw.update(kwargs)
        return mandates.create_plan(self.user, self.branch, **kw)

    def test_draft_prevents_any_money_movement(self):
        plan = self.plan()
        self.assertEqual(plan.status, "draft")
        self.assertEqual(plan.phone_suffix, "4567")
        self.assertNotIn("233241234567", plan.phone_encrypted)
        with patch("hubtel_direct.debit_api.requests.request") as outgoing:
            self.assertEqual(mandates.payment_preview(plan, timezone.localdate() + timedelta(days=14)), Decimal("0.00"))
            self.assertIsNone(mandates.plan_due_attempt(plan))
            outgoing.assert_not_called()

    def test_customer_approval_request_is_not_provider_approval(self):
        plan = self.plan()
        with patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
            "provider_id": "provider-a", "verification_type": "USSD",
            "otp_prefix": "", "status": "PENDING",
        }) as initiated:
            mandates.prepare_customer_authorization(plan)
        plan.refresh_from_db()
        self.assertEqual(plan.status, "pending")
        self.assertEqual(initiated.call_count, 1)
        with patch("hubtel_direct.debit_api.preapproval_status", return_value="APPROVED") as check:
            plan = mandates.refresh_preapproval(plan)
        self.assertEqual(plan.status, "approved")
        self.assertIsNotNone(plan.provider_approved_at)
        check.assert_called_once()

    def test_cancel_unapproved_draft_cannot_cancel_wallet_at_hubtel(self):
        plan = self.plan()
        with patch("hubtel_direct.debit_api.cancel_preapproval") as provider:
            row = mandates.request_cancel(plan)
        self.assertEqual(row.status, "cancelled")
        provider.assert_not_called()

    def test_approved_cancel_immediately_blocks_next_debit(self):
        plan = self.plan()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        with patch("hubtel_direct.debit_api.cancel_preapproval", side_effect=client.StatusUnconfirmed):
            row = mandates.request_cancel(plan)
        self.assertEqual(row.status, "cancel_pending")
        self.assertEqual(mandates.payment_preview(row, timezone.localdate() + timedelta(days=21)), Decimal("0.00"))

    def test_one_active_mandate_per_wallet_and_customer(self):
        self.plan()
        with self.assertRaises(ValidationError):
            self.plan()
        # Different debtor may not bind an already-active wallet to a
        # separate commitment under the same Hubtel merchant account.

    def test_installment_arrears_never_exceed_contract_ceiling_or_real_debt(self):
        plan = self.plan(first_due_date=timezone.localdate())
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        future = timezone.localdate() + timedelta(days=14)
        self.assertEqual(mandates.payment_preview(plan, future), Decimal("600.00"))
        plan.collected_total = Decimal("150.00")
        self.assertEqual(mandates.payment_preview(plan, future), Decimal("450.00"))
        plan.maximum_total = Decimal("400.00")
        self.assertEqual(mandates.payment_preview(plan, future), Decimal("250.00"))

    def test_partial_wallet_balance_is_not_probed_by_rapid_automatic_retries(self):
        plan = self.plan(first_due_date=timezone.localdate(),
                         allow_one_partial_retry=True, minimum_partial="100")
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        first = mandates.plan_due_attempt(plan)
        self.assertEqual(first.amount, Decimal("200.00"))
        self.assertIsNone(mandates.plan_due_attempt(plan))
        first.status = "failed"
        first.save(update_fields=["status"])
        self.assertIsNone(mandates.plan_due_attempt(plan))

    def test_direct_debit_charge_disabled_until_explicit_live_activation(self):
        plan = self.plan()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        with patch("hubtel_direct.debit_api.requests.request") as provider:
            with self.assertRaises(ValidationError):
                mandates.enqueue_due()
            provider.assert_not_called()

    @patch("hubtel_direct.debit_api.cancel_preapproval")
    def test_uncertain_cancellation_retries_only_after_cooldown(self, provider):
        plan = self.plan()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        provider.side_effect = [client.StatusUnconfirmed("network timeout"), True]
        first = mandates.request_cancel(plan)
        self.assertEqual(first.status, "cancel_pending")
        # A second click cannot flood the provider with revoke requests.
        second = mandates.request_cancel(first)
        self.assertEqual(second.status, "cancel_pending")
        self.assertEqual(provider.call_count, 1)
        DebtMandate.objects.filter(pk=plan.pk).update(
            updated_at=timezone.now() - timedelta(minutes=16),
        )
        self.assertEqual(mandates.cancel_fully_paid(limit=2), 1)
        plan.refresh_from_db()
        self.assertEqual(plan.status, "cancelled")
        self.assertEqual(provider.call_count, 2)

    @patch("hubtel_direct.debit_api.cancel_preapproval")
    def test_provider_approval_cannot_override_local_cancellation(self, provider):
        plan = self.plan()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        provider.side_effect = client.StatusUnconfirmed("network timeout")
        mandates.request_cancel(plan)
        plan.refresh_from_db()
        with patch("hubtel_direct.debit_api.preapproval_status", return_value="APPROVED"):
            mandates.refresh_preapproval(plan)
        plan.refresh_from_db()
        self.assertEqual(plan.status, "cancel_pending")
        self.assertEqual(mandates.payment_preview(plan, timezone.localdate() + timedelta(days=8)), Decimal("0.00"))

    @patch("hubtel_direct.verification.requests.get")
    def test_bank_account_name_is_supplied_by_hubtel(self, get):
        get.return_value = Mock(status_code=200, json=lambda: {
            "responseCode": "0000", "data": {"name": "Verified Payroll Beneficiary"},
        })
        result = verification.bank_inquiry("300312", "1234567890")
        self.assertTrue(result["verified"])
        self.assertEqual(result["name"], "Verified Payroll Beneficiary")
        self.assertIn("/v2/merchantaccount/", get.call_args.args[0])
        self.assertEqual(result["account_suffix"], "7890")
