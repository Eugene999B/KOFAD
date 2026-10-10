"""Management address must be role-based without exposing or losing historical mail."""
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

from core.email_center import compose, visible_mailboxes
from core.email_models import (
    EmailConversation, EmailLetter, EmailMailbox, EmailMailboxMember,
)

OLD = "eugene@kofadimpex.com"
NEW = "management@kofadimpex.com"


@override_settings(KOFAD_EMAIL_CENTER_ENABLED=True)
class ManagementMailboxIdentityTests(TestCase):
    def setUp(self):
        self.management = EmailMailbox.objects.get(address=NEW)
        self.support = EmailMailbox.objects.get(address="support@kofadimpex.com")
        self.owner = User.objects.create_superuser(
            "management-test-owner", "owner@example.net", "SafePassword2345"
        )
        self.staff = User.objects.create_user("management-test-staff", password="SafePassword2345")
        EmailMailboxMember.objects.create(
            mailbox=self.management, user=self.staff, can_read=True, can_send=False
        )

    def test_role_based_management_address_and_grants(self):
        self.assertEqual(self.management.label, "Management")
        self.assertFalse(EmailMailbox.objects.filter(address=OLD).exists())
        self.assertIn(self.management, visible_mailboxes(self.staff))

    def test_legacy_internal_address_delivers_into_management_not_external_provider(self):
        with override_settings(KOFAD_EMAIL_ENABLED=False, KOFAD_EMAIL_PROVIDER="auto"):
            sent = compose(self.support, OLD, "Private management message",
                           "This stays inside KOFAD", self.owner)
        self.assertEqual(sent.status, "internal")
        received = EmailLetter.objects.get(
            mailbox=self.management, direction="inbound",
            subject="Private management message",
        )
        self.assertEqual(received.body_text, "This stays inside KOFAD")
        self.assertEqual(received.to_address, NEW)

    @override_settings(KOFAD_EMAIL_INGEST_SECRET="m" * 64)
    def test_historical_external_to_address_routes_to_management_with_original_to(self):
        mail = EmailMessage()
        mail["From"] = "Visitor <buyer@example.net>"
        mail["To"] = OLD
        mail["Subject"] = "Confidential request for management"
        mail["Message-ID"] = "<management-legacy-unique@customer.net>"
        mail.set_content("Please contact our office")
        raw = mail.as_bytes()
        ts = str(int(time.time()))
        material = (ts + "\n" + OLD + "\n" + hashlib.sha256(raw).hexdigest()).encode()
        sig = hmac.new(("m" * 64).encode(), material, hashlib.sha256).hexdigest()
        headers = {
            "HTTP_X_KOFAD_TIMESTAMP": ts,
            "HTTP_X_KOFAD_RECIPIENT": OLD,
            "HTTP_X_KOFAD_SIGNATURE": sig,
        }
        for _ in range(2):
            response = self.client.post(
                reverse("email_ingest"), data=raw, content_type="message/rfc822",
                **headers,
            )
            self.assertEqual(response.status_code, 200)
        messages = EmailLetter.objects.filter(
            mailbox=self.management, direction="inbound",
            subject="Confidential request for management",
        )
        self.assertEqual(messages.count(), 1)
        self.assertEqual(messages.get().to_address, OLD)

    def test_ordinary_staff_cannot_recreate_retired_personal_mailbox(self):
        from core.models import Access
        access, _ = Access.objects.get_or_create(user=self.owner)
        self.client.force_login(self.owner)
        session = self.client.session
        session["access_version"] = access.session_version
        session.save()
        response = self.client.post(reverse("email_team"), {
            "action": "new_mailbox", "address": OLD, "label": "Personal",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(EmailMailbox.objects.filter(address=OLD).exists())

    def test_migration_renames_in_place_keeping_links_and_idempotency(self):
        migration = importlib.import_module(
            "core.migrations.0042_management_email_identity"
        )
        # Simulate a historical database at the moment this migration arrives.
        pk = self.management.pk
        self.management.address = OLD
        self.management.label = "Management"
        self.management.save(update_fields=["address", "label"])
        conversation = EmailConversation.objects.create(
            mailbox=self.management, customer_email="client@example.net",
            subject="Before the name change",
        )
        letter = EmailLetter.objects.create(
            mailbox=self.management, direction="inbound", status="received",
            from_address="client@example.net", to_address=OLD,
            subject="Before the name change", body_text="Keep this message",
        )
        migration.promote_management_mailbox(apps, None)
        migration.promote_management_mailbox(apps, None)
        self.management.refresh_from_db()
        self.assertEqual(self.management.pk, pk)
        self.assertEqual(self.management.address, NEW)
        self.assertEqual(conversation.mailbox_id, pk)
        self.assertEqual(letter.mailbox_id, pk)
        self.assertTrue(EmailMailboxMember.objects.filter(
            user=self.staff, mailbox_id=pk
        ).exists())
        self.assertEqual(EmailMailbox.objects.filter(address=NEW).count(), 1)

    def test_migration_refuses_to_mix_two_confidential_mailboxes(self):
        migration = importlib.import_module(
            "core.migrations.0042_management_email_identity"
        )
        old = EmailMailbox.objects.create(address=OLD, label="Old management")
        with self.assertRaises(RuntimeError):
            migration.promote_management_mailbox(apps, None)
        self.assertTrue(EmailMailbox.objects.filter(pk=old.pk, address=OLD).exists())
        self.assertTrue(EmailMailbox.objects.filter(pk=self.management.pk, address=NEW).exists())
