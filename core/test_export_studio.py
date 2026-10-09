"""Acceptance checks for scoped, complete and professional KOFAD downloads."""
import csv
import io
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.utils import timezone
from openpyxl import load_workbook

from core.models import Message, Payment
from core.tests import Fixtures
from marketplace.models import CustomerAccount, EmailIdentity, GoogleIdentity, MarketPaymentAttempt, OnlineOrder


class ExportStudioTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()

    def market_customer(self, name, phone):
        customer = CustomerAccount(full_name=name, phone=phone, email=f"{name.lower().replace(' ', '')}@example.test")
        customer.set_password("Customer-Password-Test-2026!")
        customer.save()
        return customer

    def market_order(self, customer, branch, reference, amount="45.00"):
        return OnlineOrder.objects.create(
            public_reference=reference, customer=customer, branch=branch,
            recipient_name=customer.full_name, phone=customer.phone or "",
            email=customer.email, fulfilment="pickup",
            payment_status="paid", status="paid",
            subtotal=amount, total=amount,
        )

    def csv(self, dataset):
        response = self.client.get("/exports/download/csv/", {"dataset": dataset})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_export_studio_has_guided_categories_and_four_downloads(self):
        response = self.client.get("/exports/")
        self.assertEqual(response.status_code, 200)
        for label in ("Registered Market customer accounts", "Detailed sale items",
                      "Online gateway verification history", "SMS & WhatsApp delivery",
                      "Low stock & reorder", "Excel workbook", "Professional PDF",
                      "Editable document", "Raw data export"):
            self.assertContains(response, label)
        self.assertContains(response, "export-studio.css")

    def test_registered_customers_include_unsold_accounts_and_verified_sign_in(self):
        with_account = self.market_customer("First Customer", "+233241234567")
        unsold = self.market_customer("Second Customer", "+233241234568")
        self.market_order(with_account, self.branch, "EX-REG-001")
        EmailIdentity.objects.create(
            kind="customer", owner_id=with_account.pk, email=with_account.email,
            verified_at=timezone.now(), notifications_enabled=True, marketing_emails_enabled=True
        )
        GoogleIdentity.objects.create(kind="customer", owner_id=with_account.pk,
                                      subject="unique-google-sub-001", email=with_account.email)
        rows = self.csv("market_customers")
        self.assertEqual(len(rows), 2)
        by_name = {row["Registered name"]: row for row in rows}
        active = by_name["First Customer"]
        self.assertEqual(active["Phone verification"], "Unverified")
        self.assertEqual(active["Email verification"], "Verified")
        self.assertEqual(active["Google sign-in"], "Linked")
        self.assertEqual(active["Confirmed paid order value"], "45.00")
        self.assertEqual(active["Paid orders"], "1")
        self.assertEqual(active["Marketing consent"], "Opted in")
        self.assertEqual(by_name["Second Customer"]["Orders"], "0")
        self.assertIn("Account ID", rows[0])

    def test_branch_manager_only_sees_account_having_orders_at_own_store(self):
        visible = self.market_customer("Local Client", "+233241234561")
        hidden = self.market_customer("Other Branch Client", "+233241234562")
        self.market_order(visible, self.branch, "EX-REG-LOCAL")
        self.market_order(hidden, self.other, "EX-REG-REMOTE")
        member = User.objects.create_user("branch-manager", password="Demo-password-2026!")
        member.user_permissions.add(Permission.objects.get(codename="manage_company"))
        member.access.branches.add(self.branch)
        self.authenticate_client(member)
        data = self.csv("market_customers")
        self.assertEqual([row["Registered name"] for row in data], ["Local Client"])
        self.assertNotIn(b"Other Branch Client", self.client.get(
            "/exports/download/pdf/", {"dataset": "market_customers"}
        ).content)

    def test_non_privileged_cashier_cannot_export_registered_accounts(self):
        member = User.objects.create_user("cashier-only", password="Demo-password-2026!")
        member.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        member.access.branches.add(self.branch)
        self.authenticate_client(member)
        self.assertNotContains(self.client.get("/exports/"), "Registered Market customer accounts")
        self.assertEqual(self.client.get("/exports/download/csv/",
                                         {"dataset": "market_customers"}).status_code, 403)
        self.assertEqual(self.client.get("/exports/download/csv/",
                                         {"dataset": "email_delivery"}).status_code, 403)

    def test_detailed_sales_math_and_manual_momo_are_labeled_accurately(self):
        sale = self.sale()
        Payment.objects.filter(document=sale).update(method="momo")
        item = self.csv("sales_lines")[0]
        self.assertEqual(item["Net sales (GHS)"], "50.00")
        self.assertEqual(item["Cost of goods (GHS)"], "20.00")
        self.assertEqual(item["Gross profit (GHS)"], "30.00")
        payment = self.csv("payment_ledger")[0]
        self.assertEqual(payment["Payment channel"], "MoMo")
        self.assertIn("not asserted", payment["Verification source"])
        self.assertEqual(payment["Cash-flow direction"], "Incoming")

    def test_gateway_attempt_report_tracks_unknown_separately_from_paid_orders(self):
        customer = self.market_customer("Gateway Client", "+233241234563")
        order = self.market_order(customer, self.branch, "EX-REG-GATE")
        order.payment_status = "pending"
        order.save(update_fields=["payment_status"])
        MarketPaymentAttempt.objects.create(
            order=order, reference="EX-GATE-ATTEMPT", amount=Decimal("45.00"),
            provider="paystack", status="submission_unknown", currency="GHS"
        )
        rows = self.csv("gateway_attempts")
        self.assertEqual(rows[0]["Verification state"], "submission_unknown")
        self.assertEqual(rows[0]["Order payment"], "Pending")
        self.assertEqual(rows[0]["Expected amount"], "45.00")

    def test_message_delivery_export_does_not_leak_handover_codes(self):
        Message.objects.create(
            branch=self.branch, created_by=self.user, recipient="+233241234567",
            recipient_name="Customer", body="Your handover code is 123456",
            channel="sms", status="queued", source_key="sale:test"
        )
        response = self.client.get("/exports/download/csv/", {"dataset": "message_delivery"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"123456", response.content)
        self.assertIn(b"queued", response.content)

    def test_existing_customer_export_includes_business_details(self):
        self.customer.email = "customer@example.test"
        self.customer.address = "Test neighbourhood"
        self.customer.save(update_fields=["email", "address"])
        row = self.csv("customers")[0]
        self.assertEqual(row["Email"], "customer@example.test")
        self.assertEqual(row["Address"], "Test neighbourhood")
        self.assertIn("Debt email consent", row)

    def test_excel_sheet_has_filters_and_customer_columns(self):
        self.market_customer("Spreadsheet Customer", "+233241234564")
        response = self.client.get("/exports/download/xlsx/", {"dataset": "market_customers"})
        self.assertEqual(response.status_code, 200)
        book = load_workbook(io.BytesIO(response.content))
        sheet = book.active
        self.assertTrue(sheet.freeze_panes)
        self.assertTrue(sheet.auto_filter.ref)
        self.assertTrue(any("Registered name" in str(cell.value) for row in sheet for cell in row))

    def test_invalid_download_format_rejected_and_not_rendered(self):
        response = self.client.get("/exports/download/exe/", {"dataset": "customers"})
        self.assertEqual(response.status_code, 400)
