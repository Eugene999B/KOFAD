"""Direct Debit is customer-approved wallet authority, not an SMS payment method."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
import uuid

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Document
from core.tests import Fixtures
from . import mandates, schedule
from .models import DebtMandate, DebtDebitAttempt, OnlineDebtSettings

PROVIDER = {
    "HUBTEL_API_ID": "offline-test",
    "HUBTEL_API_KEY": "offline-test-key",
    "HUBTEL_COLLECTION_ACCOUNT": "12345",
    "HUBTEL_DIRECT_EGRESS_CONFIRMED": True,
    "HUBTEL_DIRECT_RECEIVE_ENABLED": True,
    "HUBTEL_DEBT_PREAPPROVAL_ENABLED": True,
    "HUBTEL_DEBT_AUTO_DEBIT_ENABLED": False,
    "MARKET_SITE_ORIGIN": "https://market.kofadimpex.com",
}


@override_settings(**PROVIDER)
class DebitAgreementScheduleTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.consent = True
        self.customer.phone = "+233241234567"
        self.customer.save(update_fields=["consent", "phone"])
        self.sale = Document.objects.create(
            id=uuid.uuid4(), branch=self.branch, party=self.customer,
            kind="sale", reference="TEST-HUBTEL-CREDIT-20261013",
            total=Decimal("1000.00"), paid=Decimal("200.00"),
            due_date=timezone.localdate() + timedelta(days=7),
            created_by=self.user,
        )
        self.policy = OnlineDebtSettings.objects.create(branch=self.branch)

    def plan(self, **kwargs):
        start = timezone.localdate() + timedelta(days=7)
        args = dict(
            party_id=self.customer.pk, phone="0241234567",
            channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=start, final_due_date=start + timedelta(days=21),
            collection_hour=9, source_document_id=self.sale.pk,
        )
        args.update(kwargs)
        return mandates.create_plan(self.user, self.branch, **args)

    def test_last_installment_is_rounded_to_exact_unpaid_sale(self):
        start = timezone.localdate() + timedelta(days=7)
        s = schedule.calculate(Decimal("1000.00"), start,
                               start + timedelta(days=14), "weekly", 9)
        self.assertEqual(s["count"], 3)
        self.assertEqual(s["amount"], Decimal("333.34"))
        self.assertEqual(s["last_amount"], Decimal("333.32"))
        self.assertEqual(sum(x["amount"] for x in s["entries"]), Decimal("1000.00"))
        self.assertEqual(s["entries"][0]["date"], start)
        self.assertEqual(s["entries"][-1]["date"], start + timedelta(days=14))

    def test_a_recorded_sale_determines_the_ceiling_and_count(self):
        plan = self.plan(maximum_total="9000.00", amount_per_period="9000.00")
        self.assertEqual(plan.status, "draft")
        self.assertEqual(plan.maximum_total, Decimal("800.00"))
        self.assertEqual(plan.amount_per_period, Decimal("200.00"))
        self.assertEqual(plan.terms_snapshot["scheduled_periods"], 4)
        self.assertEqual(plan.terms_snapshot["source_document_id"], str(self.sale.pk))
        self.assertEqual(plan.terms_snapshot["collection_hour"], 9)
        self.assertEqual(len(schedule.for_mandate(plan)), 4)
        self.assertFalse(DebtDebitAttempt.objects.exists())

    def test_staff_can_track_approval_on_an_explicit_waiting_screen(self):
        plan = self.plan()
        self.authenticate_client()
        page = self.client.get(reverse("hubtel_debt_staff_status", kwargs={"mandate_id":plan.pk}))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Send Hubtel approval prompt")
        self.assertContains(page, "4")
        self.assertContains(page, "200.00")
        self.assertContains(page, "https://market.kofadimpex.com/market/debt-authorization/")
        state = self.client.get(reverse("hubtel_debt_staff_json", kwargs={"mandate_id":plan.pk}))
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()["status"], "draft")

    @patch("marketplace.services.send_otp")
    @patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
        "provider_id": "hubtel-uuid", "verification_type": "USSD",
        "otp_prefix": "", "status": "PENDING",
    })
    def test_customer_accepting_terms_triggers_real_provider_preapproval_not_kofad_otp(self, initiate, sms):
        plan = self.plan()
        route = reverse("hubtel_debt_authorize_action", kwargs={"mandate_id": plan.pk})
        response = self.client.post(route, {"action":"start","accepted_terms":"yes"})
        self.assertEqual(response.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.status, "pending")
        self.assertEqual(plan.verification_type, "USSD")
        initiate.assert_called_once()
        sms.assert_not_called()

    @patch("hubtel_direct.debit_api.initiate_preapproval")
    def test_customer_cannot_initiate_without_reviewing_terms(self, initiate):
        plan = self.plan()
        response = self.client.post(
            reverse("hubtel_debt_authorize_action", kwargs={"mandate_id":plan.pk}),
            {"action":"start"},
        )
        self.assertEqual(response.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.status, "draft")
        initiate.assert_not_called()

    def test_single_debit_only_once_and_schedule_limits(self):
        start = timezone.localdate() + timedelta(days=7)
        s = schedule.calculate("800", start, start, "once", 10)
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["last_amount"], Decimal("800.00"))
