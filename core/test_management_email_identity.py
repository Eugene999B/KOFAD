"""Retired Management is not a business mailbox; historical records remain safe."""
import hashlib
import hmac
import importlib
import time
from email.message import EmailMessage

from django.apps import apps
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.email_center import compose, visible_mailboxes
from core.email_models import (
    EmailConversation, EmailLetter, EmailMailbox, EmailMailboxMember,
    EmailStaffDraft, EmailSavedReply,
)
from core.models import Access


OLD = "eugene@kofadimpex.com"
NEW = "management@kofadimpex.com"


@override_settings(KOFAD_EMAIL_CENTER_ENABLED=True)
class ManagementMailboxRetirementTests(TestCase):
    def setUp(self):
        self.support = EmailMailbox.objects.get(address="support@kofadimpex.com")
        self.owner = User.objects.create_superuser(
            "retire-test-owner", "owner@example.net", "SafePassword2345"
        )
        self.staff = User.objects.create_user(
            "retire-test-staff", password="SafePassword2345"
        )

    def _sign(self, recipient):
        mail = EmailMessage()
        mail["From"] = "Visitor <buyer@example.net>"
        mail["To"] = recipient
        mail["Subject"] = "Former management"
        mail["Message-ID"] = "<retired-management@customer.net>"
        mail.set_content("Do not create a management inbox")
        raw = mail.as_bytes()
        stamp = str(int(time.time()))
        material = (stamp + "\n" + recipient + "\n"
                    + hashlib.sha256(raw).hexdigest()).encode()
        signature = hmac.new(("a" * 64).encode(), material, hashlib.sha256).hexdigest()
        return raw, {
            "HTTP_X_KOFAD_TIMESTAMP": stamp,
            "HTTP_X_KOFAD_RECIPIENT": recipient,
            "HTTP_X_KOFAD_SIGNATURE": signature,
        }

    def test_neither_management_address_exists_as_an_active_default_mailbox(self):
        active_addresses = set(EmailMailbox.objects.filter(active=True).values_list(
            "address", flat=True
        ))
        self.assertNotIn(OLD, active_addresses)
        self.assertNotIn(NEW, active_addresses)
        self.assertFalse(visible_mailboxes(self.owner).filter(address__in=[OLD, NEW]).exists())

    @override_settings(KOFAD_EMAIL_INGEST_SECRET="a" * 64)
    def test_signed_external_mail_for_both_retired_addresses_is_rejected(self):
        for retired in (OLD, NEW):
            with self.subTest(address=retired):
                raw, headers = self._sign(retired)
                response = self.client.post(
                    reverse("email_ingest"), data=raw,
                    content_type="message/rfc822", **headers,
                )
                self.assertEqual(response.status_code, 404)
        self.assertFalse(EmailLetter.objects.filter(subject="Former management").exists())

    def test_internal_and_external_attempts_to_use_retired_recipient_fail_closed(self):
        with override_settings(KOFAD_EMAIL_ENABLED=False, KOFAD_EMAIL_PROVIDER="auto"):
            for address in (OLD, NEW):
                with self.subTest(address=address):
                    with self.assertRaises(ValidationError):
                        compose(self.support, address, "Subject", "Message", self.owner)
            with self.assertRaises(ValidationError):
                compose(
                    self.support, "customer@example.net", "Subject", "Message",
                    self.owner, bcc=NEW,
                )
        self.assertFalse(EmailLetter.objects.filter(direction="outbound").exists())

    def test_staff_cannot_create_either_retired_address_even_as_owner(self):
        access, _ = Access.objects.get_or_create(user=self.owner)
        self.client.force_login(self.owner)
        session = self.client.session
        session["access_version"] = access.session_version
        session.save()
        for retired in (OLD, NEW):
            with self.subTest(address=retired):
                response = self.client.post(reverse("email_team"), {
                    "action": "new_mailbox", "address": retired, "label": "Management",
                })
                self.assertEqual(response.status_code, 302)
                self.assertFalse(EmailMailbox.objects.filter(address=retired).exists())

    def test_unused_mailbox_is_deleted_and_staff_access_is_removed(self):
        mgmt = EmailMailbox.objects.create(address=NEW, label="Management")
        EmailMailboxMember.objects.create(
            mailbox=mgmt, user=self.staff, can_read=True, can_send=True
        )
        migration = importlib.import_module("core.migrations.0043_retire_management_email")
        migration.retire_management(apps, None)
        migration.retire_management(apps, None)
        self.assertFalse(EmailMailbox.objects.filter(pk=mgmt.pk).exists())
        self.assertFalse(EmailMailboxMember.objects.filter(user=self.staff).exists())

    def test_historical_mail_and_private_draft_remain_retained_but_inaccessible(self):
        mgmt = EmailMailbox.objects.create(address=NEW, label="Management")
        EmailMailboxMember.objects.create(
            mailbox=mgmt, user=self.staff, can_read=True, can_send=True
        )
        thread = EmailConversation.objects.create(
            mailbox=mgmt, customer_email="customer@example.net", subject="Old history"
        )
        received = EmailLetter.objects.create(
            mailbox=mgmt, conversation=thread, direction="inbound",
            status="received", from_address="customer@example.net",
            to_address=NEW, subject="Old history", body_text="Preserve business audit"
        )
        pending = EmailLetter.objects.create(
            mailbox=mgmt, direction="outbound", status="queued",
            from_address=NEW, to_address="customer@example.net",
            subject="Never send", body_text="Old unsent text",
            next_attempt_at=timezone.now(),
        )
        draft = EmailStaffDraft.objects.create(
            mailbox=mgmt, author=self.staff, recipient="customer@example.net",
            subject="Private draft", body="Do not destroy private drafts"
        )
        saved_reply = EmailSavedReply.objects.create(
            mailbox=mgmt, author=self.owner, title="Old management snippet",
            body="Do not destroy saved staff content"
        )
        migration = importlib.import_module("core.migrations.0043_retire_management_email")
        migration.retire_management(apps, None)
        mgmt.refresh_from_db()
        pending.refresh_from_db()
        self.assertFalse(mgmt.active)
        self.assertEqual(pending.status, "suppressed")
        self.assertEqual(EmailLetter.objects.get(pk=received.pk).body_text, "Preserve business audit")
        self.assertTrue(EmailConversation.objects.filter(pk=thread.pk).exists())
        self.assertTrue(EmailStaffDraft.objects.filter(pk=draft.pk).exists())
        self.assertTrue(EmailSavedReply.objects.filter(pk=saved_reply.pk).exists())
        self.assertFalse(visible_mailboxes(self.owner).filter(pk=mgmt.pk).exists())
        self.assertFalse(visible_mailboxes(self.staff).filter(pk=mgmt.pk).exists())
        self.assertFalse(EmailMailboxMember.objects.filter(mailbox=mgmt).exists())
        with self.assertRaises(ValidationError):
            compose(mgmt, "customer@example.net", "Subject", "Body", self.owner)
        migration.retire_management(apps, None)
        mgmt.refresh_from_db()
        self.assertFalse(mgmt.active)

    @override_settings(
        KOFAD_EMAIL_ENABLED=True, KOFAD_EMAIL_PROVIDER="brevo",
        KOFAD_BREVO_API_KEY="FAKE-CI-ONLY",
        KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
        KOFAD_BREVO_SECURITY_FROM_EMAIL="transactions@kofadimpex.com",
        KOFAD_BREVO_REGISTERED_SENDERS="transactions@kofadimpex.com",
    )
    def test_disabled_mailbox_queue_cannot_submit_to_provider(self):
        from unittest.mock import patch
        from core.email_center import deliver_outgoing
        mgmt = EmailMailbox.objects.create(
            address=NEW, label="Management", active=False
        )
        letter = EmailLetter.objects.create(
            mailbox=mgmt, direction="outbound", status="queued",
            from_address=NEW, to_address="client@example.net",
            subject="Do not send", body_text="Company retired this address",
            next_attempt_at=timezone.now(),
        )
        with patch("core.brevo_email.requests.post") as post:
            self.assertEqual(deliver_outgoing(limit=10), 0)
            post.assert_not_called()
        letter.refresh_from_db()
        self.assertEqual(letter.status, "suppressed")

    @override_settings(
        KOFAD_EMAIL_ENABLED=True, KOFAD_EMAIL_PROVIDER="brevo",
        KOFAD_BREVO_API_KEY="FAKE-CI-ONLY",
        KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
        KOFAD_BREVO_SECURITY_FROM_EMAIL="transactions@kofadimpex.com",
        KOFAD_BREVO_REGISTERED_SENDERS="transactions@kofadimpex.com",
    )
    def test_direct_provider_rejects_retired_sender_and_recipient(self):
        from unittest.mock import patch
        from core.brevo_email import send_brevo
        with patch("core.brevo_email.requests.post") as post:
            with self.assertRaises(ValidationError):
                send_brevo(
                    subject="Test", body="Body", recipient="client@example.net",
                    sender_email=NEW,
                )
            with self.assertRaises(ValidationError):
                send_brevo(
                    subject="Test", body="Body", recipient=OLD,
                )
            post.assert_not_called()

    def test_old_name_is_also_deleted_if_present_after_renaming(self):
        original = EmailMailbox.objects.create(address=OLD, label="Former management")
        rename = importlib.import_module("core.migrations.0042_management_email_identity")
        retire = importlib.import_module("core.migrations.0043_retire_management_email")
        rename.promote_management_mailbox(apps, None)
        original.refresh_from_db()
        self.assertEqual(original.address, NEW)
        retire.retire_management(apps, None)
        self.assertFalse(EmailMailbox.objects.filter(pk=original.pk).exists())
