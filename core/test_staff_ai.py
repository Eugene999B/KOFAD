"""Authorization and privacy regression tests for web/Telegram staff intelligence."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import RequestFactory, TestCase

from core.models import Access, Branch, Product, Stock
from core.staff_ai import staff_reply
from core.staff_ai_views import assistant_chat


class StaffAssistantTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.hq = Branch.objects.create(name="Head Office", code="hq-ai")
        self.other = Branch.objects.create(name="Hidden Depot", code="hidden-ai")
        self.manager = User.objects.create_superuser("ai-manager", "manager@example.test", "test-only-pass")
        self.manager_access = Access.objects.create(user=self.manager)
        self.manager_access.branches.add(self.hq)
        self.seller = User.objects.create_user("ai-seller", password="test-only-pass")
        self.seller.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        self.seller_access = Access.objects.create(user=self.seller)
        self.seller_access.branches.add(self.hq)
        self.inventory = User.objects.create_user("ai-inventory", password="test-only-pass")
        self.inventory.user_permissions.add(Permission.objects.get(codename="operate_inventory"))
        self.inventory_access = Access.objects.create(user=self.inventory)
        self.inventory_access.branches.add(self.hq)

    def test_anonymous_and_unassigned_do_not_read_staff_data(self):
        anonymous = User()
        anonymous.is_active = False
        result, source = staff_reply(anonymous, "sales today")
        self.assertEqual(source, "denied")
        self.assertNotIn("GHS", result)
        outsider = User.objects.create_user("ai-outsider", password="test-only-pass")
        Access.objects.create(user=outsider)
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

    def test_credential_change_blocks_all_reporting(self):
        self.inventory_access.force_password_change = True
        self.inventory_access.save(update_fields=["force_password_change"])
        text, source = staff_reply(self.inventory, "low stock")
        self.assertEqual(source, "denied")
        self.assertNotIn("Grounded inventory", text)

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
