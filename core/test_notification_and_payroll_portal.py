from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Branch, Closing, Company, EmailNotice, PayrollEntry, PayrollPeriod, PayrollRule, Worker
from .notification_engine import process_email_outbox, queue_closing_reports


class StaffPayrollIsolationTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Main", code="main")
        self.second_branch = Branch.objects.create(name="Other", code="other")
        Company.objects.create()
        self.owner = User.objects.create_superuser("owner", password="Test-pass-1234567")
        self.staff = User.objects.create_user("worker-one", password="Test-pass-1234567")
        self.other = User.objects.create_user("worker-two", password="Test-pass-1234567")
        self.unlinked = User.objects.create_user("unlinked", password="Test-pass-1234567")
        for user in (self.staff, self.other, self.unlinked):
            user.access.branches.add(self.branch)
        self.worker = Worker.objects.create(
            employee_code="KFD-SELF-1", branch=self.branch, user=self.staff,
            first_name="Ama", last_name="Worker", phone="+233241234501",
            job_title="Operations", hire_date=date(2024, 1, 1), created_by=self.owner,
        )
        self.worker_two = Worker.objects.create(
            employee_code="KFD-SELF-2", branch=self.branch, user=self.other,
            first_name="Kofi", last_name="Worker", phone="+233241234502",
            job_title="Operations", hire_date=date(2024, 1, 1), created_by=self.owner,
        )
        rule = PayrollRule.objects.create(
            code="GH-PAYROLL", name="Test rules", effective_from=date(2024, 1, 1),
        )
        self.period = PayrollPeriod.objects.create(
            year=2026, month=10, branch=self.branch,
            start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
            status="locked", rule=rule, created_by=self.owner,
        )
        self.own_entry = PayrollEntry.objects.create(
            period=self.period, worker=self.worker, gross_pay=Decimal("1500"),
            net_pay=Decimal("1300"),
        )
        self.other_entry = PayrollEntry.objects.create(
            period=self.period, worker=self.worker_two, gross_pay=Decimal("9000"),
            net_pay=Decimal("8000"),
        )

    def login(self, user):
        user.access.refresh_from_db()
        self.client.force_login(user)
        session = self.client.session
        session["access_version"] = user.access.session_version
        session["branch"] = self.branch.pk
        session["mfa_ok"] = True
        session.save()

    def test_linked_employee_sees_only_own_released_payroll(self):
        self.login(self.staff)
        response = self.client.get("/payroll/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ama Worker")
        self.assertContains(response, "1300.00")
        self.assertNotContains(response, "8000.00")
        self.assertNotContains(response, "KFD-SELF-2")
        own = self.client.get(f"/payroll/{self.period.pk}/payslip/{self.own_entry.pk}/pdf/")
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own["Content-Type"], "application/pdf")
        self.assertEqual(
            self.client.get(f"/payroll/{self.period.pk}/payslip/{self.other_entry.pk}/pdf/").status_code, 404
        )
        self.assertEqual(self.client.get(f"/payroll/{self.period.pk}/export/csv/").status_code, 403)
        self.assertEqual(self.client.get(f"/payroll/{self.period.pk}/").status_code, 403)
        self.assertEqual(self.client.post("/payroll/", {"year": "2026", "month": "10"}).status_code, 403)

    def test_draft_salary_is_never_released_to_employee(self):
        self.period.status = "draft"
        self.period.save(update_fields=["status"])
        self.login(self.staff)
        response = self.client.get("/payroll/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "1300.00")
        self.assertEqual(
            self.client.get(f"/payroll/{self.period.pk}/payslip/{self.own_entry.pk}/pdf/").status_code, 404
        )

    def test_unlinked_employee_cannot_view_payroll(self):
        self.login(self.unlinked)
        self.assertEqual(self.client.get("/payroll/").status_code, 403)

    def test_admin_can_link_an_existing_worker_to_staff(self):
        self.login(self.owner)
        # Link an initially unlinked worker through the new admin-only form field.
        self.worker.user = None
        self.worker.save(update_fields=["user"])
        data = {
            "employee_code": self.worker.employee_code, "first_name": self.worker.first_name,
            "last_name": self.worker.last_name, "phone": self.worker.phone,
            "job_title": self.worker.job_title, "hire_date": "2024-01-01",
            "status": "active", "employment_type": "permanent",
            "staff_user": str(self.staff.pk), "ssnit_enabled": "on",
        }
        self.assertEqual(self.client.post(f"/workers/{self.worker.pk}/edit/", data).status_code, 302)
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.user_id, self.staff.pk)


@override_settings(EMAIL_AUTOMATIONS_ENABLED=True, EMAIL_DELIVERY_ENABLED=False,
                   SMS_STAFF_NOTICES_ENABLED=False)
class StaffNotificationTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Main", code="main")
        Company.objects.create(closing_tolerance=Decimal("20"))
        self.owner = User.objects.create_superuser(
            "owner-report", "owner-report@example.test", "Test-pass-1234567"
        )
        self.owner.access.branches.add(self.branch)
        self.owner.access.email_daily_closing = True
        self.owner.access.email_critical_alerts = True
        self.owner.access.save(update_fields=["email_daily_closing", "email_critical_alerts"])
        self.closing = Closing.objects.create(
            branch=self.branch, date=date(2026, 10, 10),
            expected={"cash": "2000"}, counted={"cash": "1200"},
            summary={"sales_total": "5000", "expenses_total": "300",
                     "debt_collections": "250"},
            submitted_by=self.owner,
        )

    def test_repeated_daily_closing_queues_one_report_and_one_critical(self):
        self.assertEqual(queue_closing_reports(self.closing), 2)
        self.assertEqual(queue_closing_reports(self.closing), 0)
        self.assertEqual(EmailNotice.objects.count(), 2)
        self.assertIn("5,000.00", EmailNotice.objects.get(category="daily").body)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    @patch("core.notification_engine.EmailMultiAlternatives.send", return_value=1)
    def test_email_cannot_be_sent_after_unsubscribe(self, send):
        queue_closing_reports(self.closing)
        self.owner.access.email_critical_alerts = False
        self.owner.access.email_daily_closing = False
        self.owner.access.save(update_fields=["email_critical_alerts", "email_daily_closing"])
        self.assertEqual(process_email_outbox(), 0)
        send.assert_not_called()
        self.assertFalse(EmailNotice.objects.exclude(status="cancelled").exists())

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    @patch("core.notification_engine.EmailMultiAlternatives.send", return_value=1)
    def test_valid_email_delivered_exactly_once(self, send):
        queue_closing_reports(self.closing)
        self.assertEqual(process_email_outbox(), 2)
        self.assertEqual(process_email_outbox(), 0)
        self.assertEqual(send.call_count, 2)
