from unittest.mock import patch

from django.core import signing
from django.test import override_settings
from django.utils import timezone

from core.models import EmailNotice
from core.notification_engine import (
    process_email_outbox, queue_customer_order_email,
    run_customer_personalised_promotions,
)
from .models import WishlistItem
from .tests import MarketFixtures


@override_settings(EMAIL_AUTOMATIONS_ENABLED=True, EMAIL_DELIVERY_ENABLED=False)
class CustomerEmailTests(MarketFixtures):
    def authenticate_customer(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def test_order_email_idempotent_and_pauses_after_opt_out(self):
        order = self.order()
        order.status = "paid"
        order.payment_status = "paid"
        order.save(update_fields=["status", "payment_status"])
        self.assertEqual(queue_customer_order_email(order, "paid"), 1)
        self.assertEqual(queue_customer_order_email(order, "paid"), 0)
        self.assertEqual(EmailNotice.objects.filter(category="order").count(), 1)
        self.customer.transactional_email_enabled = False
        self.customer.save(update_fields=["transactional_email_enabled"])
        with override_settings(EMAIL_DELIVERY_ENABLED=True):
            with patch("core.notification_engine.EmailMultiAlternatives.send") as mock_send:
                self.assertEqual(process_email_outbox(), 0)
                mock_send.assert_not_called()

    def test_marketing_requires_signed_one_time_mailbox_confirmation(self):
        self.authenticate_customer()
        url = "/market/account/"
        response = self.client.post(url, {
            "action": "email_preferences",
            "notification_email": "customer@example.test",
            "transactional_email_enabled": "on",
            "marketing_email_opt_in": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.marketing_email_opt_in)
        self.assertTrue(self.customer.marketing_email_challenge)
        notice = EmailNotice.objects.get(category="marketing_verify")
        self.assertIn("email-confirm/", notice.body)
        # A signed token is valid once for the exact saved challenge/address.
        token = signing.dumps({
            "customer": self.customer.pk, "email": self.customer.email,
            "purpose": "marketing", "challenge": self.customer.marketing_email_challenge,
        }, salt="kofad-market-email-v1", compress=True)
        confirmation = self.client.get(f"/market/account/email-confirm/{token}/")
        self.assertEqual(confirmation.status_code, 200)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.marketing_email_opt_in)
        self.assertEqual(self.customer.marketing_email_challenge, "")
        self.assertEqual(self.client.get(f"/market/account/email-confirm/{token}/").status_code, 400)
        # Opting out does not disable transactional notices.
        self.client.post(url, {
            "action": "email_preferences",
            "notification_email": self.customer.email,
            "transactional_email_enabled": "on",
        })
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.marketing_email_opt_in)
        self.assertTrue(self.customer.transactional_email_enabled)
        self.assertEqual(self.client.get(f"/market/account/email-confirm/{token}/").status_code, 400)

    def test_promotions_only_to_confirmed_customers_with_saved_listings(self):
        WishlistItem.objects.create(customer=self.customer, listing=self.listing)
        monday = timezone.make_aware(
            timezone.datetime(2026, 10, 12, 9, 10), timezone.get_current_timezone()
        )
        self.assertEqual(run_customer_personalised_promotions(monday), 0)
        self.customer.marketing_email_opt_in = True
        self.customer.marketing_email_verified_at = timezone.now()
        self.customer.save(update_fields=["marketing_email_opt_in", "marketing_email_verified_at"])
        self.assertEqual(run_customer_personalised_promotions(monday), 1)
        self.assertEqual(run_customer_personalised_promotions(monday), 0)
        self.assertEqual(EmailNotice.objects.filter(category="marketing").count(), 1)
