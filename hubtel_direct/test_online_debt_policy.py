"""Offline regression for management-controlled online debt collections."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
import uuid

from django.core.exceptions import ValidationError
from django.forms.models import model_to_dict
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Document
from core.tests import Fixtures
from . import mandates, debit_api
from .models import DebtDebitAttempt, DebtMandate, OnlineDebtSettings
from .policy_forms import OnlineDebtSettingsForm


CONFIGURED = {
    "HUBTEL_API_ID": "offline-test-id",
    "HUBTEL_API_KEY": "offline-test-key",
    "HUBTEL_COLLECTION_ACCOUNT": "12345",
    "HUBTEL_DIRECT_EGRESS_CONFIRMED": True,
    "HUBTEL_DIRECT_RECEIVE_ENABLED": True,
    "HUBTEL_DEBT_PREAPPROVAL_ENABLED": True,
    "HUBTEL_DEBT_AUTO_DEBIT_ENABLED": True,
    "MARKET_SITE_ORIGIN": "https://market.kofadimpex.com",
}


@override_settings(**CONFIGURED)
class OnlineDebtPolicyTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.consent = True
        self.customer.phone = "+233241234567"
        self.customer.save(update_fields=["consent", "phone"])
        Document.objects.create(
            id=uuid.uuid4(), branch=self.branch, party=self.customer,
            kind="sale", total=Decimal("800.00"), paid=Decimal("0.00"),
            due_date=timezone.localdate() + timedelta(days=7),
            reference="ONLINE-DEBT-SETTINGS-TEST", created_by=self.user,
        )
        self.policy = OnlineDebtSettings.objects.create(branch=self.branch)

    def draft(self, **kwargs):
        args = {
            "party_id": self.customer.pk, "phone": "0241234567",
            "channel": "mtn-gh-direct-debit", "frequency": "weekly",
            "first_due_date": timezone.localdate(), "amount_per_period": "200",
            "maximum_total": "800",
        }
        args.update(kwargs)
        return mandates.create_plan(self.user, self.branch, **args)

    def test_policies_allow_drafts_by_default_but_still_pause_all_charges(self):
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.plan_creation_enabled)
        self.assertTrue(self.policy.allow_daily)
        self.assertTrue(self.policy.allow_yearly)
        self.assertEqual(self.policy.min_open_balance, Decimal("1.00"))
        self.assertEqual(self.policy.min_installment, Decimal("1.00"))
        self.assertTrue(self.policy.pause_auto_collection)
        drafted = self.draft()
        self.assertEqual(drafted.status, "draft")
        self.assertFalse(DebtDebitAttempt.objects.exists())

    def test_manager_may_explicitly_pause_new_plan_creation(self):
        self.policy.plan_creation_enabled = False
        self.policy.save(update_fields=["plan_creation_enabled"])
        with self.assertRaisesRegex(ValidationError, "disabled"):
            self.draft()
        self.assertFalse(DebtMandate.objects.exists())

    @patch("hubtel_direct.debit_api.initiate_preapproval")
    @patch("hubtel_direct.debit_api.debit_charge")
    def test_draft_can_be_edited_without_preapproving_or_charging(self, debit, preapprove):
        row = self.draft()
        initial_reference = row.client_reference
        edited = mandates.update_draft_plan(
            self.user, self.branch, row.pk, phone="",
            channel="mtn-gh-direct-debit", frequency="monthly",
            first_due_date=timezone.localdate() + timedelta(days=14),
            amount_per_period="100.00", maximum_total="600.00",
        )
        self.assertEqual(edited.pk, row.pk)
        self.assertEqual(edited.status, "draft")
        self.assertEqual(edited.frequency, "monthly")
        self.assertEqual(edited.amount_per_period, Decimal("100.00"))
        self.assertEqual(edited.maximum_total, Decimal("600.00"))
        self.assertNotEqual(edited.client_reference, initial_reference)
        self.assertEqual(edited.terms_snapshot["frequency"], "monthly")
        preapprove.assert_not_called()
        debit.assert_not_called()

    def test_authorized_or_pending_terms_cannot_be_silently_revised(self):
        row = self.draft()
        DebtMandate.objects.filter(pk=row.pk).update(status="pending")
        with self.assertRaisesRegex(ValidationError, "Cancel it"):
            mandates.update_draft_plan(
                self.user, self.branch, row.pk,
                frequency="daily", first_due_date=timezone.localdate(),
                amount_per_period="100", maximum_total="600",
            )
        row.refresh_from_db()
        self.assertEqual(row.frequency, "weekly")

    def test_staff_edits_must_remain_within_outstanding_and_configured_limits(self):
        row = self.draft()
        for amount, ceiling in [("900", "900"), ("200", "900"), ("0.01", "800")]:
            with self.subTest(amount=amount, ceiling=ceiling):
                with self.assertRaises(ValidationError):
                    mandates.update_draft_plan(
                        self.user, self.branch, row.pk,
                        frequency="daily", first_due_date=timezone.localdate(),
                        amount_per_period=amount, maximum_total=ceiling,
                    )
        row.refresh_from_db()
        self.assertEqual(row.amount_per_period, Decimal("200.00"))

    def test_disabled_frequency_network_and_limits_are_enforced(self):
        self.policy.plan_creation_enabled = True
        self.policy.allow_daily = False
        self.policy.max_open_balance = Decimal("500")
        self.policy.save()
        with self.assertRaisesRegex(ValidationError, "frequency"):
            self.draft(frequency="daily")
        with self.assertRaisesRegex(ValidationError, "eligibility"):
            self.draft()
        self.policy.max_open_balance = Decimal("1000")
        self.policy.max_installment = Decimal("100")
        self.policy.save()
        with self.assertRaisesRegex(ValidationError, "Installment amount"):
            self.draft()
        self.policy.max_installment = Decimal("900")
        self.policy.allow_mtn = False
        self.policy.save()
        with self.assertRaisesRegex(ValidationError, "network"):
            self.draft()

    def test_grace_and_nonrollover_are_part_of_customer_agreement(self):
        self.policy.plan_creation_enabled = True
        self.policy.rollover_unpaid = False
        self.policy.auto_collection_grace_days = 3
        self.policy.save()
        plan = self.draft()
        self.assertFalse(plan.terms_snapshot["rollover_unpaid"])
        self.assertEqual(plan.terms_snapshot["auto_collection_grace_days"], 3)
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        self.assertIsNone(mandates.plan_due_attempt(plan))
        self.assertEqual(mandates.payment_preview(plan, timezone.localdate() + timedelta(days=14)), Decimal("200.00"))
        # Policy edits cannot rewrite a previously accepted agreement.
        self.policy.rollover_unpaid = True
        self.policy.auto_collection_grace_days = 0
        self.policy.save()
        plan.refresh_from_db()
        self.assertFalse(plan.terms_snapshot["rollover_unpaid"])

    def test_staff_form_rejects_unbounded_and_inconsistent_rules(self):
        form = OnlineDebtSettingsForm(data={
            **model_to_dict(self.policy),
            "plan_creation_enabled": True, "allow_weekly": False,
            "default_frequency": "weekly", "collection_from_hour": 22,
            "collection_until_hour": 9, "daily_attempt_limit": 0,
            "min_open_balance": "900", "max_open_balance": "800",
        }, instance=self.policy)
        self.assertFalse(form.is_valid())
        self.assertIn("default_frequency", form.errors)
        self.assertIn("collection_until_hour", form.errors)
        self.assertIn("daily_attempt_limit", form.errors)
        self.assertIn("max_open_balance", form.errors)

    @patch("hubtel_direct.debit_api.debit_charge")
    @patch("hubtel_direct.mandates.refresh_preapproval")
    def test_paused_policy_never_sends_money(self, refresh, send):
        self.policy.plan_creation_enabled = True
        self.policy.save()
        mandate = self.draft()
        DebtMandate.objects.filter(pk=mandate.pk).update(status="approved")
        mandate.refresh_from_db()
        attempt = mandates.plan_due_attempt(mandate)
        refresh.return_value = mandate
        self.assertEqual(mandates.dispatch_planned(), 0)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "planned")
        self.assertIsNone(attempt.sent_at)
        send.assert_not_called()

    def test_verified_failure_advances_due_cycle_without_second_post(self):
        self.policy.plan_creation_enabled = True
        self.policy.save()
        plan = self.draft()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        attempt = mandates.plan_due_attempt(plan)
        DebtDebitAttempt.objects.filter(pk=attempt.pk).update(
            status="pending", sent_at=timezone.now() - timedelta(minutes=10),
            created_at=timezone.now() - timedelta(minutes=10),
            next_check_at=timezone.now() - timedelta(minutes=1),
            hubtel_transaction_id="provider-failed-transaction",
        )
        with patch("hubtel_direct.client.check", return_value=("failed", "provider-failed-transaction")):
            self.assertEqual(mandates.reconcile_attempts(), 0)
        plan.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "failed")
        self.assertEqual(plan.next_due_date, timezone.localdate() + timedelta(days=7))

    @patch("hubtel_direct.debit_api._request")
    def test_cancel_after_scheduling_cannot_be_sent_from_stale_object(self, request):
        self.policy.plan_creation_enabled = True
        self.policy.pause_auto_collection = False
        self.policy.save()
        plan = self.draft()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        attempt = mandates.plan_due_attempt(plan)
        DebtDebitAttempt.objects.filter(pk=attempt.pk).update(
            status="sending", sent_at=timezone.now(),
        )
        stale = DebtDebitAttempt.objects.select_related("mandate").get(pk=attempt.pk)
        self.assertEqual(stale.mandate.status, "approved")
        DebtMandate.objects.filter(pk=plan.pk).update(status="cancel_pending")
        with self.assertRaisesRegex(ValidationError, "current approved mandate"):
            debit_api.debit_charge(stale)
        request.assert_not_called()

    @patch("hubtel_direct.mandates.timezone.localdate")
    def test_provider_paid_finance_review_stops_new_due_attempt(self, localdate):
        today = timezone.localtime(timezone.now()).date()
        localdate.return_value = today
        self.policy.plan_creation_enabled = True
        self.policy.save()
        plan = self.draft()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        old_attempt = mandates.plan_due_attempt(plan)
        DebtDebitAttempt.objects.filter(pk=old_attempt.pk).update(status="finance_review")
        DebtMandate.objects.filter(pk=plan.pk).update(next_due_date=today + timedelta(days=7))
        localdate.return_value = today + timedelta(days=7)
        plan.refresh_from_db()
        self.assertIsNone(mandates.plan_due_attempt(plan))
        self.assertEqual(DebtDebitAttempt.objects.filter(mandate=plan).count(), 1)

    @patch("hubtel_direct.mandates.client.check", return_value=("pending", "hubtel-unpaid-status"))
    def test_old_pending_status_suspends_debits_but_keeps_polling_same_reference(self, check):
        self.policy.plan_creation_enabled = True
        self.policy.pending_review_hours = 6
        self.policy.unresolved_poll_hours = 3
        self.policy.save()
        plan = self.draft()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        attempt = mandates.plan_due_attempt(plan)
        earlier = timezone.now() - timedelta(hours=7)
        DebtDebitAttempt.objects.filter(pk=attempt.pk).update(
            status="pending", sent_at=earlier, created_at=earlier,
            next_check_at=timezone.now() - timedelta(minutes=1),
            hubtel_transaction_id="hubtel-unpaid-status",
        )
        self.assertEqual(mandates.reconcile_attempts(), 0)
        plan.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(plan.status, "suspended")
        self.assertEqual(attempt.status, "pending")
        self.assertGreater(attempt.next_check_at, timezone.now() + timedelta(hours=2))
        check.assert_called_once()
        self.assertIsNone(mandates.plan_due_attempt(plan))

    @override_settings(
        HUBTEL_DEBT_AUTO_DEBIT_ENABLED=False,
        HUBTEL_DIRECT_RECEIVE_ENABLED=False,
    )
    def test_provider_paid_recovers_ledger_without_a_second_wallet_charge(self):
        """Hubtel charge gate OFF must not strand a previously paid receipt."""
        self.policy.plan_creation_enabled = True
        self.policy.save()
        plan = self.draft()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        attempt = mandates.plan_due_attempt(plan)
        sent = timezone.now() - timedelta(minutes=10)
        DebtDebitAttempt.objects.filter(pk=attempt.pk).update(
            status="pending", sent_at=sent, created_at=sent, next_check_at=sent,
        )
        with patch("hubtel_direct.client.check", return_value=("success", "verified-provider-payment")) as get, \
             patch("hubtel_direct.debit_api.debit_charge") as send:
            self.assertEqual(mandates.reconcile_attempts(), 1)
            self.assertEqual(mandates.post_verified_collections(), 0)
        get.assert_called_once()
        send.assert_not_called()
        attempt.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual(attempt.status, "settled")
        self.assertIsNotNone(attempt.provider_verified_paid_at)
        self.assertIsNotNone(attempt.collection_document_id)
        self.assertEqual(plan.collected_total, Decimal("200.00"))
        self.assertEqual(attempt.collection_document.total, Decimal("200.00"))
        self.assertEqual(mandates.post_verified_collections(), 0)

    @override_settings(
        HUBTEL_DEBT_AUTO_DEBIT_ENABLED=False,
        HUBTEL_DIRECT_RECEIVE_ENABLED=False,
    )
    def test_provider_paid_unposted_receipt_can_recover_locally_after_error(self):
        """Only verified paid evidence can recover; never probe wallet again."""
        self.policy.plan_creation_enabled = True
        self.policy.save()
        plan = self.draft()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        attempt = mandates.plan_due_attempt(plan)
        sent = timezone.now() - timedelta(minutes=10)
        DebtDebitAttempt.objects.filter(pk=attempt.pk).update(
            status="pending", sent_at=sent, created_at=sent, next_check_at=sent,
        )
        with patch("hubtel_direct.client.check", return_value=("success", "verified-provider-payment")), \
             patch("core.debts.post_customer_payment", side_effect=ValidationError("Ledger temporarily unavailable")):
            self.assertEqual(mandates.reconcile_attempts(), 0)
        attempt.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual(attempt.status, "finance_review")
        self.assertIsNotNone(attempt.provider_verified_paid_at)
        self.assertEqual(plan.status, "suspended")
        self.assertIsNone(attempt.collection_document_id)
        with patch("hubtel_direct.client.check", side_effect=AssertionError("Do not recheck to retry posting")), \
             patch("hubtel_direct.debit_api.debit_charge", side_effect=AssertionError("Never send again")):
            self.assertEqual(mandates.post_verified_collections(), 1)
            self.assertEqual(mandates.post_verified_collections(), 0)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "settled")
        self.assertIsNotNone(attempt.collection_document_id)

    def test_unknown_finance_review_without_paid_evidence_cannot_auto_credit(self):
        self.policy.plan_creation_enabled = True
        self.policy.save()
        plan = self.draft()
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        attempt = mandates.plan_due_attempt(plan)
        DebtDebitAttempt.objects.filter(pk=attempt.pk).update(status="finance_review")
        with patch("core.debts.post_customer_payment") as post:
            self.assertEqual(mandates.post_verified_collections(), 0)
        post.assert_not_called()

