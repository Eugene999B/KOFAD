import hashlib
import hmac
import time
from email.message import EmailMessage

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.email_center import compose, visible_mailboxes
from core.email_models import EmailLetter, EmailMailbox, EmailMailboxMember


@override_settings(KOFAD_EMAIL_CENTER_ENABLED=True)
class KofadEmailCentreTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("mailowner", "owner@example.com", "a-strong-pass-12345")
        self.reader = User.objects.create_user("mailreader", password="a-strong-pass-12345")
        self.stranger = User.objects.create_user("nostaffmail", password="a-strong-pass-12345")
        self.support = EmailMailbox.objects.get(address="support@kofadimpex.com")
        self.accounts = EmailMailbox.objects.get(address="accounts@kofadimpex.com")
        EmailMailboxMember.objects.create(mailbox=self.support, user=self.reader, can_read=True)
        self.letter = EmailLetter.objects.create(
            mailbox=self.support, direction="inbound", status="received",
            from_address="client@example.com", to_address=self.support.address,
            subject="Private enquiry", body_text="Do not expose to other staff.",
            fingerprint="a" * 64,
        )

    def test_reader_sees_only_assigned_mailbox(self):
        self.assertEqual(list(visible_mailboxes(self.reader)), [self.support])
        self.assertFalse(visible_mailboxes(self.stranger).exists())
        self.client.force_login(self.reader)
        response = self.client.get(reverse("email_center"), {"mailbox": self.accounts.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Private enquiry")
        self.assertNotContains(response, "accounts@kofadimpex.com")

    def test_staff_cannot_grant_themselves_an_inbox(self):
        self.client.force_login(self.reader)
        response = self.client.post(reverse("email_center"), {
            "action": "assign", "mailbox_id": self.accounts.pk,
            "user_id": self.reader.pk, "can_read": "on"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(EmailMailboxMember.objects.filter(user=self.reader, mailbox=self.accounts).exists())

    def test_internal_mail_is_delivered_without_paid_provider(self):
        with override_settings(KOFAD_EMAIL_ENABLED=False, KOFAD_EMAIL_PROVIDER="auto"):
            letter = compose(self.support, self.accounts.address, "Internal note",
                             "Hello finance", self.owner)
        self.assertEqual(letter.status, "internal")
        received = EmailLetter.objects.get(mailbox=self.accounts, direction="inbound")
        self.assertEqual(received.body_text, "Hello finance")

    def test_external_mail_requires_sender_credentials(self):
        with override_settings(KOFAD_EMAIL_ENABLED=False, KOFAD_EMAIL_PROVIDER="auto"):
            with self.assertRaises(ValidationError):
                compose(self.support, "someone@example.com", "Subject", "Body", self.owner)
        self.assertFalse(EmailLetter.objects.filter(direction="outbound").exists())

    def _signed_message(self, secret):
        mail = EmailMessage()
        mail["From"] = "Customer <buyer@example.net>"
        mail["To"] = self.support.address
        mail["Subject"] = "Cloudflare test"
        mail["Message-ID"] = "<unique-123@sender.net>"
        mail.set_content("Hello KOFAD team")
        raw = mail.as_bytes()
        stamp = str(int(time.time()))
        material = (stamp + "\n" + self.support.address + "\n"
                    + hashlib.sha256(raw).hexdigest()).encode()
        signature = hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()
        return raw, {"HTTP_X_KOFAD_TIMESTAMP": stamp,
                     "HTTP_X_KOFAD_RECIPIENT": self.support.address,
                     "HTTP_X_KOFAD_SIGNATURE": signature}

    @override_settings(KOFAD_EMAIL_INGEST_SECRET="a" * 64)
    def test_authenticated_worker_delivery_is_idempotent(self):
        raw, headers = self._signed_message("a" * 64)
        url = reverse("email_ingest")
        for _ in range(2):
            r = self.client.post(url, data=raw, content_type="message/rfc822", **headers)
            self.assertEqual(r.status_code, 200)
        self.assertEqual(EmailLetter.objects.filter(
            mailbox=self.support, subject="Cloudflare test").count(), 1)

    @override_settings(KOFAD_EMAIL_INGEST_SECRET="a" * 64)
    def test_forged_worker_delivery_is_rejected(self):
        raw, headers = self._signed_message("a" * 64)
        headers["HTTP_X_KOFAD_SIGNATURE"] = "0" * 64
        r = self.client.post(reverse("email_ingest"),
                             data=raw, content_type="message/rfc822", **headers)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(EmailLetter.objects.filter(subject="Cloudflare test").exists())

    @override_settings(KOFAD_EMAIL_INGEST_SECRET="a" * 64)
    def test_no_mail_delivery_for_unknown_alias(self):
        raw, headers = self._signed_message("a" * 64)
        headers["HTTP_X_KOFAD_RECIPIENT"] = "unknown@kofadimpex.com"
        stamp = headers["HTTP_X_KOFAD_TIMESTAMP"]
        material = (stamp + "\n" + "unknown@kofadimpex.com" + "\n"
                    + hashlib.sha256(raw).hexdigest()).encode()
        headers["HTTP_X_KOFAD_SIGNATURE"] = hmac.new(
            ("a" * 64).encode(), material, hashlib.sha256).hexdigest()
        r = self.client.post(reverse("email_ingest"), data=raw,
                             content_type="message/rfc822", **headers)
        self.assertEqual(r.status_code, 404)
