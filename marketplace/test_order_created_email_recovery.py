"""Order-confirmation recovery: queue after commit and respect customer consent."""
from django.test import override_settings

from core.notification_engine import EmailNotice, _may_deliver
from marketplace.models import EmailIdentity
from django.utils import timezone
from marketplace.tests import MarketFixtures


@override_settings(EMAIL_AUTOMATIONS_ENABLED=True, EMAIL_DELIVERY_ENABLED=True)
class NewOrderEmailRecoveryTests(MarketFixtures):
    def setUp(self):
        super().setUp()
        EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk, email=self.customer.email,
            verified_at=timezone.now(), notifications_enabled=True,
        )

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
        notice = EmailNotice.objects.get(
            source_key=f"market-email:{order.pk}:awaiting_payment",
        )
        self.assertTrue(_may_deliver(notice))
        self.assertIn("KOFAD order update: Awaiting payment", notice.subject)
        self.assertIn("Your KOFAD order has a new update", notice.body)
        self.assertNotIn("order Awaiting payment", notice.body)
        self.assertNotIn(order.public_reference, notice.body)
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
    def test_transactional_order_email_does_not_depend_on_scheduled_automations(self):
        with self.captureOnCommitCallbacks(execute=True):
            order = self.order()
        self.assertTrue(EmailNotice.objects.filter(
            source_key=f"market-email:{order.pk}:awaiting_payment",
        ).exists())

    @override_settings(EMAIL_DELIVERY_ENABLED=False)
    def test_disabled_delivery_does_not_queue_deferred_customer_notices(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.order()
        self.assertFalse(EmailNotice.objects.filter(category="order").exists())
