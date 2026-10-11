"""Order creation should restore opt-in email without causing chargeable SMS."""
from django.test import override_settings

from core.models import EmailNotice, Message
from marketplace.models import EmailIdentity
from marketplace.notifications import queue_order_email
from marketplace.tests import MarketFixtures
from django.utils import timezone


@override_settings(EMAIL_AUTOMATIONS_ENABLED=True, EMAIL_DELIVERY_ENABLED=False, SMS_ENABLED=True)
class CreatedOrderEmailRecoveryTests(MarketFixtures):
    def test_created_order_queues_one_verified_account_email_but_no_sms(self):
        self.customer.transactional_email_enabled = True
        self.customer.save(update_fields=["transactional_email_enabled"])
        EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk, email=self.customer.email,
            verified_at=timezone.now(), notifications_enabled=True,
        )
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            order = self.order()
        self.assertEqual(len(callbacks), 1)
        key = f"market-email:{order.pk}:awaiting_payment"
        self.assertEqual(EmailNotice.objects.filter(source_key=key, category="order").count(), 1)
        self.assertEqual(Message.objects.filter(source_key__startswith=f"market-event:{order.pk}:").count(), 0)
        self.assertEqual(queue_order_email(order, "awaiting_payment"), 0)
        self.assertEqual(EmailNotice.objects.filter(source_key=key).count(), 1)

    def test_opted_out_customer_never_gets_created_order_email(self):
        self.customer.transactional_email_enabled = False
        self.customer.save(update_fields=["transactional_email_enabled"])
        with self.captureOnCommitCallbacks(execute=True):
            order = self.order()
        self.assertFalse(EmailNotice.objects.filter(source_key=f"market-email:{order.pk}:awaiting_payment").exists())
