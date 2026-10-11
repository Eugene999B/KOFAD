"""Regression tests for support directory, recovery, consent and send cap."""
import re
from unittest.mock import patch, Mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.brevo_email import send_brevo, usage_today, DailyEmailLimitExceeded
from core.email_campaigns import queue_active_campaigns, is_campaign_recipient_allowed
from core.email_models import EmailCampaign, EmailLetter
from core.models import Access, CustomerServiceContact, PasswordRecovery
from marketplace.models import CustomerAccount, EmailIdentity
from marketplace import email_recovery


BREVO = dict(
    KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_PROVIDER="brevo",
    KOFAD_BREVO_API_KEY="test-key-only",
    KOFAD_BREVO_SECURITY_FROM_EMAIL="security@kofadimpex.com",
    KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
    KOFAD_EMAIL_CENTER_ENABLED=True,
)


class SupportContactsTests(TestCase):
    def test_public_customer_login_shows_configured_contact(self):
        CustomerServiceContact.objects.create(
            label="Main help desk", channel="whatsapp", number="+233241234567"
        )
        response = self.client.get("/market/account/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Main help desk")
        self.assertContains(response, "https://wa.me/233241234567")

    def test_new_support_contact_is_hidden_from_public_until_active(self):
        CustomerServiceContact.objects.create(
            label="Private number", channel="call", number="+233241234567", active=False
        )
        response = self.client.get("/market/account/login/")
        self.assertNotContains(response, "Private number")


@override_settings(**BREVO)
class VerifiedCustomerRecoveryTests(TestCase):
    def setUp(self):
        self.customer = CustomerAccount.objects.create(
            full_name="Email Customer", phone=None, email="buyer@example.org",
        )
        self.customer.set_password("OriginalPassword-123!")
        self.customer.save(update_fields=["password_hash"])
        self.identity = EmailIdentity.objects.create(
            kind="customer", owner_id=self.customer.pk, email="buyer@example.org",
            verified_at=timezone.now(),
        )

    @patch("marketplace.email_recovery._send_kofad_mail", return_value=1)
    def test_verified_email_recovery_changes_password_once(self, sender):
        challenge = email_recovery.issue("buyer@example.org", request=None)
        body = sender.call_args.args[1]
        code = re.search(r"\b[0-9]{6}\b", body).group()
        self.assertEqual(email_recovery.verify(challenge, code), challenge)
        customer = email_recovery.finish(challenge, "NewLongPassword-890!")
        self.assertTrue(customer.check_password("NewLongPassword-890!"))
        with self.assertRaises(ValidationError):
            email_recovery.finish(challenge, "AnotherLongPassword-890!")

    @patch("marketplace.email_recovery._send_kofad_mail", return_value=1)
    def test_wrong_email_code_consumes_attempt(self, sender):
        challenge = email_recovery.issue("buyer@example.org", request=None)
        good = re.search(r"\b[0-9]{6}\b", sender.call_args.args[1]).group()
        wrong = "999999" if good != "999999" else "000000"
        with self.assertRaises(ValidationError):
            email_recovery.verify(challenge, wrong)
        row = email_recovery.CustomerEmailRecovery.objects.get(pk=challenge)
        self.assertEqual(row.attempts, 1)
        self.assertIsNone(row.verified_at)

    def test_unknown_and_unverified_addresses_not_recovered(self):
        self.identity.verified_at = None
        self.identity.save(update_fields=["verified_at"])
        self.assertIsNone(email_recovery.resolve_verified("buyer@example.org"))
        self.assertIsNone(email_recovery.resolve_verified("not-a-customer@example.org"))


@override_settings(**BREVO)
class EmailCampaignConsentTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owneremails", "owner@example.org", "SecureOwnerPassword-987!")
        self.c1 = CustomerAccount.objects.create(full_name="Subscriber", email="yes@example.org")
        self.c2 = CustomerAccount.objects.create(full_name="Not subscriber", email="no@example.org")
        self.i1 = EmailIdentity.objects.create(
            kind="customer", owner_id=self.c1.pk, email="yes@example.org",
            verified_at=timezone.now(), marketing_emails_enabled=True,
        )
        EmailIdentity.objects.create(
            kind="customer", owner_id=self.c2.pk, email="no@example.org",
            verified_at=timezone.now(), notifications_enabled=True,
            marketing_emails_enabled=False,
        )
        self.campaign = EmailCampaign.objects.create(
            title="News", subject="KOFAD update",
            body="New arrivals are available.", created_by=self.owner, active=True,
        )

    def test_campaign_only_queues_explicit_opt_ins(self):
        self.assertEqual(queue_active_campaigns(limit=10), 1)
        letters = list(EmailLetter.objects.filter(source_key__startswith="campaign:"))
        self.assertEqual(len(letters), 1)
        self.assertEqual(letters[0].to_address, "yes@example.org")
        self.i1.marketing_emails_enabled = False
        self.i1.save(update_fields=["marketing_emails_enabled"])
        self.assertFalse(is_campaign_recipient_allowed(letters[0].source_key, letters[0].to_address))
        self.assertEqual(queue_active_campaigns(limit=10), 0)

    def test_nonadmin_cannot_access_campaigns(self):
        regular = User.objects.create_user("noaccess", password="A-good-password-098!")
        self.client.force_login(regular)
        access, _ = Access.objects.get_or_create(user=regular)
        sess = self.client.session
        sess["access_version"] = access.session_version
        sess.save()
        response = self.client.get("/email/campaigns/")
        self.assertEqual(response.status_code, 403)


@override_settings(**BREVO, KOFAD_BREVO_DAILY_LIMIT=1)
class EmailDailyCapTests(TestCase):
    @patch("core.brevo_email.requests.post")
    def test_unregistered_department_uses_verified_sender_and_reply_to(self, send):
        send.return_value = Mock(status_code=201)
        send_brevo(subject="Closing test", body="Safe test",
                   recipient="manager@example.org",
                   sender_email="reports@kofadimpex.com")
        payload = send.call_args.kwargs["json"]
        self.assertEqual(payload["sender"]["email"], "transactions@kofadimpex.com")
        self.assertEqual(payload["replyTo"]["email"], "reports@kofadimpex.com")

    @patch("core.brevo_email.requests.post")
    @override_settings(KOFAD_BREVO_REGISTERED_SENDERS="transactions@kofadimpex.com,support@kofadimpex.com")
    def test_registered_department_sends_from_own_address(self, send):
        send.return_value = Mock(status_code=201)
        send_brevo(subject="Support test", body="Safe test",
                   recipient="manager@example.org",
                   sender_email="support@kofadimpex.com")
        payload = send.call_args.kwargs["json"]
        self.assertEqual(payload["sender"]["email"], "support@kofadimpex.com")

    @patch("core.brevo_email.requests.post")
    def test_second_external_send_is_blocked_by_shared_cap(self, send):
        send.return_value = Mock(status_code=201)
        send_brevo(subject="Test", body="One", recipient="buyer@example.org")
        with self.assertRaises(DailyEmailLimitExceeded):
            send_brevo(subject="Test", body="Two", recipient="buyer@example.org")
        usage = usage_today()
        self.assertEqual(usage["attempted"], 1)
        self.assertEqual(usage["accepted"], 1)
        self.assertEqual(usage["remaining"], 0)


@override_settings(**BREVO)
class StaffEmailRecoveryTests(TestCase):
    @patch("core.email_identity._send_kofad_mail", return_value=1)
    def test_staff_can_request_verified_email_recovery(self, mocked):
        user = User.objects.create_user("cashier-recover", password="OldStaffPassword-123!")
        EmailIdentity.objects.create(
            kind="staff", owner_id=user.pk, email="staff@example.org",
            verified_at=timezone.now(),
        )
        result = self.client.post("/forgot-password/", {
            "username": "cashier-recover", "channel": "email",
        })
        self.assertEqual(result.status_code, 302)
        row = PasswordRecovery.objects.get(user=user)
        self.assertTrue(row.sent)
        self.assertEqual(row.channel, "email")
        self.assertEqual(row.email, "staff@example.org")
        mocked.assert_called_once()
