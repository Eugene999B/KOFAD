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

    def test_policies_start_off_and_cannot_create_draft(self):
        self.assertFalse(self.policy.plan_creation_enabled)
        self.assertTrue(self.policy.pause_auto_collection)
        with self.assertRaisesRegex(ValidationError, "disabled"):
            self.draft()
        self.assertFalse(DebtMandate.objects.exists())

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

