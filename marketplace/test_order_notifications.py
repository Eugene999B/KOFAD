from unittest.mock import patch

from django.db import transaction
from django.test import override_settings

from core.models import Message
from .models import EmailIdentity
from core.notification_engine import EmailNotice
from django.utils import timezone
from .notifications import queue_order_sms, process_order_sms
from .tests import MarketFixtures


@override_settings(SMS_ENABLED=True)
class OrderNotificationTests(MarketFixtures):
    @patch("core.sms.service.get_provider")
    def test_order_event_queues_once_without_provider_call(self, provider):
        order = self.order()
        order.payment_status = "paid"
        order.save(update_fields=["payment_status"])
        first = queue_order_sms(order, "paid", "KOFAD payment confirmed.")
        second = queue_order_sms(order, "paid", "KOFAD payment confirmed.")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Message.objects.filter(source_key__startswith="market-event:").count(), 1)
        provider.assert_not_called()

    def test_rollback_does_not_leave_a_payment_confirmation(self):
        order = self.order()
        order.payment_status = "paid"
        order.save(update_fields=["payment_status"])
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
        order.payment_status = "paid"
        order.save(update_fields=["payment_status"])
        message = queue_order_sms(order, "paid", "KOFAD payment confirmed.")
        for status in ("unknown", "sending", "failed", "accepted"):
            message.status = status
            message.save(update_fields=["status"])
            self.assertEqual(process_order_sms(), 0)
        send.assert_not_called()


    def test_never_announces_paid_before_ledger_verification(self):
        order = self.order()
        self.assertIsNone(queue_order_sms(order, "paid", "KOFAD payment confirmed"))
        self.assertFalse(Message.objects.filter(
            source_key=f"market-event:{order.pk}:paid",
        ).exists())

    @override_settings(
        SMS_ENABLED=True, EMAIL_DELIVERY_ENABLED=True,
        EMAIL_AUTOMATIONS_ENABLED=False,
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
    )
    @patch("core.telegram_integration.queue_order_update")
    def test_order_queues_all_three_channels_independently(self, telegram):
        order = self.order()
        EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk,
            email=self.customer.email, verified_at=timezone.now(),
            notifications_enabled=True,
        )
        sms = queue_order_sms(order, "awaiting_payment", "Order received, payment pending.")
        self.assertIsNotNone(sms)
        self.assertEqual(telegram.call_count, 1)
        self.assertTrue(EmailNotice.objects.filter(
            source_key=f"market-email:{order.pk}:awaiting_payment",
        ).exists())
        queue_order_sms(order, "awaiting_payment", "Order received, payment pending.")
        self.assertEqual(Message.objects.filter(
            source_key=f"market-event:{order.pk}:awaiting_payment",
        ).count(), 1)
        self.assertEqual(EmailNotice.objects.filter(
            source_key=f"market-email:{order.pk}:awaiting_payment",
        ).count(), 1)

    @override_settings(
        SMS_ENABLED=True, EMAIL_DELIVERY_ENABLED=True,
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
    )
    @patch("core.telegram_integration.queue_order_update")
    def test_bad_sms_recipient_does_not_block_verified_email_or_telegram(self, telegram):
        order = self.order()
        EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk,
            email=self.customer.email, verified_at=timezone.now(),
            notifications_enabled=True,
        )
        order.phone = "invalid-phone"
        self.assertIsNone(queue_order_sms(
            order, "awaiting_payment", "Order received, payment pending.",
        ))
        telegram.assert_called_once()
        self.assertTrue(EmailNotice.objects.filter(
            source_key=f"market-email:{order.pk}:awaiting_payment",
        ).exists())
