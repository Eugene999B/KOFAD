"""Order-confirmation recovery: queue after commit and respect customer consent."""
from django.test import override_settings

from core.notification_engine import EmailNotice
from marketplace.tests import MarketFixtures


@override_settings(EMAIL_AUTOMATIONS_ENABLED=True, EMAIL_DELIVERY_ENABLED=False)
class NewOrderEmailRecoveryTests(MarketFixtures):
    def test_eligible_order_is_queued_only_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            order = self.order()
            self.assertFalse(EmailNotice.objects.filter(
                source_key=f"market-email:{order.pk}:awaiting_payment",
            ).exists())
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(EmailNotice.objects.filter(
            source_key=f"market-email:{order.pk}:awaiting_payment", category="order",
        ).count(), 1)
        # Never queue the same order event twice.
        from core.notification_engine import queue_customer_order_email
        self.assertEqual(queue_customer_order_email(order, "awaiting_payment"), 0)

    def test_customer_opt_out_prevents_order_created_email(self):
        self.customer.transactional_email_enabled = False
        self.customer.save(update_fields=["transactional_email_enabled"])
        with self.captureOnCommitCallbacks(execute=True):
            order = self.order()
        self.assertFalse(EmailNotice.objects.filter(
            source_key=f"market-email:{order.pk}:awaiting_payment",
        ).exists())

    @override_settings(EMAIL_AUTOMATIONS_ENABLED=False)
    def test_disabled_automations_never_queue_order_created_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.order()
        self.assertFalse(EmailNotice.objects.filter(category="order").exists())
