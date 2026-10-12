"""Security regression checks for Telegram Mini App fast sign-in."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Access, Branch, TelegramIdentity, TelegramOutbound
from core.telegram_quick_auth import (
    TelegramMiniAppError, link_miniapp_identity, verify_miniapp_data,
)
from core.telegram_intelligence import account_tasks, staff_menu, queue_role_briefings
from marketplace.models import CustomerAccount


@override_settings(STORAGES={**settings.STORAGES, "staticfiles": {
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}})
@override_settings(
    TELEGRAM_BOT_ENABLED=True, TELEGRAM_ACCOUNT_LINK_ENABLED=True,
    TELEGRAM_NOTIFICATIONS_ENABLED=True, TELEGRAM_RECOVERY_ENABLED=True,
    TELEGRAM_BOT_TOKEN="official-ci-token",
    TELEGRAM_WEBHOOK_SECRET="official-ci-secret",
    TELEGRAM_STAFF_BOT_ENABLED=True, TELEGRAM_STAFF_BOT_TOKEN="staff-ci-token",
    TELEGRAM_STAFF_WEBHOOK_SECRET="staff-ci-secret",
    TELEGRAM_CUSTOMER_BOT_ENABLED=True, TELEGRAM_CUSTOMER_BOT_TOKEN="market-ci-token",
    TELEGRAM_CUSTOMER_WEBHOOK_SECRET="market-ci-secret",
)
class TelegramQuickAuthTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(
            "telegram-admin-ci", "telegram-ci@example.test", "TestPassword!2026"
        )
        self.access, _ = Access.objects.get_or_create(user=self.staff)
        self.access.save()
        self.customer = CustomerAccount(full_name="Mini App CI customer",
                                        phone="+233541234567", verified_at=timezone.now())
        self.customer.set_password("MarketPassword!2026")
        self.customer.save()

    def signed(self, bot="staff", user_id=42133, timestamp=None):
        tokens = {"staff": "staff-ci-token", "official": "official-ci-token",
                  "customer": "market-ci-token"}
        pairs = [
            ("auth_date", str(int(time.time()) if timestamp is None else timestamp)),
            ("user", json.dumps({"id": user_id, "first_name": "KOFAD", "is_bot": False},
                                separators=(",", ":"))),
        ]
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs))
        secret = hmac.new(b"WebAppData", tokens[bot].encode(), hashlib.sha256).digest()
        checksum = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        return urlencode(pairs + [("hash", checksum)])

    def staff_session(self):
        self.client.force_login(self.staff)
        session = self.client.session
        session["access_version"] = self.access.session_version
        session["staff_session_expires_at"] = timezone.now().timestamp() + 3600
        session.save()

    def test_miniapp_signature_rejects_other_bot_tampering_and_stale_data(self):
        data = self.signed()
        self.assertEqual(verify_miniapp_data("staff", data)["id"], 42133)
        with self.assertRaises(TelegramMiniAppError):
            verify_miniapp_data("official", data)
        with self.assertRaises(TelegramMiniAppError):
            verify_miniapp_data("staff", data.replace("42133", "42134"))
        with self.assertRaises(TelegramMiniAppError):
            verify_miniapp_data("staff", self.signed(timestamp=int(time.time()) - 400))
        with self.assertRaises(TelegramMiniAppError):
            verify_miniapp_data("staff", data + "&hash=123")

    def test_website_login_required_and_telegram_signed_proof_required(self):
        url = "/integrations/telegram/quick/staff/"
        response = self.client.post(url, {
            "telegram_init_data": self.signed(), "alerts": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TelegramIdentity.objects.exists())
        self.staff_session()
        response = self.client.post(url, {"telegram_init_data": "", "alerts": "on"})
        self.assertContains(response, "Open this page using the Sign in button")
        self.assertFalse(TelegramIdentity.objects.exists())
        response = self.client.post(url, {
            "telegram_init_data": self.signed(), "alerts": "on",
        })
        self.assertContains(response, "Account connected")
        identity = TelegramIdentity.objects.get(bot_kind="staff", kind="staff")
        self.assertEqual(identity.telegram_user_id, 42133)
        self.assertTrue(identity.notifications_enabled)
        self.assertTrue(identity.critical_opt_in)

    def test_staff_account_can_link_official_bot_without_phone_and_receive_recovery(self):
        self.staff_session()
        self.assertEqual(self.access.recovery_phone, "")
        response = self.client.post("/integrations/telegram/quick/official/", {
            "telegram_init_data": self.signed(bot="official"), "recovery": "on",
        })
        self.assertContains(response, "Account connected")
        linked = TelegramIdentity.objects.get(kind="staff", bot_kind="official")
        self.assertTrue(linked.recovery_opt_in)
        from core.telegram_recovery import request_recovery
        code_id = request_recovery("staff", "", self.staff.username)
        self.assertIsNotNone(code_id)
        self.assertTrue(TelegramOutbound.objects.filter(category="recovery").exists())

    def test_cannot_silently_transfer_bot_chat_between_kofad_users(self):
        link_miniapp_identity("staff", self.staff.pk, "staff", 42133)
        someone = User.objects.create_user(
            "different-staff-ci", password="IndependentTestPassword!2026",
        )
        Access.objects.get_or_create(user=someone)
        with self.assertRaises(TelegramMiniAppError):
            link_miniapp_identity("staff", someone.pk, "staff", 42133)
        with self.assertRaises(TelegramMiniAppError):
            link_miniapp_identity("staff", self.staff.pk, "staff", 39921)
        self.assertEqual(TelegramIdentity.objects.count(), 1)

    def test_customer_promotion_is_not_silently_enabled(self):
        link = link_miniapp_identity("customer", self.customer.pk, "customer", 53321,
                                    alerts=True, marketing=False)
        self.assertTrue(link.notifications_enabled)
        self.assertFalse(link.marketing_opt_in)

    def test_staff_menu_and_role_duties_respect_access(self):
        branch = Branch.objects.create(name="CI Telegram Branch", code="tg-ci-test")
        self.access.branches.add(branch)
        menu = staff_menu(self.staff)
        self.assertIn("/month", menu)
        self.assertIn("/inventory", menu)
        self.assertNotIn("password", menu.casefold())
        self.assertNotIn("KOFAD OPERATIONAL PRIORITIES", account_tasks(self.staff))
        linked = link_miniapp_identity("staff", self.staff.pk, "staff", 78398, alerts=True)
        self.assertTrue(linked.notifications_enabled)
        self.assertEqual(queue_role_briefings(timezone.localtime().replace(hour=8)), 0)
