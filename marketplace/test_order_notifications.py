from unittest.mock import patch

from django.db import transaction
from django.test import override_settings

from core.models import Message
from .notifications import queue_order_sms, process_order_sms
from .tests import MarketFixtures


@override_settings(SMS_ENABLED=True)
class OrderNotificationTests(MarketFixtures):
    @patch("core.sms.service.get_provider")
    def test_order_event_queues_once_without_provider_call(self, provider):
        order = self.order()
        first = queue_order_sms(order, "paid", "KOFAD payment confirmed.")
        second = queue_order_sms(order, "paid", "KOFAD payment confirmed.")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Message.objects.filter(source_key__startswith="market-event:").count(), 1)
        provider.assert_not_called()

    def test_rollback_does_not_leave_a_payment_confirmation(self):
        order = self.order()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                queue_order_sms(order, "paid", "KOFAD payment confirmed.")
                raise RuntimeError("Posting rolled back")
        self.assertFalse(Message.objects.filter(source_key__startswith="market-event:").exists())

    @override_settings(SMS_ENABLED=False)
    def test_disabled_sms_does_not_accumulate_old_notifications(self):
        self.assertIsNone(queue_order_sms(self.order(), "paid", "KOFAD payment confirmed."))
        self.assertEqual(process_order_sms(), 0)
        self.assertFalse(Message.objects.exists())

    @patch("marketplace.notifications.send_message_now")
    @patch("marketplace.notifications.validate_config")
    def test_worker_ignores_uncertain_and_failed_submissions(self, config, send):
        order = self.order()
        message = queue_order_sms(order, "paid", "KOFAD payment confirmed.")
        for status in ("unknown", "sending", "failed", "accepted"):
            message.status = status
            message.save(update_fields=["status"])
            self.assertEqual(process_order_sms(), 0)
        send.assert_not_called()
