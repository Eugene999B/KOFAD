import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from . import automations
from . import debts as debt_service
from . import services as s
from .models import (
    Closing, CommunicationSettings, Company, DebtSettings, Document, ManagementContact,
    Message, Party, Product, Stock,
)
from .sms.providers import Submission
from .sms.service import _delivery_callback_token, create_internal_draft, send_automatic
from .tests import Fixtures


class ReceiptDebtCommunicationSettingsTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def historical_debt(self, *, days_ago=5, total="100.00"):
        return Document.objects.create(
            id=uuid.uuid4(),
            reference=f"HIST-DEBT-{uuid.uuid4().hex[:8].upper()}",
            branch=self.branch,
            kind="sale",
            party=self.customer,
            total=Decimal(total),
            paid=Decimal("0.00"),
            due_date=timezone.localdate() - timedelta(days=days_ago),
            created_by=self.user,
        )

    def test_receipt_uses_business_numbers_location_and_explicit_print_modes(self):
        company = Company.objects.get()
        company.phone = "+233302111111"
        company.secondary_phone = "+233242222222"
        company.address = "Kumasi business address"
        company.save(update_fields=["phone", "secondary_phone", "address"])
        self.branch.address = "Adum, Kumasi"
        self.branch.save(update_fields=["address"])

        sale = self.sale()
        self.authenticate_client()
        response = self.client.get(f"/documents/{sale.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BUSINESS CONTACT")
        self.assertContains(response, "+233302111111")
        self.assertContains(response, "+233242222222")
        self.assertContains(response, "Adum, Kumasi")
        self.assertContains(response, "A4 PDF / print")
        self.assertContains(response, "Thermal 80mm PDF")
        self.assertContains(response, "Thermal 58mm PDF")
        self.assertNotContains(response, "Best print quality:")

        for format_name in ("a4", "thermal80", "thermal58"):
            with self.subTest(format=format_name):
                pdf = self.client.get(f"/documents/{sale.pk}/pdf/{format_name}/")
                self.assertEqual(pdf.status_code, 200)
                self.assertEqual(pdf["Content-Type"], "application/pdf")
                self.assertTrue(pdf.content.startswith(b"%PDF"))
                self.assertGreater(len(pdf.content), 1000)

    def test_company_and_location_settings_normalize_public_identity(self):
        self.authenticate_client()
        response = self.client.post("/settings/company/", {
            "name": "KOFAD IMPEX ENTERPRISE",
            "phone": "0241234567",
            "secondary_phone": "0207654321",
            "address": "Kumasi, Ghana",
        })
        self.assertEqual(response.status_code, 302)
        company = Company.objects.get()
        self.assertEqual(company.phone, "+233241234567")
        self.assertEqual(company.secondary_phone, "+233207654321")

        response = self.client.post("/settings/location/", {
            "name": "Adum Main Shop",
            "address": "Adum, Kumasi",
        })
        self.assertEqual(response.status_code, 302)
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.name, "Adum Main Shop")
        self.assertEqual(self.branch.address, "Adum, Kumasi")

    def test_single_unit_product_removes_pack_semantics_server_side(self):
        self.authenticate_client()
        response = self.client.post("/products/new/", {
            "name": "Single bottle item",
            "sku": "SINGLE-001",
            "barcode": "",
            "category": "Drinks",
            "base_unit": "bottle",
            "pack_enabled": "no",
            "opening_packs": "6",
            "opening_units": "7",
            "cost": "10.00",
            "retail_unit": "15.00",
            "retail_pack": "300.00",
            "wholesale_unit": "13.00",
            "wholesale_pack": "250.00",
            "reorder_level": "3",
            "active": "on",
        })
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(sku="SINGLE-001")
        self.assertEqual(product.pack_size, 1)
        self.assertEqual(product.pack_name, "bottle")
        self.assertIsNone(product.retail_pack)
        self.assertIsNone(product.wholesale_pack)
        self.assertEqual(Stock.objects.get(branch=self.branch, product=product).quantity, 7)

        page = self.client.get(f"/products/{product.pk}/")
        self.assertContains(page, "Single-unit product")
        self.assertContains(page, "Retail price")
        self.assertContains(page, "Wholesale price")

    def test_debt_grace_period_controls_when_account_becomes_overdue(self):
        self.historical_debt(days_ago=3)
        settings, _ = DebtSettings.objects.update_or_create(
            pk=1,
            defaults={
                "delivery_mode": "off",
                "overdue_grace_value": 1,
                "overdue_grace_unit": "weeks",
            },
        )
        snapshot = debt_service.customer_account_snapshot(self.customer)
        self.assertEqual(snapshot["outstanding"], Decimal("100.00"))
        self.assertEqual(snapshot["overdue"], Decimal("0.00"))
        self.assertEqual(snapshot["maximum_days_overdue"], 0)
        self.assertEqual(snapshot["overdue_grace_days"], 7)

        settings.overdue_grace_value = 0
        settings.overdue_grace_unit = "days"
        settings.save(update_fields=["overdue_grace_value", "overdue_grace_unit"])
        snapshot = debt_service.customer_account_snapshot(self.customer)
        self.assertEqual(snapshot["overdue"], Decimal("100.00"))
        self.assertEqual(snapshot["maximum_days_overdue"], 3)

    def test_debt_settings_save_overdue_units_and_editable_default_message(self):
        self.authenticate_client()
        body = (
            "{company}: Dear {customer}, balance {currency} {balance} across {debt_count}. "
            "{due_sentence} Call {business_phone}. {location}"
        )
        response = self.client.post("/settings/debt/", {
            "delivery_mode": "draft",
            "reminder_time": "09:00",
            "due_soon_enabled": "on",
            "due_soon_days": "1, 7, 3, 3",
            "due_today_enabled": "on",
            "overdue_enabled": "on",
            "overdue_grace_value": "2",
            "overdue_grace_unit": "weeks",
            "overdue_repeat_days": "4",
            "max_sms_7_days": "3",
            "max_sms_30_days": "8",
            "minimum_hours_between_sms": "24",
            "minimum_balance": "10.00",
            "message_template": body,
        })
        self.assertEqual(response.status_code, 302)
        item = DebtSettings.objects.get()
        self.assertEqual(item.due_soon_days, "7,3,1")
        self.assertEqual(item.overdue_grace_days, 14)
        self.assertEqual(item.overdue_repeat_days, 4)
        self.assertEqual(item.message_template, body)

    def test_debt_automation_is_consolidated_consent_aware_and_idempotent(self):
        self.customer.phone = "+233241234567"
        self.customer.consent = True
        self.customer.save(update_fields=["phone", "consent"])
        self.historical_debt(days_ago=10, total="150.00")
        DebtSettings.objects.update_or_create(
            pk=1,
            defaults={
                "delivery_mode": "draft",
                "reminder_time": "09:00",
                "due_soon_enabled": True,
                "due_soon_days": "7,3,1",
                "due_today_enabled": True,
                "overdue_enabled": True,
                "overdue_grace_value": 0,
                "overdue_grace_unit": "days",
                "overdue_repeat_days": 3,
                "max_sms_7_days": 3,
                "max_sms_30_days": 8,
                "minimum_hours_between_sms": 24,
                "minimum_balance": Decimal("1.00"),
            },
        )
        now = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        self.assertEqual(automations.run_debt_reminders(now), 1)
        message = Message.objects.get(source_key__startswith=f"auto:debt:{self.customer.pk}:")
        self.assertEqual(message.party_id, self.customer.pk)
        self.assertIsNone(message.management_contact_id)
        self.assertEqual(message.status, "draft")
        self.assertIn("150.00", message.body)

        self.assertEqual(automations.run_debt_reminders(now), 0)
        self.assertEqual(Message.objects.filter(party=self.customer, source_key__startswith="auto:debt:").count(), 1)

        other = self.customer
        other.consent = False
        other.save(update_fields=["consent"])
        tomorrow = now + timedelta(days=4)
        self.assertEqual(automations.run_debt_reminders(tomorrow), 0)

    def test_management_closing_draft_uses_configured_internal_recipient(self):
        company = Company.objects.get()
        company.phone = "+233302111111"
        company.save(update_fields=["phone"])
        contact = ManagementContact.objects.create(
            name="Managing Director",
            phone="+233244444444",
            receive_closing=True,
        )
        CommunicationSettings.objects.update_or_create(
            pk=1,
            defaults={"daily_closing_mode": "draft"},
        )
        closing = Closing.objects.create(
            branch=self.branch,
            date=timezone.localdate() - timedelta(days=1),
            expected={"cash": "100.00", "momo": "0.00", "bank": "0.00", "card": "0.00"},
            counted={"cash": "98.00", "momo": "0.00", "bank": "0.00", "card": "0.00"},
            summary={
                "sales_total": "500.00",
                "debt_collections": "50.00",
                "expenses_total": "20.00",
            },
            submitted_by=self.user,
        )
        created = automations.prepare_closing_notifications(closing, self.user)
        self.assertEqual(len(created), 1)
        message = created[0]
        self.assertEqual(message.management_contact_id, contact.pk)
        self.assertIsNone(message.party_id)
        self.assertEqual(message.status, "draft")
        self.assertIn("500.00", message.body)
        self.assertIn("variance GHS -2.00", message.body)

    @override_settings(
        SMS_ENABLED=True,
        SMS_PROVIDER="arkesel",
        SMS_SANDBOX=False,
        SMS_PUBLIC_ORIGIN="https://kofad.example.test",
        ARKESEL_API_KEY="ci-test-key",
        SMS_SENDER_ID="KOFAD",
    )
    @patch("core.sms.providers.Arkesel.submit_many")
    def test_daily_closing_sends_immediately_to_all_configured_management_numbers(self, submit_many):
        submit_many.side_effect = lambda recipients, body, sender, callback_url, sandbox: [
            Submission("accepted", provider_id=f"close-{index}", http_status=200, recipient=recipient)
            for index, recipient in enumerate(recipients, 1)
        ]
        ManagementContact.objects.create(
            name="Owner One", phone="+233244444444", receive_closing=True
        )
        ManagementContact.objects.create(
            name="Owner Two", phone="+233255555555", receive_closing=True
        )
        CommunicationSettings.objects.update_or_create(
            pk=1, defaults={"daily_closing_mode": "send"}
        )
        closing = Closing.objects.create(
            branch=self.branch,
            date=timezone.localdate(),
            expected={"cash": "100.00", "momo": "0.00", "bank": "0.00", "card": "0.00"},
            counted={"cash": "100.00", "momo": "0.00", "bank": "0.00", "card": "0.00"},
            summary={"sales_total": "900.00", "debt_collections": "0.00", "expenses_total": "20.00"},
            submitted_by=self.user,
        )
        sent = automations.prepare_closing_notifications(closing, self.user)
        self.assertEqual(len(sent), 2)
        self.assertTrue(all(message.status == "accepted" for message in sent))
        submit_many.assert_called_once()
        self.assertEqual(set(submit_many.call_args.args[0]), {
            "+233244444444", "+233255555555"
        })

    @override_settings(SMS_ENABLED=False)
    def test_automatic_send_mode_stays_draft_when_live_sms_is_unavailable(self):
        contact = ManagementContact.objects.create(
            name="Owner alerts",
            phone="+233245555555",
            receive_closing=True,
        )
        message = create_internal_draft(
            self.user, self.branch, contact, "Internal closing draft", "auto:test:provider-off"
        )
        send_automatic(message, self.user)
        message.refresh_from_db()
        self.assertEqual(message.status, "draft")
        self.assertIsNone(message.submitted_by)

    def test_source_key_cannot_be_rebound_to_another_management_recipient(self):
        first = ManagementContact.objects.create(name="First", phone="+233246666666")
        second = ManagementContact.objects.create(name="Second", phone="+233247777777")
        create_internal_draft(self.user, self.branch, first, "Same source", "auto:test:immutable-source")
        with self.assertRaisesRegex(ValidationError, "already belongs"):
            create_internal_draft(self.user, self.branch, second, "Same source", "auto:test:immutable-source")

    def test_settings_and_communications_workspaces_render(self):
        self.authenticate_client()
        for path, phrase in (
            ("/settings/debt/", "Debt settings"),
            ("/settings/communications/", "Who gets what message"),
            ("/settings/location/", "Location settings"),
            ("/communications/", "AUTOMATIC MESSAGES"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, phrase)

    def test_debt_payment_uses_dialog_not_inline_scroll_form(self):
        due = (timezone.localdate() + timedelta(days=7)).isoformat()
        self.sale(payments=[], party=self.customer.pk, due_date=due)
        self.authenticate_client()
        response = self.client.get("/debts/", {"customer": self.customer.pk})
        self.assertContains(response, 'class="debt-payment-dialog"')
        self.assertContains(response, "Save payment")


    def test_sale_checkout_keeps_payment_inline_and_can_capture_sms_consent(self):
        self.authenticate_client()
        page = self.client.get("/sales/new/")
        self.assertNotContains(page, 'id="sale-payment-dialog"')
        self.assertContains(page, "Customer & payment")
        self.assertContains(page, "Payment method")
        self.assertContains(page, "Complete Sale & Generate Receipt")
        self.assertContains(page, 'id="customer-consent"')

        self.customer.phone = "+233241234567"
        self.customer.save(update_fields=["phone"])
        self.assertFalse(self.customer.consent)
        self.sale(customer_consent=True)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.consent)

    @override_settings(SMS_ENABLED=True)
    def test_sales_history_receipt_detail_stays_focused_on_transaction(self):
        self.customer.phone = "+233241234567"
        self.customer.consent = True
        self.customer.save(update_fields=["phone", "consent"])
        sale = self.sale()
        self.authenticate_client()
        response = self.client.get(f"/documents/{sale.pk}/")
        self.assertContains(response, sale.reference)
        self.assertNotContains(response, "CUSTOMER MESSAGE")
        self.assertNotContains(response, "Receipt message ready")
        self.assertNotContains(response, "Send SMS now")
        self.assertNotContains(response, "Best print quality:")

    def test_new_sale_sms_checkbox_has_no_repeated_instruction_copy(self):
        self.authenticate_client()
        response = self.client.get("/sales/new/")
        self.assertContains(response, "Send receipt by SMS")
        self.assertNotContains(response, "Tick only after this customer agrees to receive the receipt message.")

    def test_communications_page_omits_redundant_status_cards(self):
        self.authenticate_client()
        response = self.client.get("/communications/")
        self.assertNotContains(response, "Staff drafts")
        self.assertNotContains(response, "Customer events")
        self.assertNotContains(response, "Management alerts")
        self.assertNotContains(response, "Drafts remain safe in KOFAD until credentials are enabled.")
        self.assertContains(response, "Send a message")
        self.assertContains(response, "Recent messages")


    def test_communications_hub_exposes_all_recipient_modes_and_whatsapp(self):
        self.customer.phone = "0241234567"
        self.customer.save(update_fields=["phone"])
        self.authenticate_client()
        response = self.client.get("/communications/")
        for phrase in ("One customer", "Select customers", "All customers", "Any number", "WhatsApp"):
            self.assertContains(response, phrase)

    @override_settings(
        SMS_ENABLED=True,
        SMS_PROVIDER="arkesel",
        SMS_SANDBOX=False,
        SMS_PUBLIC_ORIGIN="https://kofad.example.test",
        ARKESEL_API_KEY="ci-test-key",
        SMS_SENDER_ID="KOFAD",
    )
    @patch("core.sms.providers.Arkesel.submit_many")
    def test_manual_sms_is_normalized_and_sent_immediately(self, submit_many):
        submit_many.side_effect = lambda recipients, body, sender, callback_url, sandbox: [
            Submission("accepted", provider_id=f"msg-{index}", http_status=200, recipient=recipient)
            for index, recipient in enumerate(recipients, 1)
        ]
        self.authenticate_client()
        response = self.client.post("/communications/", {
            "action": "send_compose",
            "key": str(uuid.uuid4()),
            "channel": "sms",
            "target": "manual",
            "phone": "0241234567",
            "body": "KOFAD test message",
        })
        self.assertEqual(response.status_code, 302)
        message = Message.objects.get(channel="sms", body="KOFAD test message")
        self.assertEqual(message.recipient, "+233241234567")
        self.assertTrue(message.manual_override)
        self.assertEqual(message.status, "accepted")
        self.assertEqual(message.provider, "arkesel")
        self.assertIsNotNone(message.submitted_by)
        attempt = message.delivery_attempts.get()
        self.assertEqual(attempt.provider_id, "msg-1")
        self.assertEqual(attempt.status, "accepted")
        submit_many.assert_called_once()

    @override_settings(
        SMS_ENABLED=True,
        SMS_PROVIDER="arkesel",
        SMS_SANDBOX=False,
        SMS_PUBLIC_ORIGIN="https://kofad.example.test",
        ARKESEL_API_KEY="ci-test-key",
        SMS_SENDER_ID="KOFAD",
    )
    @patch("core.sms.providers.Arkesel.submit_many")
    def test_selected_customers_are_sent_in_one_provider_batch(self, submit_many):
        submit_many.side_effect = lambda recipients, body, sender, callback_url, sandbox: [
            Submission("accepted", provider_id=f"batch-{index}", http_status=200, recipient=recipient)
            for index, recipient in enumerate(recipients, 1)
        ]
        self.customer.phone = "0241234567"
        self.customer.save(update_fields=["phone"])
        other = Party.objects.create(
            branch=self.branch, kind="customer", name="Second Customer", phone="0207654321"
        )
        self.authenticate_client()
        response = self.client.post("/communications/", {
            "action": "send_compose",
            "key": str(uuid.uuid4()),
            "channel": "sms",
            "target": "selected",
            "customer_ids": [str(self.customer.pk), str(other.pk)],
            "body": "Stock has arrived",
        })
        self.assertEqual(response.status_code, 302)
        sent = Message.objects.filter(channel="sms", body="Stock has arrived", status="accepted")
        self.assertEqual(sent.count(), 2)
        self.assertEqual(set(sent.values_list("recipient", flat=True)), {
            "+233241234567", "+233207654321"
        })
        submit_many.assert_called_once()
        recipients = set(submit_many.call_args.args[0])
        self.assertEqual(recipients, {"+233241234567", "+233207654321"})


    @override_settings(
        SMS_ENABLED=True,
        SMS_PROVIDER="arkesel",
        SMS_SANDBOX=False,
        SMS_PUBLIC_ORIGIN="https://kofad.example.test",
        ARKESEL_API_KEY="ci-test-key",
        SMS_SENDER_ID="KOFAD",
    )
    @patch("core.sms.providers.Arkesel.submit_many")
    def test_delivery_callback_changes_sent_to_delivered(self, submit_many):
        submit_many.return_value = [
            Submission("accepted", provider_id="ark-123", http_status=200, recipient="+233241234567")
        ]
        self.authenticate_client()
        self.client.post("/communications/", {
            "action": "send_compose",
            "key": str(uuid.uuid4()),
            "channel": "sms",
            "target": "manual",
            "phone": "0241234567",
            "body": "Delivery tracking test",
        })
        message = Message.objects.get(body="Delivery tracking test")
        self.assertEqual(message.status, "accepted")
        response = self.client.get("/sms/delivery/", {
            "token": _delivery_callback_token(),
            "sms_id": "ark-123",
            "status": "DELIVERED",
        })
        self.assertEqual(response.status_code, 200)
        message.refresh_from_db()
        self.assertEqual(message.status, "delivered")

    @override_settings(
        SMS_ENABLED=True,
        SMS_PROVIDER="arkesel",
        SMS_SANDBOX=False,
        SMS_PUBLIC_ORIGIN="https://kofad.example.test",
        ARKESEL_API_KEY="ci-test-key",
        SMS_SENDER_ID="KOFAD",
    )
    @patch("core.sms.providers.Arkesel.submit_many")
    def test_provider_failure_reason_is_saved_for_staff(self, submit_many):
        submit_many.return_value = [
            Submission(
                "failed",
                http_status=422,
                error_code="provider_rejected",
                error_detail="Sender ID is not approved.",
                recipient="+233241234567",
            )
        ]
        self.authenticate_client()
        self.client.post("/communications/", {
            "action": "send_compose",
            "key": str(uuid.uuid4()),
            "channel": "sms",
            "target": "manual",
            "phone": "0241234567",
            "body": "Failure detail test",
        })
        message = Message.objects.get(body="Failure detail test")
        self.assertEqual(message.status, "failed")
        self.assertEqual(message.last_error, "Sender ID is not approved.")
        page = self.client.get("/communications/")
        self.assertContains(page, "Sender ID is not approved.")

    def test_manual_whatsapp_prepares_direct_chat_link_without_claiming_delivery(self):
        self.authenticate_client()
        response = self.client.post("/communications/", {
            "action": "send_compose",
            "key": str(uuid.uuid4()),
            "channel": "whatsapp",
            "target": "manual",
            "phone": "0241234567",
            "body": "Hello from KOFAD",
        })
        self.assertEqual(response.status_code, 302)
        item = Message.objects.get(channel="whatsapp", body="Hello from KOFAD")
        self.assertEqual(item.status, "ready")
        self.assertEqual(item.provider, "whatsapp-link")
        page = self.client.get(f"/communications/?wa={item.pk}")
        self.assertContains(page, "Open WhatsApp")
        self.assertContains(page, "https://wa.me/233241234567")
