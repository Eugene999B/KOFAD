import os
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Document
from core.tests import Fixtures
from . import client, debt_momo, mandates
from .models import DebtDebitAttempt, DebtMandate, DebtMomoCollection, OnlineDebtSettings
from .test_mandates import READY


@override_settings(**READY)
class DebtCollectionSafetyTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone = "+233241234567"
        self.customer.consent = True
        self.customer.save()
        Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("800.00"), paid=Decimal("0.00"),
            reference="COLLECTION-SAFETY", created_by=self.user,
        )
        OnlineDebtSettings.objects.update_or_create(branch=self.branch, defaults={
            "plan_creation_enabled": True, "pause_auto_collection": False,
            "skip_weekends": False, "collection_from_hour": 0, "collection_until_hour": 24,
        })

    def collection(self):
        earlier = timezone.now() - timedelta(minutes=10)
        row = DebtMomoCollection.objects.create(
            request_key=uuid.uuid4(), branch=self.branch, party=self.customer,
            requested_by=self.user, amount=Decimal("200.00"), channel="mtn-gh",
            phone_encrypted=client.seal("233241234567"), phone_suffix="4567",
            wallet_name="Test Customer", reference=uuid.uuid4().hex,
            status="pending", next_check_at=earlier,
        )
        DebtMomoCollection.objects.filter(pk=row.pk).update(created_at=earlier)
        row.refresh_from_db()
        return row

    def planned_debit(self):
        plan = mandates.create_plan(
            self.user, self.branch, party_id=self.customer.pk,
            phone="0241234567", channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=timezone.localdate(), amount_per_period="200", maximum_total="800",
        )
        DebtMandate.objects.filter(pk=plan.pk).update(status="approved")
        plan.refresh_from_db()
        return mandates.plan_due_attempt(plan)

    @patch("hubtel_direct.client.check", return_value=("pending", "provider-test"))
    def test_repeated_reconciliation_checks_claim_one_poll(self, check):
        row = self.collection()
        debt_momo.reconcile(row.pk)
        debt_momo.reconcile(row.pk)
        check.assert_called_once()

    @patch.dict(os.environ, {"RAILWAY_SERVICE_NAME": "kofad-sms"})
    @patch("hubtel_direct.client.check")
    def test_wrong_egress_does_not_turn_pending_into_attention(self, check):
        row = self.collection()
        with self.assertRaises(ValidationError):
            debt_momo.reconcile(row.pk)
        row.refresh_from_db()
        self.assertEqual(row.status, "pending")
        check.assert_not_called()

    @patch("hubtel_direct.client.check", return_value=("success", "verified-test"))
    def test_verified_collection_posts_one_receipt(self, check):
        row = self.collection()
        self.assertEqual(debt_momo.reconcile(row.pk).status, "success")
        self.assertEqual(debt_momo.reconcile(row.pk).status, "success")
        self.assertEqual(Document.objects.filter(kind="collection").count(), 1)
        check.assert_called_once()

    @patch("hubtel_direct.client.submit")
    @patch("hubtel_direct.verification.inquiry", return_value={"wallet_name": "Test Customer"})
    def test_pending_automatic_debit_blocks_staff_collection(self, inquiry, submit):
        self.planned_debit()
        with self.assertRaisesRegex(ValidationError, "automatic debit remains unresolved"):
            debt_momo.start(
                self.user, self.branch, party_id=self.customer.pk, amount="200.00",
                phone="0241234567", channel="mtn-gh", wallet_name="Test Customer",
                request_key=uuid.uuid4(), consent=True,
            )
        submit.assert_not_called()
        self.assertFalse(DebtMomoCollection.objects.exists())

    @override_settings(HUBTEL_DEBT_AUTO_DEBIT_ENABLED=True)
    @patch("hubtel_direct.debit_api.debit_charge")
    @patch("hubtel_direct.debit_api.preapproval_status", return_value="APPROVED")
    def test_pending_staff_collection_blocks_automatic_debit(self, approval, charge):
        attempt = self.planned_debit()
        self.collection()
        self.assertEqual(mandates.dispatch_planned(), 0)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "planned")
        charge.assert_not_called()
        self.assertEqual(DebtDebitAttempt.objects.count(), 1)
