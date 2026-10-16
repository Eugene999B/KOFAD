"""Hubtel Direct Debit prompt: explicit consent, provider USSD/OTP, no unrelated name API."""
from unittest.mock import patch

from decimal import Decimal
from datetime import timedelta
import uuid

from django.test import override_settings, TestCase
from django.utils import timezone
from core.models import Document
from core.tests import Fixtures
from .models import OnlineDebtSettings
from .test_mandates import READY


# Reuse the established authorized-finance customer debt fixture; each case
# has a distinct transaction and all external provider calls are mocked.
@override_settings(**READY)
class PreapprovalPromptFlowTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        OnlineDebtSettings.objects.update_or_create(
            branch=self.branch, defaults={"plan_creation_enabled": True},
        )
        self.customer.phone = "+233241234567"
        self.customer.consent = True
        self.customer.save(update_fields=["phone", "consent"])
        Document.objects.create(
            id=uuid.uuid4(), branch=self.branch, party=self.customer,
            kind="sale", total=Decimal("800.00"), paid=Decimal("0.00"),
            due_date=timezone.localdate() + timedelta(days=7),
            reference="DEBT-PREAPPROVAL-TEST", created_by=self.user,
        )

    def plan(self):
        from . import mandates
        return mandates.create_plan(
            self.user, self.branch,
            party_id=self.customer.pk, phone="0241234567",
            channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=timezone.localdate() + timedelta(days=7),
            amount_per_period="200", maximum_total="800",
        )

    def _accepted(self, method="USSD"):
        return {
            "provider_id": "provider-prompt-test-001",
            "verification_type": method,
            "otp_prefix": "ABCD" if method == "OTP" else "",
            "status": "PENDING",
        }

    def test_staff_can_initiate_after_explicit_consent_without_wallet_name_api(self):
        plan = self.plan()
        self.authenticate_client()
        with patch("hubtel_direct.verification.inquiry", side_effect=AssertionError("name API must not run")):
            with patch("hubtel_direct.debit_api.initiate_preapproval", return_value=self._accepted()) as provider:
                page = self.client.post(
                    f"/debts/hubtel/mandates/{plan.pk}/prompt/",
                    {"customer_requested_prompt": "yes", "customer_reviewed_schedule": "yes"},
                    HTTP_HOST="staff.kofadimpex.com",
                )
        self.assertEqual(page.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.status, "pending")
        self.assertEqual(plan.verification_type, "USSD")
        self.assertFalse(bool(plan.provider_approved_at))
        provider.assert_called_once()

    def test_staff_not_allowed_to_start_without_explicit_consent(self):
        plan = self.plan()
        self.authenticate_client()
        with patch("hubtel_direct.debit_api.initiate_preapproval") as provider:
            page = self.client.post(
                f"/debts/hubtel/mandates/{plan.pk}/prompt/",
                {"customer_requested_prompt": "yes"},
                HTTP_HOST="staff.kofadimpex.com",
            )
        self.assertEqual(page.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.status, "draft")
        provider.assert_not_called()

    def test_customer_approval_starts_directly_after_terms_without_name_api(self):
        plan = self.plan()
        with patch("hubtel_direct.verification.inquiry", side_effect=AssertionError("name API must not run")):
            with patch("hubtel_direct.debit_api.initiate_preapproval", return_value=self._accepted("OTP")) as provider:
                response = self.client.post(
                    f"/market/debt-authorization/{plan.pk}/action/",
                    {"action": "start", "accepted_terms": "yes"},
                )
        self.assertEqual(response.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.status, "pending")
        self.assertEqual(plan.verification_type, "OTP")
        self.assertFalse(bool(plan.provider_approved_at))
        provider.assert_called_once()
        self.assertEqual(
            self.client.post(
                f"/market/debt-authorization/{plan.pk}/action/",
                {"action": "start", "accepted_terms": "yes"},
            ).status_code,
            302,
        )
        provider.assert_called_once()

    def test_customer_screen_offers_immediate_hubtel_approval_not_a_name_lookup(self):
        plan = self.plan()
        response = self.client.get(f"/market/debt-authorization/{plan.pk}/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Request Mobile Money approval", html)
        self.assertNotIn("Verify number and name", html)
        self.assertNotIn("data-direct-registered-name", html)
