"""Authorization and privacy regression tests for web/Telegram staff intelligence."""
import json
from decimal import Decimal
from datetime import timedelta

from django.utils import timezone
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import RequestFactory, TestCase

from core.models import Access, Allocation, Branch, Document, Party, Product, Stock
from core.staff_ai import staff_reply, staff_actions
from core.staff_ai_views import assistant_chat


class StaffAssistantTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.hq = Branch.objects.create(name="Head Office", code="hq-ai")
        self.other = Branch.objects.create(name="Hidden Depot", code="hidden-ai")
        self.manager = User.objects.create_superuser("ai-manager", "manager@example.test", "test-only-pass")
        self.manager_access = Access.objects.get(user=self.manager)
        self.manager_access.branches.add(self.hq)
        self.seller = User.objects.create_user("ai-seller", password="test-only-pass")
        self.seller.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        self.seller_access = Access.objects.get(user=self.seller)
        self.seller_access.branches.add(self.hq)
        self.inventory = User.objects.create_user("ai-inventory", password="test-only-pass")
        self.inventory.user_permissions.add(Permission.objects.get(codename="operate_inventory"))
        self.inventory_access = Access.objects.get(user=self.inventory)
        self.inventory_access.branches.add(self.hq)

    def test_anonymous_and_unassigned_do_not_read_staff_data(self):
        anonymous = User()
        anonymous.is_active = False
        result, source = staff_reply(anonymous, "sales today")
        self.assertEqual(source, "denied")
        self.assertNotIn("GHS", result)
        outsider = User.objects.create_user("ai-outsider", password="test-only-pass")
        Access.objects.get(user=outsider)
        result, source = staff_reply(outsider, "my priorities")
        self.assertEqual(source, "denied")

    def test_sales_role_cannot_see_finance_or_inventory(self):
        for question in ("sales report today", "compare sales this month", "low stock and inventory"):
            response, source = staff_reply(self.seller, question)
            self.assertEqual(source, "denied")
            self.assertNotIn("GHS", response)
        response, source = staff_reply(self.seller, "pending online orders")
        self.assertEqual(source, "orders")
        self.assertIn("assigned branches", response)

    def test_inventory_scope_never_mentions_unassigned_branch(self):
        product = Product.objects.create(sku="STAFF-AI-01", name="Grounded inventory",
                                         retail_unit=Decimal("2.00"), reorder_level=5, active=True)
        Stock.objects.create(branch=self.hq, product=product, quantity=3)
        Stock.objects.create(branch=self.other, product=product, quantity=0)
        message, source = staff_reply(self.inventory, "Which products need restocking?")
        self.assertEqual(source, "inventory")
        self.assertIn("Head Office", message)
        self.assertNotIn("Hidden Depot", message)

    def test_receivables_analysis_uses_live_scoped_invoices_and_oldest_due(self):
        customer = Party.objects.create(
            branch=self.hq, kind="customer", name="Authorized customer", phone="0240000001",
        )
        outsider = Party.objects.create(
            branch=self.other, kind="customer", name="Private outside customer", phone="0240000002",
        )
        today = timezone.localdate()
        invoice = Document.objects.create(
            reference="AI-DEBT-ONE", branch=self.hq, kind="sale", party=customer,
            total=Decimal("10.00"), paid=Decimal("4.00"),
            due_date=today - timedelta(days=2), created_by=self.manager,
        )
        collection = Document.objects.create(
            reference="AI-DEBT-PAYMENT", branch=self.hq, kind="collection", party=customer,
            total=Decimal("2.00"), paid=Decimal("2.00"), created_by=self.manager,
        )
        Allocation.objects.create(invoice=invoice, payment_document=collection, amount=Decimal("2.00"))
        Document.objects.create(
            reference="AI-DEBT-HIDDEN", branch=self.other, kind="sale", party=outsider,
            total=Decimal("999.00"), paid=Decimal("0.00"),
            due_date=today - timedelta(days=3), created_by=self.manager,
        )
        # Superusers legitimately see all active branches; restrict this
        # regression to a finance-authorized, single-branch staff account.
        analyst = User.objects.create_user("ai-analyst", password="test-only-pass")
        analyst.user_permissions.add(Permission.objects.get(codename="view_reports"))
        Access.objects.get(user=analyst).branches.add(self.hq)
        response, source = staff_reply(
            analyst, "Which receivables and overdue invoices need attention today?",
        )
        self.assertEqual(source, "receivables")
        self.assertIn("Outstanding now: GHS 4.00", response)
        self.assertIn("Overdue: GHS 4.00", response)
        self.assertIn("Debt collections posted today: GHS 2.00", response)
        self.assertIn("AI-DEBT-ONE", response)
        self.assertNotIn("AI-DEBT-HIDDEN", response)
        self.assertNotIn("999.00", response)
        denied, source = staff_reply(self.seller, "Show me overdue invoices")
        self.assertEqual(source, "denied")
        self.assertNotIn("AI-DEBT-ONE", denied)

    def test_staff_question_about_receivables_does_not_route_to_online_orders(self):
        answer, source = staff_reply(
            self.manager,
            "What changed in customer receivables today, which invoices need attention first?",
        )
        self.assertEqual(source, "receivables")
        self.assertIn("Outstanding now", answer)
        self.assertNotIn("open online orders needing operational follow-through", answer)

    def test_credential_change_blocks_all_reporting(self):
        self.inventory_access.force_password_change = True
        self.inventory_access.save(update_fields=["force_password_change"])
        text, source = staff_reply(self.inventory, "low stock")
        self.assertEqual(source, "denied")
        self.assertNotIn("Grounded inventory", text)

    def test_follow_up_remains_permission_checked(self):
        reply, source = staff_reply(self.manager, "What about last month?", context="compare")
        self.assertEqual(source, "compare")
        self.assertIsInstance(reply, str)
        reply, source = staff_reply(self.seller, "What about last month?", context="compare")
        self.assertEqual(source, "denied")
        self.assertNotIn("GHS", reply)

    @patch("marketplace.kofad_ai.requests.post")
    def test_staff_questions_never_call_free_ai_providers(self, external):
        for question in ("compare sales this week", "my priorities", "send customers an SMS",
                         "analyse my finances", "hello", "show low stock"):
            staff_reply(self.manager, question)
        external.assert_not_called()

    def test_chat_post_only_authenticated_role_checked_and_no_store(self):
        request = self.factory.post(
            "/staff/assistant/chat/", data=json.dumps({"message": "my priorities"}),
            content_type="application/json",
        )
        request.user = self.inventory
        request.session = {}
        response = assistant_chat(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertFalse(json.loads(response.content)["external_ai_used"])

    def test_branch_and_abilities_question_is_answered_together(self):
        response, source = staff_reply(self.seller, "Which branches and abilities are assigned to me?")
        self.assertEqual(source, "role")
        self.assertIn("Head Office", response)
        self.assertIn("online-order fulfilment", response)
        self.assertNotIn("Hidden Depot", response)
        self.assertNotIn("finance trends", response)

    def test_missing_verified_closings_are_not_treated_as_zero_real_sales(self):
        response, source = staff_reply(self.manager, "Show sales today")
        self.assertEqual(source, "reports")
        self.assertIn("this does not mean KOFAD made zero sales", response)
        self.assertNotIn("Recorded sales: GHS 0.00", response)

    def test_staff_analysis_stays_grounded_and_permission_scoped(self):
        text, source = staff_reply(self.manager, "Deep analysis: why did sales change this week?")
        self.assertEqual(source, "insights")
        self.assertIn("VERIFIED SALES COMPARISON", text)
        self.assertIn("recorded signals", text)
        restricted, source = staff_reply(self.seller, "Deep analysis: why did sales change this week?")
        self.assertNotIn("VERIFIED SALES COMPARISON", restricted)

    def test_workflow_actions_respect_current_permissions(self):
        self.assertEqual(staff_actions(self.seller, "reports"), [])
        self.assertEqual(staff_actions(self.seller, "receivables"), [])
        self.assertIn({"label": "View online orders", "href": "/online-orders/"},
                      staff_actions(self.seller, "orders"))
        self.assertIn({"label": "Open inventory", "href": "/inventory/"},
                      staff_actions(self.inventory, "inventory"))
        self.inventory_access.force_password_change = True
        self.inventory_access.save(update_fields=["force_password_change"])
        self.assertEqual(staff_actions(self.inventory, "inventory"), [])

    def test_chat_exposes_only_server_approved_workflow_links(self):
        request = self.factory.post(
            "/staff/assistant/chat/", data=json.dumps({"message": "pending online orders"}),
            content_type="application/json",
        )
        request.user = self.seller
        request.session = {}
        response = assistant_chat(request)
        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["actions"], [{"label": "View online orders", "href": "/online-orders/"}])
        self.assertTrue(payload["read_only"])

    def test_chat_rejects_oversized_and_bad_input(self):
        request = self.factory.post(
            "/staff/assistant/chat/", data=json.dumps({"message": "x" * 601}),
            content_type="application/json",
        )
        request.user = self.manager
        request.session = {}
        self.assertEqual(assistant_chat(request).status_code, 400)

    def test_message_request_is_advice_not_external_send(self):
        text, source = staff_reply(self.seller, "Send an unsolicited Gmail and SMS blast")
        self.assertEqual(source, "draft")
        self.assertIn("cannot send unsolicited", text)
