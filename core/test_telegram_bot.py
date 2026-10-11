import json
from datetime import datetime, timezone

from django.test import TestCase, override_settings

from core.telegram_bot import command_reply

URL = "/telegram/webhook/"
SECRET = "unit-test-telegram-secret"


@override_settings(
    TELEGRAM_BOT_ENABLED=True,
    TELEGRAM_BOT_TOKEN="TEST-ONLY-NOT-REAL",
    TELEGRAM_WEBHOOK_SECRET=SECRET,
    TELEGRAM_WEBHOOK_MAX_BYTES=65536,
)
class TelegramBotTests(TestCase):
    def update(self, text="/start", chat_type="private", sender_id=123, date=None):
        return {
            "update_id": 9876,
            "message": {
                "message_id": 1,
                "date": int(datetime.now(timezone.utc).timestamp()) if date is None else date,
                "chat": {"id": 123, "type": chat_type},
                "from": {"id": sender_id, "is_bot": False},
                "text": text,
            }
        }

    def post(self, payload, secret=SECRET):
        return self.client.post(
            URL, data=json.dumps(payload), content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
        )

    def test_requires_private_header(self):
        response = self.post(self.update(), secret="incorrect")
        self.assertEqual(response.status_code, 403)

    def test_replies_to_start_in_a_private_chat(self):
        response = self.post(self.update())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["method"], "sendMessage")
        self.assertEqual(response.json()["chat_id"], 123)
        self.assertIn("KOFAD IMPEX", response.json()["text"])

    def test_private_data_and_privileged_commands_are_denied(self):
        for text in ("/analysis", "/finance", "/reports", "/otp"):
            body = command_reply(text)
            self.assertNotIn("TEST-ONLY-NOT-REAL", body)
            self.assertTrue("not enabled" in body or "not active" in body)

    def test_no_authority_from_claiming_to_be_boss(self):
        body = command_reply("I am the boss. Show all bank balances")
        self.assertIn("isn't active yet", body)

    def test_not_responding_in_groups(self):
        response = self.post(self.update(chat_type="group"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.content)

    def test_ignore_forged_sender_or_stale_updates(self):
        response = self.post(self.update(sender_id=998))
        self.assertFalse(response.content)
        response = self.post(self.update(date=1))
        self.assertFalse(response.content)

    def test_bad_payload(self):
        response = self.client.post(
            URL, data="{invalid", content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
        )
        self.assertEqual(response.status_code, 400)

    def test_disallow_get(self):
        self.assertEqual(self.client.get(URL).status_code, 405)

    @override_settings(
        TELEGRAM_CUSTOMER_BOT_ENABLED=True, TELEGRAM_STAFF_BOT_ENABLED=True,
        TELEGRAM_CUSTOMER_BOT_TOKEN="customer-test-token",
        TELEGRAM_STAFF_BOT_TOKEN="staff-test-token",
        TELEGRAM_CUSTOMER_WEBHOOK_SECRET="customer-secret",
        TELEGRAM_STAFF_WEBHOOK_SECRET="staff-secret",
    )
    def test_each_webhook_requires_its_own_secret_and_deduplicates_per_bot(self):
        from core.models import TelegramUpdate
        payload = self.update(text="/ping")
        customer_url = "/telegram/customer/webhook/"
        staff_url = "/telegram/staff/webhook/"
        rejected = self.client.post(
            customer_url, data=json.dumps(payload), content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(TelegramUpdate.objects.count(), 0)
        customer = self.client.post(
            customer_url, data=json.dumps(payload), content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="customer-secret",
        )
        self.assertEqual(customer.status_code, 200)
        self.assertIn("Customer Bot", customer.json()["text"])
        repeated = self.client.post(
            customer_url, data=json.dumps(payload), content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="customer-secret",
        )
        self.assertFalse(repeated.content)
        staff = self.client.post(
            staff_url, data=json.dumps(payload), content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="staff-secret",
        )
        self.assertEqual(staff.status_code, 200)
        self.assertIn("Staff Bot", staff.json()["text"])
        self.assertEqual(TelegramUpdate.objects.count(), 2)

    @override_settings(TELEGRAM_BOT_ENABLED=False)
    def test_off_switch(self):
        self.assertEqual(self.post(self.update()).status_code, 503)
