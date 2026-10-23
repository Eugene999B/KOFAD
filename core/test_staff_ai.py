"""Authorization and privacy regression tests for web/Telegram staff intelligence."""
import json
from decimal import Decimal
from datetime import timedelta

from django.utils import timezone
from unittest.mock import Mock, patch

from django.contrib.auth.models import Permission, User
from django.test import RequestFactory, TestCase, override_settings

from core.models import Access, Allocation, Branch, Document, Line, Message, Party, Payment, Product, SmsAttempt, Stock
from core.staff_ai import staff_reply, staff_actions
from core.staff_ai_views import assistant_chat
from core.email_models import EmailLetter, EmailMailbox, EmailMailboxMember


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

    def test_private_staff_intent_is_redacted_before_free_model(self):
        from core.staff_ai_planner import _sanitized_intent

        text = _sanitized_intent(
            "Show why sales to Ama Mensah at ama@example.com "
            "0243344567 for item Rice-99 fell last week and check MoMo"
        )
        self.assertIn("sales", text)
        self.assertIn("momo", text)
        for secret in ("Ama", "Mensah", "example", "0243344567", "Rice", "99", "@"):
            self.assertNotIn(secret.casefold(), text.casefold())
        self.assertEqual(_sanitized_intent("Ama Mensah abc@example.test 0243344567"), "")

    @override_settings(
        KOFAD_STAFF_AI_PLANNER_ENABLED=True,
        KOFAD_GEMINI_ENABLED=True,
        KOFAD_GEMINI_FREE_TIER_CONFIRMED=True,
        GEMINI_API_KEY="test-only-gemini",
        KOFAD_OPENROUTER_FREE_ENABLED=False,
    )
    @patch("core.staff_ai_planner.requests.post")
    def test_gemini_sees_only_generic_intent_and_returns_fixed_tools(self, post):
        from core.staff_ai_planner import _sanitized_intent, _ai_plan

        post.return_value = Mock(
            status_code=200, json=lambda: {
                "candidates": [{
                    "content": {"parts": [{"text": '{"tools":["sales_activity","momo"]}'}]}
                }]
            },
        )
        redacted = _sanitized_intent("How many MoMo sales to Ama Mensah 0243344567 today?")
        selected = _ai_plan(redacted)
        self.assertEqual(selected, ("sales_activity", "momo"))
        serialized = json.dumps(post.call_args.kwargs.get("json"))
        self.assertNotIn("Mensah", serialized)
        self.assertNotIn("0243344567", serialized)
        self.assertNotIn("GHS", serialized)
        self.assertIn("responseMimeType", serialized)
        self.assertIn("test-only-gemini", post.call_args.kwargs["headers"]["x-goog-api-key"])

    @override_settings(KOFAD_STAFF_AI_PLANNER_ENABLED=True)
    @patch("core.staff_ai_planner._ai_plan", return_value=("sales_activity", "inventory"))
    def test_model_planned_multi_tool_answer_is_local_and_scope_checked(self, chosen):
        from core.staff_ai_planner import plan_and_answer

        visible = Product.objects.create(
            sku="AI-PLANNED-STOCK", name="Planner visible stock",
            retail_unit=Decimal("4"), active=True, reorder_level=3,
        )
        Stock.objects.create(branch=self.hq, product=visible, quantity=2)
        reply, source, external = plan_and_answer(
            self.manager, "Show sales and low stock today"
        )
        self.assertIn("Recorded sales", reply)
        self.assertIn("Planner visible stock", reply)
        self.assertEqual(source, "insights")
        self.assertTrue(external)
        chosen.assert_called_once()

    @override_settings(KOFAD_STAFF_AI_PLANNER_ENABLED=True)
    @patch("core.staff_ai_planner._ai_plan", return_value=("sales_activity",))
    def test_planner_does_not_bypass_staff_finance_permissions(self, chosen):
        from core.staff_ai_planner import plan_and_answer

        answer, source, external = plan_and_answer(
            self.seller, "Show sales today"
        )
        self.assertEqual(source, "denied")
        self.assertNotIn("GHS", answer)
        self.assertTrue(external)
        chosen.assert_called_once()

    @override_settings(KOFAD_STAFF_AI_PLANNER_ENABLED=True)
    @patch("core.staff_ai_planner._ai_plan")
    def test_planner_never_plans_mutating_or_secret_bearing_requests(self, chosen):
        from core.staff_ai_planner import plan_and_answer

        for question in (
            "Send SMS to customers now",
            "Approve all sales",
            "Delete overdue debts",
            "Reset password and API key",
        ):
            self.assertIsNone(plan_and_answer(self.manager, question))
        chosen.assert_not_called()

    @override_settings(KOFAD_STAFF_AI_PLANNER_ENABLED=True)
    @patch("core.staff_ai_planner._ai_plan", return_value=("sales_activity",))
    def test_staff_chat_marks_external_planning_but_never_data_export(self, chosen):
        request = self.factory.post(
            "/staff/assistant/chat/",
            data=json.dumps({"message": "What were sales today?"}),
            content_type="application/json",
        )
        request.user = self.manager
        request.session = {}
        response = assistant_chat(request)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertTrue(result["external_ai_used"])
        self.assertTrue(result["read_only"])
        self.assertIn("Recorded sales", result["reply"])

    def test_incomplete_what_is_a_short_natural_reply(self):
        from core.staff_ai_conversation import conversational_reply

        answer, source = conversational_reply(self.manager, "What")
        self.assertEqual(source, "clarify")
        self.assertEqual(answer, "What would you like me to check?")
        contextual, source = conversational_reply(
            self.manager, "What?", context="sales_activity"
        )
        self.assertEqual(source, "clarify")
        self.assertIn("about sales", contextual)
        self.assertNotIn("verified KOFAD report", contextual)

    def test_social_conversation_does_not_need_an_external_provider(self):
        from core.staff_ai_conversation import conversational_reply

        cases = (
            ("Hello", "Hi!"),
            ("How are you?", "here and ready"),
            ("Thanks", "You're welcome"),
            ("Who are you?", "KOFAD Assistant"),
            ("What can you do?", "KOFAD records"),
        )
        for question, expected in cases:
            answer, source = conversational_reply(self.manager, question)
            self.assertIn(expected, answer, question)
            self.assertEqual(source, "conversation")

    def test_followup_why_uses_actual_posted_sales_comparison(self):
        from core.staff_ai_conversation import conversational_reply

        answer, source = conversational_reply(
            self.manager, "Why?", context="sales_activity"
        )
        self.assertEqual(source, "sales_trend")
        self.assertIn("Recorded sales comparison", answer)
        self.assertIn("cause is unverified", answer)
        denied, source = conversational_reply(
            self.seller, "Why?", context="sales_activity"
        )
        self.assertEqual(source, "denied")
        self.assertNotIn("GHS", denied)

    def test_followup_date_rechecks_permissions_and_returns_actual_records(self):
        from core.staff_ai_conversation import conversational_reply

        answer, source = conversational_reply(
            self.manager, "What about yesterday?", context="sales_activity"
        )
        self.assertEqual(source, "sales_activity")
        self.assertIn("Recorded sales", answer)
        denied, source = conversational_reply(
            self.seller, "And yesterday?", context="sales_activity"
        )
        self.assertEqual(source, "denied")
        self.assertNotIn("GHS", denied)
        uncertain, source = conversational_reply(
            self.manager, "What about yesterday?", context="unknown"
        )
        self.assertEqual(source, "clarify")
        self.assertIn("Which report", uncertain)

    def test_tell_me_more_uses_real_sales_tool_without_making_up_cause(self):
        from core.staff_ai_conversation import conversational_reply

        text, source = conversational_reply(
            self.manager, "Tell me more", context="sales_activity"
        )
        self.assertEqual(source, "sales_trend")
        self.assertIn("finalized ledger postings", text)
        question, source = conversational_reply(self.manager, "Tell me more")
        self.assertEqual(source, "clarify")
        self.assertLess(len(question), 130)

    @override_settings(KOFAD_STAFF_AI_PLANNER_ENABLED=True)
    @patch("core.staff_ai_planner._ai_plan")
    def test_chat_short_messages_skip_external_planner(self, planned):
        messages = [
            ("What", "What would you like me to check?"),
            ("Thanks", "You're welcome"),
            ("What?", "about sales"),
        ]
        for message, expected in messages:
            request = self.factory.post(
                "/staff/assistant/chat/",
                data=json.dumps({
                    "message": message,
                    "context": "sales_activity" if message == "What?" else "",
                }),
                content_type="application/json",
            )
            request.user = self.manager
            request.session = {}
            response = assistant_chat(request)
            self.assertEqual(response.status_code, 200)
            result = json.loads(response.content)
            self.assertIn(expected, result["reply"])
            self.assertFalse(result["external_ai_used"])
        planned.assert_not_called()

    def test_unknown_request_stays_short_not_repetitive_marketing(self):
        answer, source = staff_reply(
            self.manager, "some unrelated puzzling phrase"
        )
        self.assertEqual(source, "clarify")
        self.assertNotIn("verified KOFAD report", answer)
        self.assertNotIn("I won't invent an answer", answer)
        self.assertLess(len(answer), 190)

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
        self.assertEqual(source, "sales_activity")
        self.assertIn("No finalized sale documents were found", response)
        self.assertIn("This does not prove that no goods changed hands", response)
        self.assertIn("Reconciliation is not confirmed", response)

    def test_exact_screenshot_question_reaches_live_sales_tool(self):
        response, source = staff_reply(self.manager, "What was really sold today")
        self.assertEqual(source, "sales_activity")
        self.assertIn("Recorded sales", response)
        self.assertNotIn("Hello!", response)
        self.assertNotIn("Try:", response)

    def test_posted_sales_show_real_products_returns_and_branch_scope(self):
        visible = Product.objects.create(
            sku="AI-SALE-BOX", name="Visible Rice Pack", retail_unit=Decimal("3.00"), active=True,
        )
        hidden = Product.objects.create(
            sku="AI-SALE-HIDDEN", name="Private Other Branch Stock", retail_unit=Decimal("2.00"), active=True,
        )
        for branch, product, value in (
            (self.hq, visible, Decimal("60.00")),
            (self.other, hidden, Decimal("999.00")),
        ):
            doc = Document.objects.create(
                reference="AI-POSTED-" + branch.code, branch=branch, kind="sale",
                total=value, paid=value, created_by=self.manager,
            )
            Line.objects.create(
                document=doc, product=product, description=product.name, mode="unit",
                quantity=2, factor=1, unit_price=value / 2, unit_cost=Decimal("1"),
                total=value,
            )
        ret = Document.objects.create(
            reference="AI-RETURN-HQ", branch=self.hq, kind="return",
            total=Decimal("10.00"), paid=Decimal("10.00"), created_by=self.manager,
        )
        Line.objects.create(
            document=ret, product=visible, description="Visible Rice Pack", mode="unit",
            quantity=1, factor=1, unit_price=Decimal("10.00"),
            unit_cost=Decimal("1"), total=Decimal("10.00"),
        )
        analyst = User.objects.create_user("sale-analyst", password="test-only-pass")
        analyst.user_permissions.add(Permission.objects.get(codename="view_reports"))
        Access.objects.get(user=analyst).branches.add(self.hq)
        reply, source = staff_reply(analyst, "What did we actually sell today?")
        self.assertEqual(source, "sales_activity")
        self.assertIn("Gross recorded sales: GHS 60.00", reply)
        self.assertIn("Net recorded sales after returns: GHS 50.00", reply)
        self.assertIn("Visible Rice Pack", reply)
        self.assertNotIn("999.00", reply)
        self.assertNotIn("Private Other Branch Stock", reply)
        self.assertIn({"label": "Sales history", "href": "/documents/?kind=sale"},
                      staff_actions(analyst, "sales_activity"))
        self.assertEqual(staff_actions(self.seller, "sales_activity"), [])

    def test_sales_followup_changes_date_without_reusing_unauthorized_data(self):
        reply, source = staff_reply(self.manager, "What about yesterday?", context="sales_activity")
        self.assertEqual(source, "sales_activity")
        self.assertIn("Recorded sales", reply)
        denied, source = staff_reply(self.seller, "What about yesterday?", context="sales_activity")
        self.assertEqual(source, "denied")

    def test_analytical_sales_questions_do_not_collapse_into_simple_counts(self):
        result, source = staff_reply(self.manager, "Why did sales change this week?")
        self.assertEqual(source, "insights")
        self.assertIn("VERIFIED SALES COMPARISON", result)

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

    def test_operational_date_parser_is_gra_local_and_bounded(self):
        from core.staff_ai_tools import _window

        start, end, _, _ = _window("from 2026-10-01 to 2026-10-23")
        self.assertEqual((str(start), str(end)), ("2026-10-01", "2026-10-23"))
        d, e, _, _ = _window("SMS sent 01/10/2026")
        self.assertEqual(d, e)
        self.assertEqual(str(d), "2026-10-01")
        with self.assertRaises(ValueError):
            _window("from 2026-11-01 to 2026-10-01")
        with self.assertRaises(ValueError):
            _window("from 2024-01-01 to 2026-10-23")

    def test_momo_uses_linked_customers_and_never_exposes_other_branches(self):
        p = Party.objects.create(branch=self.hq, kind="customer", name="Visible", phone="0240000191")
        hidden = Party.objects.create(branch=self.other, kind="customer", name="Hidden", phone="0240000192")
        for b, party, amount in ((self.hq, p, "32.00"), (self.other, hidden, "9999.00")):
            sale = Document.objects.create(
                reference=f"AI-MOMO-{b.code}", branch=b, kind="sale",
                party=party, total=Decimal(amount), paid=Decimal(amount), created_by=self.manager,
            )
            Payment.objects.create(document=sale, method="momo", direction=1, amount=Decimal(amount))
        analyst = User.objects.create_user("momo-analyst", password="test-only-pass")
        analyst.user_permissions.add(Permission.objects.get(codename="view_reports"))
        Access.objects.get(user=analyst).branches.add(self.hq)
        date = timezone.localdate().isoformat()
        result, source = staff_reply(analyst, f"How many people used MoMo on {date}?")
        self.assertEqual(source, "momo")
        self.assertIn("Distinct linked customers using MoMo: 1", result)
        self.assertIn("GHS 32.00", result)
        self.assertNotIn("9,999.00", result)
        denied, source = staff_reply(self.seller, f"How many people used MoMo on {date}?")
        self.assertEqual(source, "denied")
        self.assertNotIn("GHS", denied)

    @override_settings(ARKESEL_API_KEY='')
    def test_sms_counts_are_scoped_and_remaining_credits_are_not_invented(self):
        p = Party.objects.create(branch=self.hq, kind="customer", name="SMS tested", phone="0240000301")
        msg = Message.objects.create(
            branch=self.hq, party=p, created_by=self.manager, channel="sms",
            status="delivered", recipient="+233240000301", body="Test update",
            sandbox=False, source_key="debt:example",
        )
        SmsAttempt.objects.create(
            message=msg, number=1, provider="arkesel", status="delivered",
            callback_digest="0" * 64,
        )
        date = timezone.localdate().isoformat()
        reply, source = staff_reply(self.manager, f"How many SMS were sent and how many SMS credits are left on {date}?")
        self.assertEqual(source, "sms")
        self.assertIn("with recorded delivery: 1", reply)
        self.assertIn("Remaining Arkesel SMS credits: not available", reply)
        denied, source = staff_reply(self.seller, "How many SMS credits are left today?")
        self.assertEqual(source, "denied")
        self.assertNotIn("with recorded delivery: 1", denied)

    @override_settings(SMS_PROVIDER="arkesel", ARKESEL_API_KEY="ci-test-only-arkesel-key")
    @patch("requests.get")
    def test_sms_remaining_credit_checks_read_only_arkesel_api(self, get):
        from django.core.cache import cache
        from core.staff_ai_tools import _arkesel_balance

        cache.delete("kofad:staff-ai:arkesel-sms-credit:v1")
        get.return_value = Mock(status_code=200, json=lambda: {
            "status": "success", "data": {"sms_balance": "2003", "main_balance": "GHS 20.99"},
        })
        self.assertIn("2,003 credits", _arkesel_balance())
        self.assertIn("2,003 credits", _arkesel_balance())
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0],
                         "https://sms.arkesel.com/api/v2/clients/balance-details")
        self.assertNotIn("GHS 20.99", _arkesel_balance())
        cache.delete("kofad:staff-ai:arkesel-sms-credit:v1")
        get.return_value = Mock(status_code=401)
        self.assertIn("not available", _arkesel_balance())

    def test_mail_reports_require_mailbox_read_access(self):
        mailbox, _ = EmailMailbox.objects.get_or_create(address="sales@kofadimpex.com", defaults={"label": "Sales"})
        EmailMailboxMember.objects.create(mailbox=mailbox, user=self.seller, can_read=True)
        other_box, _ = EmailMailbox.objects.get_or_create(address="private@kofadimpex.com", defaults={"label": "Restricted"})
        for mb, to in ((mailbox, "buyer@example.test"), (other_box, "private@example.test")):
            EmailLetter.objects.create(
                mailbox=mb, direction="outbound", status="submitted",
                from_address=mb.address, to_address=to, subject="Order delivery",
                submitted_at=timezone.now(), delivery_status="delivered",
            )
        date = timezone.localdate().isoformat()
        response, source = staff_reply(self.seller, f"Which email addresses and types of emails were sent {date}?")
        self.assertEqual(source, "email")
        self.assertIn("buyer@example.test", response)
        self.assertIn("Order delivery", response)
        self.assertNotIn("private@example.test", response)
        noaccess, source = staff_reply(self.inventory, f"Show email addresses on {date}")
        self.assertIn("No authorized email mailboxes", noaccess)

    def test_cross_system_question_respects_partial_permissions(self):
        response, source = staff_reply(self.seller, "How many emails and MoMo transactions were sent today?")
        self.assertNotIn("Recorded POS/debt payment entries", response)
        self.assertIn("Not included due to your account permissions: financial reports", response)

    def test_message_request_is_advice_not_external_send(self):
        text, source = staff_reply(self.seller, "Send an unsolicited Gmail and SMS blast")
        self.assertEqual(source, "draft")
        self.assertIn("cannot send unsolicited", text)
