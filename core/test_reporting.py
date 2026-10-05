import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from . import services as s
from .models import Document, PayrollEntry, PayrollPeriod, PayrollRule, Stock, Worker
from .reporting import branch_comparison, limited
from .tests import Fixtures


class BranchReportingTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.today = timezone.localdate()

    def report(self, user=None, first=None, last=None):
        rows, _ = branch_comparison(user or self.user, first or self.today, last or self.today)
        return {row["branch"]: row for row in rows}

    def test_sales_returns_and_standard_cost(self):
        sale = self.sale(2)
        s.post_return(self.reviewer, self.branch, {"line": sale.lines.get().pk, "quantity": 1,
            "reason": "Original item returned", "method": "cash"}, uuid.uuid4())
        row = self.report()["Main"]
        self.assertEqual(row["sales"], Decimal("50"))
        self.assertEqual(row["cost"], Decimal("20"))
        self.assertEqual(row["profit"], Decimal("30"))

    def test_allocations_do_not_duplicate_receivables(self):
        sale = self.sale(2, payments=[], party=self.customer.pk, due_date=str(self.today))
        for amount in [20, 30]:
            s.post_payment(self.user, self.branch, {"invoice": str(sale.pk), "amount": amount, "method": "cash"}, uuid.uuid4())
        self.assertEqual(self.report()["Main"]["receivables"], Decimal("50"))

    def test_reversed_collection_restores_outstanding_balance(self):
        sale = self.sale(2, payments=[], party=self.customer.pk, due_date=str(self.today))
        payment = s.post_payment(self.user, self.branch, {"invoice": str(sale.pk), "amount": 30, "method": "cash"}, uuid.uuid4())
        correction = s.request_correction(self.user, self.branch, payment.pk, "Payment entered by mistake")
        s.review_correction(self.reviewer, self.branch, correction.pk, True)
        self.assertEqual(self.report()["Main"]["receivables"], Decimal("100"))

    def test_expense_reversal_is_net_zero(self):
        expense = s.post_expense(self.user, self.branch, {"amount": 25, "note": "Fuel expense", "method": "cash"}, uuid.uuid4())
        correction = s.request_correction(self.user, self.branch, expense.pk, "Incorrect expense entered")
        s.review_correction(self.reviewer, self.branch, correction.pk, True)
        self.assertEqual(self.report()["Main"]["expenses"], Decimal("0"))

    def test_period_sales_and_current_snapshot_are_distinct(self):
        self.sale()
        rows = self.report(first=self.today-timedelta(days=2), last=self.today-timedelta(days=1))
        self.assertEqual(rows["Main"]["sales"], 0)
        self.assertEqual(rows["Main"]["stock"], 239 * Decimal("20"))
        self.assertEqual(rows["Warehouse"]["stock"], 0)

    def test_staff_branch_scope_and_inactive_branch(self):
        staff = User.objects.create_user("reporter", password="test-private-password")
        staff.user_permissions.add(Permission.objects.get(codename="view_reports"))
        staff.access.branches.add(self.branch)
        self.assertEqual(set(self.report(staff)), {"Main"})
        self.other.active = False
        self.other.save()
        self.assertEqual(set(self.report()), {"Main"})

    def test_reporting_permission_required(self):
        staff = User.objects.create_user("no-reports", password="test-private-password")
        with self.assertRaises(PermissionDenied):
            self.report(staff)

    def test_branch_export_excludes_unassigned_locations(self):
        staff = User.objects.create_user("exporter", password="test-private-password")
        staff.user_permissions.add(Permission.objects.get(codename="view_reports"))
        staff.access.branches.add(self.branch)
        self.authenticate_client(staff)
        response = self.client.get("/reports/export/csv/?family=branches")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Main", response.content)
        self.assertNotIn(b"Warehouse", response.content)

    def test_oversized_reports_fail_instead_of_truncating(self):
        query = Mock()
        query.count.return_value = 10001
        with self.assertRaises(ValidationError):
            limited(query, 10000)

    def test_report_pagination_and_full_export(self):
        Document.objects.bulk_create([Document(branch=self.branch, kind="expense",
            reference=f"PAGE-{i:03}", total=1, paid=1, created_by=self.user) for i in range(105)])
        self.authenticate_client()
        first = self.client.get("/reports/")
        second = self.client.get("/reports/?page=2")
        self.assertEqual(len(first.context["rows"]), 100)
        self.assertEqual(len(second.context["rows"]), 5)
        self.assertEqual(self.client.get("/reports/export/csv/").content.count(b"PAGE-"), 105)


    def test_accounting_renders_locked_payroll_entries_with_current_ssnit_field(self):
        rule = PayrollRule.objects.create(
            name="Accounting regression payroll rule",
            effective_from=self.today.replace(day=1),
            created_by=self.user,
        )
        worker = Worker.objects.create(
            employee_code="ACC-001",
            branch=self.branch,
            first_name="Accounting",
            last_name="Worker",
            phone="0241234567",
            job_title="Tester",
            hire_date=self.today.replace(day=1),
            created_by=self.user,
        )
        period = PayrollPeriod.objects.create(
            branch=self.branch,
            year=self.today.year,
            month=self.today.month,
            start_date=self.today.replace(day=1),
            end_date=self.today,
            rule=rule,
            status="locked",
            created_by=self.user,
        )
        PayrollEntry.objects.create(
            period=period,
            worker=worker,
            gross_pay=Decimal("1000"),
            ssnit_employee=Decimal("55"),
            employer_pension=Decimal("130"),
            net_pay=Decimal("945"),
        )
        self.authenticate_client()
        response = self.client.get("/accounting/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accounting Intelligence")
