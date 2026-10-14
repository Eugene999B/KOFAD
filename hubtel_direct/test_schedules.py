"""Offline Direct Debit dates and amounts; NO merchant wallet operations."""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.models import Document
from core.tests import Fixtures
from . import debit_api, mandates, schedules
from .models import DebtMandate, DebtDebitAttempt, OnlineDebtSettings


class AutomaticScheduleArithmeticTests(SimpleTestCase):
    def test_three_payments_round_last_without_overcharging(self):
        result = schedules.preview(Decimal("800.00"), "weekly", date(2026, 10, 16), "3")
        self.assertEqual(result["amount_per_period"], Decimal("266.67"))
        self.assertEqual(result["last_amount"], Decimal("266.66"))
        self.assertEqual([r["date"] for r in result["schedule"]],
                         ["2026-10-16", "2026-10-23", "2026-10-30"])
        self.assertEqual(sum(Decimal(x["amount"]) for x in result["schedule"]), Decimal("800.00"))

    def test_month_end_and_leap_year_recurrence(self):
        r = schedules.preview("9.00", "monthly", date(2028, 1, 31), 3)
        self.assertEqual([x["date"] for x in r["schedule"]], [
            "2028-01-31", "2028-02-29", "2028-03-29",
        ])

    def test_single_payment_and_invalid_payment_count(self):
        one = schedules.preview("123.45", "once", date(2026, 10, 20), 1)
        self.assertEqual(one["schedule"][0]["amount"], "123.45")
        for count, frequency in [(0,"weekly"),(121,"weekly"),(2,"once"),(4,"unknown"),(2.5,"weekly")]:
            with self.subTest(count=count, frequency=frequency):
                with self.assertRaises(ValidationError):
                    schedules.preview("123.45", frequency, date(2026, 10, 20), count)

    def test_no_more_payments_than_pennies(self):
        with self.assertRaises(ValidationError):
            schedules.preview("0.02", "daily", date(2026, 10, 20), 3)


    def test_date_range_derives_count_and_exact_last_payment(self):
        result = schedules.preview_by_end_date("800.00", "weekly", date(2026, 10, 16), "2026-10-30")
        self.assertEqual(result["installment_count"], 3)
        self.assertEqual(result["requested_end_date"], "2026-10-30")
        self.assertEqual(result["last_amount"], Decimal("266.66"))
        self.assertEqual(result["end_date"], "2026-10-30")
        self.assertEqual(
            sum(Decimal(entry["amount"]) for entry in result["schedule"]),
            Decimal("800.00"),
        )
        # A cutoff that falls between installments cannot add an off-cycle charge.
        shorter = schedules.preview_by_end_date("800.00", "weekly", date(2026, 10, 16), "2026-10-29")
        self.assertEqual(shorter["installment_count"], 2)
        self.assertEqual(shorter["end_date"], "2026-10-23")

    def test_reject_invalid_date_ranges_and_oversized_daily_schedule(self):
        for end in ("2026-10-15", "bad-date", "2038-10-30"):
            with self.subTest(end=end), self.assertRaises(ValidationError):
                schedules.preview_by_end_date("800.00", "weekly", "2026-10-16", end)
        with self.assertRaises(ValidationError):
            schedules.preview_by_end_date("800.00", "daily", "2026-10-16", "2027-04-30")
        with self.assertRaises(ValidationError):
            schedules.preview_by_end_date("800.00", "once", "2026-10-16", "2026-10-17")


class AutomaticScheduleIntegrationTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.consent = True
        self.customer.phone = "+233241234567"
        self.customer.save(update_fields=["consent", "phone"])
        Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("800.00"), paid=Decimal("0.00"),
            due_date=timezone.localdate() + timedelta(days=14),
            reference="AUTO-SCHEDULE-TEST", created_by=self.user,
        )
        OnlineDebtSettings.objects.get_or_create(branch=self.branch)

    def test_staff_count_creates_signed_preview_not_wallet_charge(self):
        first = timezone.localdate() + timedelta(days=7)
        with patch("hubtel_direct.debit_api.initiate_preapproval") as prompt, \
             patch("hubtel_direct.debit_api.debit_charge") as charge:
            plan = mandates.create_plan(
                self.user, self.branch, party_id=self.customer.pk,
                phone="0241234567", channel="mtn-gh-direct-debit",
                frequency="weekly", first_due_date=first, installment_count="3",
            )
        self.assertEqual(plan.amount_per_period, Decimal("266.67"))
        self.assertEqual(plan.maximum_total, Decimal("800.00"))
        self.assertEqual(plan.terms_snapshot["installment_count"], 3)
        self.assertEqual(plan.terms_snapshot["final_installment"], "266.66")
        self.assertEqual(plan.terms_snapshot["last_due_date"], (first + timedelta(days=14)).isoformat())
        self.assertEqual(plan.status, "draft")
        self.assertFalse(DebtDebitAttempt.objects.exists())
        prompt.assert_not_called()
        charge.assert_not_called()

    def test_new_sale_uses_posted_balance_and_automatically_counts_dates(self):
        document = Document.objects.get(reference="AUTO-SCHEDULE-TEST")
        first = timezone.localdate() + timedelta(days=7)
        with patch("hubtel_direct.debit_api.initiate_preapproval") as prompt, \
             patch("hubtel_direct.debit_api.debit_charge") as charge:
            plan = mandates.create_plan(
                self.user, self.branch, party_id=self.customer.pk,
                phone="0241234567", channel="mtn-gh-direct-debit",
                frequency="weekly", first_due_date=first,
                final_due_date=first + timedelta(days=14),
                source_document_id=document.pk,
            )
        self.assertEqual(plan.maximum_total, Decimal("800.00"))
        self.assertEqual(plan.terms_snapshot["installment_count"], 3)
        self.assertEqual(plan.terms_snapshot["source_document_id"], str(document.pk))
        self.assertEqual(plan.terms_snapshot["last_due_date"], (first + timedelta(days=14)).isoformat())
        self.assertEqual(plan.status, "draft")
        prompt.assert_not_called()
        charge.assert_not_called()

    def test_sale_plan_excludes_older_debt_by_invoice_scope(self):
        source = Document.objects.get(reference="AUTO-SCHEDULE-TEST")
        older = Document.objects.create(
            branch=self.branch, party=self.customer, kind="sale",
            total=Decimal("20.00"), paid=Decimal("0.00"),
            reference="OLD-UNPAID-SALE", created_by=self.user,
        )
        first = timezone.localdate() + timedelta(days=7)
        plan = mandates.create_plan(
            self.user, self.branch, party_id=self.customer.pk,
            phone="0241234567", channel="mtn-gh-direct-debit",
            frequency="weekly", first_due_date=first,
            final_due_date=first + timedelta(days=7),
            source_document_id=source.pk,
        )
        self.assertEqual(plan.maximum_total, Decimal("800.00"))
        self.assertEqual(mandates.mandate_outstanding(plan), Decimal("800.00"))
        self.assertEqual(plan.terms_snapshot["source_document_id"], str(source.pk))
        from core.services import balance
        self.assertEqual(balance(older), Decimal("20.00"))

    def test_collections_do_not_create_extra_payment_after_signed_final_date(self):
        first = timezone.localdate() + timedelta(days=7)
        plan = mandates.create_plan(
            self.user, self.branch, party_id=self.customer.pk,
            phone="0241234567", channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=first, final_due_date=first + timedelta(days=7),
        )
        plan.status = "approved"
        plan.next_due_date = first + timedelta(days=14)
        plan.save(update_fields=["status", "next_due_date"])
        # Move the clock beyond the final signed installment, otherwise this
        # would be an early/no-op attempt rather than an overrun of the mandate.
        with patch("hubtel_direct.mandates.timezone.localdate", return_value=first + timedelta(days=21)):
            self.assertIsNone(mandates.plan_due_attempt(plan))
        plan.refresh_from_db()
        self.assertEqual(plan.status, "suspended")
        self.assertFalse(DebtDebitAttempt.objects.exists())

    def test_customer_authorization_has_real_hubtel_start_not_kofad_ownership_code(self):
        from django.urls import reverse
        plan = mandates.create_plan(
            self.user, self.branch, party_id=self.customer.pk,
            phone="0241234567", channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=timezone.localdate() + timedelta(days=7), installment_count="3",
        )
        response = self.client.get(reverse("hubtel_debt_authorize", args=[plan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="action" value="start"')
        self.assertContains(response, "Hubtel, not KOFAD SMS")
        self.assertNotContains(response, "Send my six-digit code")

    def test_agreement_sms_is_disclosure_only_not_hubtel_authorization(self):
        from django.urls import reverse
        plan = mandates.create_plan(
            self.user, self.branch, party_id=self.customer.pk,
            phone="0241234567", channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=timezone.localdate() + timedelta(days=7),
            final_due_date=timezone.localdate() + timedelta(days=21),
        )
        self.authenticate_client()
        url = reverse("hubtel_debt_staff_send_agreement", args=[plan.pk])
        with patch("hubtel_direct.mandate_views.sms_service.send_transactional_sms",
                   return_value="accepted") as sms, \
             patch("hubtel_direct.debit_api.initiate_preapproval") as hubtel, \
             patch("hubtel_direct.debit_api.debit_charge") as debit:
            result = self.client.post(url)
            self.assertEqual(result.status_code, 302)
            sms.assert_called_once()
            body = sms.call_args.args[1]
            self.assertIn("collection(s)", body)
            self.assertIn("Collection window:", body)
            self.assertIn("This SMS is not authorization", body)
            self.assertIn("Hubtel's USSD/OTP", body)
            self.assertIn(str(plan.pk), body)
            hubtel.assert_not_called()
            debit.assert_not_called()
        plan.refresh_from_db()
        self.assertEqual(plan.status, "draft")
        self.customer.consent = False
        self.customer.save(update_fields=["consent"])
        with patch("hubtel_direct.mandate_views.sms_service.send_transactional_sms") as sms:
            self.client.post(url)
            sms.assert_not_called()

    def _credit_sale_payload(self):
        sale = Document.objects.get(reference="AUTO-SCHEDULE-TEST")
        first = timezone.localdate() + timedelta(days=7)
        return {
            "party": self.customer.pk,
            "source_document_id": str(sale.pk),
            "phone": "0241234567",
            "channel": "mtn-gh-direct-debit",
            "frequency": "weekly",
            "first_due_date": first.isoformat(),
            "final_due_date": (first + timedelta(days=14)).isoformat(),
            "customer_requested_prompt": "yes",
            "customer_reviewed_schedule": "yes",
        }

    def test_pos_mandate_returns_honest_disabled_state_without_hubtel_or_debit(self):
        from django.urls import reverse
        self.authenticate_client()
        data = self._credit_sale_payload()
        with patch("hubtel_direct.debit_api.initiate_preapproval") as preapproval, \
             patch("hubtel_direct.debit_api.debit_charge") as debit:
            response = self.client.post(reverse("hubtel_debt_create"), json.dumps(data),
                                        content_type="application/json")
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["authorization_status"], "draft")
        self.assertEqual(body["prompt_state"], "unavailable")
        self.assertIn("No wallet prompt was sent", body["message"])
        self.assertIn("/status/", body["staff_url"])
        self.assertEqual(DebtMandate.objects.count(), 1)
        self.assertEqual(Document.objects.filter(reference="AUTO-SCHEDULE-TEST").count(), 1)
        preapproval.assert_not_called()
        debit.assert_not_called()

    @override_settings(
        HUBTEL_API_ID="offline-test-id", HUBTEL_API_KEY="offline-test-key",
        HUBTEL_COLLECTION_ACCOUNT="12345", HUBTEL_DIRECT_EGRESS_CONFIRMED=True,
        HUBTEL_DIRECT_RECEIVE_ENABLED=False, HUBTEL_DEBT_PREAPPROVAL_ENABLED=True,
        MARKET_SITE_ORIGIN="https://market.kofadimpex.com",
    )
    def test_pos_sends_one_real_preapproval_and_never_debits(self):
        from django.urls import reverse
        self.authenticate_client()
        data = self._credit_sale_payload()
        with patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
            "provider_id": "hubtel-authorization-one", "verification_type": "USSD",
            "otp_prefix": "", "status": "PENDING"
        }) as hubtel, patch("hubtel_direct.debit_api.debit_charge") as debit:
            first = self.client.post(reverse("hubtel_debt_create"), json.dumps(data),
                                     content_type="application/json")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(first.json()["authorization_status"], "pending")
            self.assertEqual(first.json()["prompt_state"], "pending")
            again = self.client.post(reverse("hubtel_debt_create"), json.dumps(data),
                                     content_type="application/json")
            self.assertEqual(again.status_code, 202)
            self.assertEqual(again.json()["prompt_state"], "pending")
            hubtel.assert_called_once()
            debit.assert_not_called()
        plan = DebtMandate.objects.get()
        self.assertEqual(plan.status, "pending")
        self.assertIsNone(plan.provider_approved_at)
        self.assertEqual(plan.terms_snapshot["installment_count"], 3)

    @override_settings(
        HUBTEL_API_ID="offline-test-id", HUBTEL_API_KEY="offline-test-key",
        HUBTEL_COLLECTION_ACCOUNT="12345", HUBTEL_DIRECT_EGRESS_CONFIRMED=True,
        HUBTEL_DIRECT_RECEIVE_ENABLED=True, HUBTEL_DEBT_PREAPPROVAL_ENABLED=True,
        MARKET_SITE_ORIGIN="https://market.kofadimpex.com",
    )
    def test_hubtel_definite_rejection_keeps_sale_and_allows_corrected_retry(self):
        from django.urls import reverse
        self.authenticate_client()
        data = self._credit_sale_payload()
        url = reverse("hubtel_debt_create")
        with patch("hubtel_direct.debit_api.initiate_preapproval",
                   side_effect=debit_api.ProviderDenied("Hubtel IP is not allowlisted")):
            first = self.client.post(url, json.dumps(data), content_type="application/json")
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()["authorization_status"], "draft")
        self.assertEqual(first.json()["prompt_state"], "rejected")
        self.assertIn("IP is not allowlisted", first.json()["message"])
        self.assertEqual(DebtMandate.objects.count(), 1)
        with patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
            "provider_id": "authorized-on-retry", "verification_type": "USSD",
            "otp_prefix": "", "status": "PENDING"
        }) as hubtel:
            retry = self.client.post(url, json.dumps(data), content_type="application/json")
            self.assertEqual(retry.json()["authorization_status"], "pending")
            hubtel.assert_called_once()
        self.assertEqual(Document.objects.filter(reference="AUTO-SCHEDULE-TEST").count(), 1)
        self.assertEqual(DebtMandate.objects.count(), 1)

    def test_pos_json_shows_rejected_agreement_without_losing_recorded_sale(self):
        from django.urls import reverse
        self.authenticate_client()
        data = self._credit_sale_payload()
        data["final_due_date"] = "2026-01-01"
        response = self.client.post(reverse("hubtel_debt_create"), json.dumps(data),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["debit_authorized"])
        self.assertTrue(response.json()["sale_recorded"])
        self.assertEqual(Document.objects.filter(reference="AUTO-SCHEDULE-TEST").count(), 1)
        self.assertFalse(DebtMandate.objects.exists())

    def test_pos_mandate_requires_customer_explicit_prompt_request(self):
        from django.urls import reverse
        self.authenticate_client()
        data = self._credit_sale_payload()
        data["customer_reviewed_schedule"] = "no"
        response = self.client.post(reverse("hubtel_debt_create"), json.dumps(data),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(DebtMandate.objects.exists())

    def test_staff_request_requires_explicit_customer_consent_and_provider_activation(self):
        from django.urls import reverse
        plan = mandates.create_plan(
            self.user, self.branch, party_id=self.customer.pk,
            phone="0241234567", channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=timezone.localdate() + timedelta(days=7), installment_count="4",
        )
        self.authenticate_client()
        url = reverse("hubtel_debt_staff_prompt", args=[plan.pk])
        with patch("hubtel_direct.debit_api.initiate_preapproval") as send:
            missing = self.client.post(url, {})
            self.assertEqual(missing.status_code, 302)
            send.assert_not_called()
            disabled = self.client.post(url, {"customer_requested_prompt": "yes"})
            self.assertEqual(disabled.status_code, 302)
            send.assert_not_called()
        plan.refresh_from_db()
        self.assertEqual(plan.status, "draft")

    @override_settings(
        HUBTEL_API_ID="offline-test-id", HUBTEL_API_KEY="offline-test-key",
        HUBTEL_COLLECTION_ACCOUNT="12345", HUBTEL_DIRECT_EGRESS_CONFIRMED=True,
        HUBTEL_DIRECT_RECEIVE_ENABLED=True, HUBTEL_DEBT_PREAPPROVAL_ENABLED=True,
        MARKET_SITE_ORIGIN="https://market.kofadimpex.com",
    )
    def test_staff_hubtel_prompt_is_one_time_and_not_a_debit(self):
        from django.urls import reverse
        plan = mandates.create_plan(
            self.user, self.branch, party_id=self.customer.pk,
            phone="0241234567", channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=timezone.localdate() + timedelta(days=7), installment_count="4",
        )
        self.authenticate_client()
        url = reverse("hubtel_debt_staff_prompt", args=[plan.pk])
        with patch("hubtel_direct.debit_api.initiate_preapproval", return_value={
            "provider_id": "hubtel-authorization-123", "verification_type": "USSD",
            "otp_prefix": "", "status": "PENDING",
        }) as provider, patch("hubtel_direct.debit_api.debit_charge") as debit:
            r = self.client.post(url, {"customer_requested_prompt": "yes"})
            self.assertEqual(r.status_code, 302)
            self.assertEqual(provider.call_count, 1)
            repeat = self.client.post(url, {"customer_requested_prompt": "yes"})
            self.assertEqual(repeat.status_code, 302)
            self.assertEqual(provider.call_count, 1)
            debit.assert_not_called()
        plan.refresh_from_db()
        self.assertEqual(plan.status, "pending")
        self.assertEqual(plan.verification_type, "USSD")
        # Prompt submission is not wallet approval. Only Hubtel's independent
        # authenticated status check can change the mandate to approved.
        self.assertIsNone(plan.provider_approved_at)

    def test_staff_waiting_page_and_draft_edit_show_exact_dates(self):
        plan = mandates.create_plan(
            self.user, self.branch, party_id=self.customer.pk,
            phone="0241234567", channel="mtn-gh-direct-debit", frequency="weekly",
            first_due_date=timezone.localdate() + timedelta(days=7),
            installment_count="4",
        )
        self.authenticate_client()
        from django.urls import reverse
        response = self.client.get(reverse("hubtel_debt_staff_status", args=[plan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Waiting for the customer")
        self.assertContains(response, "GHS 200.00")
        self.assertContains(response, "Hubtel preapproval has not been activated")
        self.assertContains(response, "Open customer approval page")
        self.assertContains(response, "4 scheduled payments")

        edit = mandates.update_draft_plan(
            self.user, self.branch, plan.pk, frequency="monthly",
            first_due_date=timezone.localdate() + timedelta(days=14),
            installment_count="2",
        )
        self.assertEqual(edit.terms_snapshot["installment_count"], 2)
        self.assertEqual(edit.amount_per_period, Decimal("400.00"))
        DebtMandate.objects.filter(pk=plan.pk).update(status="pending")
        with self.assertRaisesRegex(ValidationError, "Cancel it"):
            mandates.update_draft_plan(
                self.user, self.branch, plan.pk, installment_count="6",
            )
