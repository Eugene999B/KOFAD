import re
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (
    Access, Branch, Closing, TelegramIdentity, TelegramLinkChallenge, TelegramOutbound,
)
from core.telegram_integration import (
    accept_token, begin_link, confirm_link, linked_identity, queue_closing_update,
    queue_notice, queue_order_update,
)
from core.telegram_assistant import reply_for
from core.telegram_delivery import deliver_pending
from core.telegram_recovery import request_recovery, complete_recovery
from marketplace.models import CustomerAccount, OnlineOrder


@override_settings(
    TELEGRAM_BOT_ENABLED=True, TELEGRAM_ACCOUNT_LINK_ENABLED=True,
    TELEGRAM_BUSINESS_ANALYTICS_ENABLED=True, TELEGRAM_NOTIFICATIONS_ENABLED=True,
    TELEGRAM_RECOVERY_ENABLED=True, TELEGRAM_BOT_TOKEN="test-only",
)
class EnterpriseTelegramTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser("kofad_staff", "test@example.test", "StaffTestPass!4087")
        self.access = Access.objects.create(user=self.staff, recovery_phone="+233241234567")
        self.branch = Branch.objects.create(name="Test Branch", code="t-branch")
        self.access.branches.add(self.branch)
        self.customer = CustomerAccount(full_name="Customer Tester", phone="+233541234567",
                                        verified_at=timezone.now())
        self.customer.set_password("CustomerPass!12345")
        self.customer.save()

    def link(self, kind, account, chat=12345):
        token = begin_link(kind, account.pk).split("link_", 1)[1]
        answer = accept_token(token, chat, chat)
        self.assertIn("Expires in 10 minutes", answer)
        code = re.search(r"\n(\d{6})\n", answer).group(1)
        self.assertIsNone(linked_identity(kind, account.pk))
        self.assertTrue(confirm_link(kind, account.pk, code))
        return linked_identity(kind, account.pk)

    def test_requires_both_website_and_telegram_confirmation(self):
        self.link("staff", self.staff)
        self.assertIsNotNone(linked_identity("staff", self.staff.pk))
        self.assertEqual(TelegramIdentity.objects.count(), 1)

    def test_wrong_confirmation_and_expired_pairing(self):
        token = begin_link("staff", self.staff.pk).split("link_", 1)[1]
        self.assertIn("this code", accept_token(token, 123, 123))
        for _ in range(5):
            self.assertFalse(confirm_link("staff", self.staff.pk, "000001"))
        self.assertEqual(TelegramIdentity.objects.count(), 0)
        self.assertFalse(confirm_link("staff", self.staff.pk, "000001"))
        TelegramLinkChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIn("invalid or expired", accept_token(token, 123, 123))

    def test_password_change_invalidates_existing_link(self):
        link = self.link("staff", self.staff)
        self.assertIsNotNone(link)
        self.staff.set_password("ChangedStaffTest!4098")
        self.staff.save()
        self.assertIsNone(linked_identity("staff", self.staff.pk))
        self.assertIn("requires", reply_for("/analysis", 12345, 12345))

    def test_no_cross_customer_order_disclosure(self):
        self.link("customer", self.customer, chat=88)
        response = reply_for("/orders", 88, 88)
        self.assertIn("No matching order", response)
        self.assertNotIn("bank", response.casefold())

    def test_private_reports_require_management_account(self):
        self.link("customer", self.customer, chat=88)
        self.assertIn("authorized KOFAD staff", reply_for("/analysis", 88, 88))
        self.link("staff", self.staff, chat=91)
        body = reply_for("/analysis", 91, 91)
        self.assertIn("VERIFIED CLOSING INTELLIGENCE", body)
        self.assertIn("not a profit", body)

    def test_closing_notifications_are_opt_in_deduplicated(self):
        identity = self.link("staff", self.staff)
        closing = Closing.objects.create(
            branch=self.branch, date=timezone.localdate(), expected={"cash": "100"},
            counted={"cash": "900"}, summary={"sales_total": "2000", "expenses_total": "300"},
            submitted_by=self.staff, verified_by=self.staff,
        )
        self.assertEqual(queue_closing_update(closing), 0)
        identity.notifications_enabled = True
        identity.closing_opt_in = True
        identity.critical_opt_in = True
        identity.save()
        self.assertEqual(queue_closing_update(closing), 1)
        self.assertEqual(queue_closing_update(closing), 0)
        self.assertEqual(TelegramOutbound.objects.count(), 1)

    @patch("core.telegram_delivery.requests.post")
    def test_send_once_and_suppress_after_revoke(self, post):
        identity = self.link("customer", self.customer)
        identity.notifications_enabled = True
        identity.save()
        queue_notice(identity, "order", "tg:test:123", "Your KOFAD order has shipped.")
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"ok": True, "result": {"message_id": 7}}
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(deliver_pending(), 0)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(TelegramOutbound.objects.get().status, "sent")

    @patch("core.telegram_delivery.requests.post")
    def test_unknown_provider_outcome_is_never_resent(self, post):
        import requests
        identity = self.link("customer", self.customer)
        identity.notifications_enabled = True
        identity.save()
        queue_notice(identity, "order", "tg:test:uncertain", "Your KOFAD order has shipped.")
        post.side_effect = requests.Timeout
        self.assertEqual(deliver_pending(), 0)
        self.assertEqual(deliver_pending(), 0)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(TelegramOutbound.objects.get().status, "unknown")

    def test_opt_in_customer_recovery_and_original_password_invalidated(self):
        link = self.link("customer", self.customer, chat=88)
        self.assertIsNone(request_recovery("customer", self.customer.phone, ""))
        link.recovery_opt_in = True
        link.save()
        challenge_id = request_recovery("customer", self.customer.phone, "")
        self.assertIsNotNone(challenge_id)
        msg = TelegramOutbound.objects.get(source_key="tg:recovery:" + str(challenge_id))
        code = re.search(r"\n(\d{6})\n", msg.body).group(1)
        self.assertFalse(complete_recovery("customer", challenge_id, "999999", "NewPass!2026Kofad", "NewPass!2026Kofad")[0])
        self.assertFalse(complete_recovery("customer", challenge_id, code, "NewPass!2026Kofad", "NewPass!2026Kofad")[0])
        msg.status = "sent"
        msg.save(update_fields=["status"])
        ok, message = complete_recovery("customer", challenge_id, code, "NewPass!2026Kofad", "NewPass!2026Kofad")
        self.assertTrue(ok, message)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.check_password("NewPass!2026Kofad"))
        self.assertIsNone(linked_identity("customer", self.customer.pk))

    def test_unlinked_telegram_is_not_a_phone_authentication_factor(self):
        self.assertIsNone(request_recovery("customer", self.customer.phone, ""))
        self.assertIn("requires", reply_for("/analysis", 61234, 61234))
