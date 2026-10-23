"""Analytical KOFAD staff AI must be grounded, role scoped and non-sending."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.utils import timezone

from core.models import Access, Branch, Closing, Product, Stock
from core.staff_ai import staff_reply
from core.staff_decisions import decision_brief, suggested_communication
from core.notification_engine import _period_report


class EvidenceRankedStaffDecisionsTests(TestCase):
    def setUp(self):
        self.hq = Branch.objects.create(name="Accra Branch", code="accra")
        self.hidden = Branch.objects.create(name="Private Warehouse", code="private")
        self.analyst = User.objects.create_user("analyst", password="unused-secret")
        Access.objects.get(user=self.analyst).branches.add(self.hq)
        for name in ("view_reports", "operate_inventory"):
            self.analyst.user_permissions.add(Permission.objects.get(codename=name))
        self.operator = User.objects.create_user("operator", password="unused-secret")
        Access.objects.get(user=self.operator).branches.add(self.hq)
        self.operator.user_permissions.add(Permission.objects.get(codename="operate_inventory"))
        self.today = timezone.localdate()

    def closing(self, date, amount, *, verified=True, branch=None):
        return Closing.objects.create(
            branch=branch or self.hq, date=date,
            expected={}, counted={}, summary={"sales_total": str(amount)},
            submitted_by=self.analyst,
            verified_by=self.analyst if verified else None,
        )

    def test_analytic_brief_uses_verified_closings_and_missing_data_disclaimer(self):
        self.closing(self.today-timedelta(days=9), "200.00")
        self.closing(self.today, "100.00")
        self.closing(self.today-timedelta(days=1), "50.00", verified=False)
        self.closing(self.today, "999999.00", branch=self.hidden)
        answer, source = staff_reply(self.analyst, "Why sales are falling? Show insights")
        self.assertEqual(source, "insights")
        self.assertIn("Sales per verified closing", answer)
        self.assertIn("GHS 100.00", answer)
        self.assertIn("GHS 200.00", answer)
        self.assertIn("-50.0%", answer)
        self.assertIn("Missing closings may distort totals", answer)
        self.assertIn("independent verification", answer)
        self.assertNotIn("999,999", answer)
        self.assertNotIn("Private Warehouse", answer)

    def test_inventory_priorities_are_ranked_without_hidden_branches(self):
        item = Product.objects.create(
            sku="AI-GROUNDED", name="Beans", retail_unit=Decimal("12"),
            reorder_level=5, active=True,
        )
        Stock.objects.create(branch=self.hq, product=item, quantity=0)
        Stock.objects.create(branch=self.hidden, product=item, quantity=1)
        answer = decision_brief(self.operator)
        self.assertIn("1 recorded stock balances are at zero", answer)
        self.assertIn("physical count", answer)
        self.assertNotIn("Private Warehouse", answer)
        self.assertNotIn("sales per verified", answer.casefold())
        self.assertNotIn("GHS", answer)
        answer, source = staff_reply(self.operator, "Please analyse risks")
        self.assertEqual(source, "insights")
        self.assertIn("stock balances", answer)

    def test_unassigned_or_inactive_accounts_get_no_business_information(self):
        user = User.objects.create_user("no-branch", password="test-password")
        user.user_permissions.add(Permission.objects.get(codename="view_reports"))
        self.assertIn("No active", decision_brief(user))
        user.is_active = False
        user.save()
        answer, source = staff_reply(user, "Give a deep analysis")
        self.assertEqual(source, "denied")
        self.assertNotIn("GHS", answer)

    @patch("marketplace.kofad_ai.requests.post")
    @patch("core.telegram_integration.queue_notice")
    def test_communication_plan_does_not_send_messages(self, queue, external):
        response, source = staff_reply(self.operator, "Send customers a marketing SMS")
        self.assertEqual(source, "draft")
        self.assertIn("cannot send unsolicited", response)
        self.assertIn("promotional messages require separate", response)
        self.assertIn("approved Communications Center", response)
        external.assert_not_called()
        queue.assert_not_called()
        self.assertIn("REVIEW BEFORE SENDING", suggested_communication(self.operator))

    def test_period_email_never_infers_sales_collapse_from_missing_closings(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with patch("core.notification_engine._company", return_value=SimpleNamespace(name="KOFAD")):
            with patch("core.notification_engine._window_values", side_effect=[
                {"days": 1, "sales": Decimal("50"), "expenses": Decimal("0"),
                 "collections": Decimal("0"), "cash_variance": Decimal("0"), "variance_dates": []},
                {"days": 7, "sales": Decimal("700"), "expenses": Decimal("0"),
                 "collections": Decimal("0"), "cash_variance": Decimal("0"), "variance_dates": []},
            ]):
                body = _period_report(self.hq, "weekly", self.today-timedelta(days=6), self.today)
        self.assertIn("A percentage sales change would be misleading", body)
        self.assertNotIn("Sales fell over 20%", body)
