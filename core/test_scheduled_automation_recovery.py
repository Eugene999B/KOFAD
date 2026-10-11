"""Contract tests for notification jobs dropped in the 2026-10-10 promotion.

No real messages, paid providers or customer data are involved.
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from .automations import run_scheduled_automations


class RecoveredScheduledCommunicationTests(SimpleTestCase):
    @patch("core.notification_engine.run_customer_personalised_promotions", return_value=5)
    @patch("core.notification_engine.run_staff_scheduled_reports", return_value=4)
    @patch("core.debt_email.run_debt_email_reminders", return_value=3)
    @patch("core.automations.run_low_stock_summary", return_value=2)
    @patch("core.automations.run_debt_reminders", return_value=1)
    def test_all_five_schedulers_run_once(self, debt, low_stock, debt_mail, staff, customer):
        sentinel_now = object()
        result = run_scheduled_automations(sentinel_now)
        self.assertEqual(result, {
            "debt": 1, "debt_email": 3, "low_stock": 2,
            "email_reports": 4, "customer_email": 5,
        })
        for mock_job in (debt, debt_mail, low_stock, staff, customer):
            mock_job.assert_called_once_with(sentinel_now)

    @patch("core.notification_engine.run_customer_personalised_promotions", return_value=5)
    @patch("core.notification_engine.run_staff_scheduled_reports", side_effect=RuntimeError("test failure"))
    @patch("core.debt_email.run_debt_email_reminders", side_effect=RuntimeError("test failure"))
    @patch("core.automations.run_low_stock_summary", return_value=2)
    @patch("core.automations.run_debt_reminders", side_effect=RuntimeError("test failure"))
    def test_one_job_failure_does_not_skip_unrelated_schedules(self, debt, low_stock, debt_mail, staff, customer):
        with self.assertLogs("core.automations", "ERROR") as captured:
            result = run_scheduled_automations()
        self.assertEqual(result, {
            "debt": 0, "debt_email": 0, "low_stock": 2,
            "email_reports": 0, "customer_email": 5,
        })
        self.assertEqual(len(captured.output), 3)
        for mock_job in (debt, debt_mail, low_stock, staff, customer):
            mock_job.assert_called_once_with(None)
