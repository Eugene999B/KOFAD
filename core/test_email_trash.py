"""Safe department-scoped Email Trash: bulk actions, permission and delivery gates."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.email_center import deliver_outgoing
from core.email_models import (
    EmailConversation, EmailDeliveryEvent, EmailLetter, EmailMailbox, EmailMailboxMember,
)
from core.models import Access


@override_settings(
    KOFAD_EMAIL_CENTER_ENABLED=True,
    KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_PROVIDER="brevo",
    KOFAD_BREVO_API_KEY="NO_REAL_EMAIL_TEST",
    KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
    KOFAD_BREVO_SECURITY_FROM_EMAIL="transactions@kofadimpex.com",
)
class EmailTrashTests(TestCase):
    def setUp(self):
        self.support = EmailMailbox.objects.get(address="support@kofadimpex.com")
        self.accounts = EmailMailbox.objects.get(address="accounts@kofadimpex.com")
        self.owner = User.objects.create_superuser(
            "mail-trash-owner", "owner@example.net", "StrongPassword123"
        )
        self.staff = User.objects.create_user(
            "mail-trash-staff", password="StrongPassword123"
        )
        self.reader = User.objects.create_user(
            "mail-trash-reader", password="StrongPassword123"
        )
        self.outsider = User.objects.create_user(
            "mail-trash-outsider", password="StrongPassword123"
        )
        permission = Permission.objects.get(
            content_type__app_label="core", codename="send_messages"
        )
        self.staff.user_permissions.add(permission)
        for user, can_send in ((self.staff, True), (self.reader, False)):
            EmailMailboxMember.objects.create(
                mailbox=self.support, user=user, can_read=True, can_send=can_send,
            )

    def login(self, user):
        access, _ = Access.objects.get_or_create(user=user)
        self.client.force_login(user)
        session = self.client.session
        session["access_version"] = access.session_version
        session.save()

    def letter(self, mailbox=None, status="received", direction="inbound",
               subject="Customer enquiry", source_key=None, conversation=None):
        return EmailLetter.objects.create(
            mailbox=mailbox or self.support, direction=direction, status=status,
            from_address="client@example.net" if direction == "inbound" else self.support.address,
            to_address=self.support.address if direction == "inbound" else "client@example.net",
            subject=subject, body_text="Private customer letter",
            fingerprint=uuid4().hex if direction == "inbound" else "",
            source_key=source_key, conversation=conversation,
            next_attempt_at=timezone.now() - timedelta(minutes=1)
            if status == "queued" else None,
        )

    def post(self, action, ids=(), mailbox=None, **data):
        return self.client.post(reverse("email_trash_action"), {
            "action": action,
            "mailbox_id": (mailbox or self.support).pk,
            "letter_ids": [str(pk) for pk in ids],
            **data,
        })

    def test_move_selected_to_trash_hides_it_and_restore_returns_it(self):
        item = self.letter()
        self.login(self.staff)
        response = self.post("trash_selected", [item.pk])
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertIsNotNone(item.trashed_at)
        self.assertEqual(item.trashed_by, self.staff)
        self.assertNotContains(self.client.get(
            reverse("email_history"), {"mailbox": self.support.pk}
        ), "Customer enquiry")
        self.assertContains(self.client.get(
            reverse("email_trash"), {"mailbox": self.support.pk}
        ), "Customer enquiry")
        response = self.post("restore_selected", [item.pk])
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertIsNone(item.trashed_at)
        self.assertContains(self.client.get(
            reverse("email_history"), {"mailbox": self.support.pk}
        ), "Customer enquiry")

    @patch("core.brevo_email.requests.post")
    def test_queued_mail_deleted_from_filtered_view_never_sends(self, post):
        pending = self.letter(status="queued", direction="outbound", subject="Queued invoice")
        self.login(self.owner)
        response = self.post(
            "trash_all", direction="outbound", status="queued",
            confirmation="DELETE ALL",
        )
        self.assertEqual(response.status_code, 302)
        pending.refresh_from_db()
        self.assertIsNotNone(pending.trashed_at)
        self.assertEqual(pending.status, "suppressed")
        self.assertEqual(deliver_outgoing(limit=20), 0)
        post.assert_not_called()
        self.post("restore_selected", [pending.pk])
        pending.refresh_from_db()
        self.assertIsNone(pending.trashed_at)
        self.assertEqual(pending.status, "suppressed")

    def test_delete_all_applies_across_all_pages_but_only_current_mailbox(self):
        for number in range(32):
            self.letter(subject=f"Duplicate #{number}")
        other = self.letter(mailbox=self.accounts, subject="Confidential finance")
        not_matching = self.letter(subject="Keep this special customer")
        self.login(self.owner)
        first = self.client.get(reverse("email_history"), {"mailbox": self.support.pk})
        self.assertEqual(len(first.context["letters"]), 20)
        result = self.post("trash_all", confirmation="DELETE ALL", q="Duplicate")
        self.assertEqual(result.status_code, 302)
        self.assertEqual(EmailLetter.objects.filter(
            mailbox=self.support, trashed_at__isnull=False
        ).count(), 32)
        not_matching.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNone(not_matching.trashed_at)
        self.assertIsNone(other.trashed_at)

    def test_failed_confirmation_moves_nothing(self):
        item = self.letter()
        self.login(self.owner)
        self.post("trash_all", confirmation="DELETE", q="Customer")
        item.refresh_from_db()
        self.assertIsNone(item.trashed_at)

    def test_cross_mailbox_and_forged_ids_never_move_unrelated_mail(self):
        item = self.letter()
        private = self.letter(mailbox=self.accounts, subject="Account secret")
        self.login(self.staff)
        self.assertEqual(self.post("trash_selected", [private.pk]).status_code, 403)
        self.assertEqual(self.post("trash_selected", [item.pk, private.pk]).status_code, 403)
        self.assertEqual(self.post("trash_selected", ["bad"]).status_code, 302)
        private.refresh_from_db()
        item.refresh_from_db()
        self.assertIsNone(private.trashed_at)
        self.assertIsNone(item.trashed_at)

    def test_staff_cannot_cancel_automated_financial_mail(self):
        automated = self.letter(
            status="queued", direction="outbound",
            subject="System-issued transaction receipt",
            source_key="receipt:system:2026",
        )
        self.login(self.staff)
        self.assertEqual(self.post("trash_selected", [automated.pk]).status_code, 403)
        automated.refresh_from_db()
        self.assertEqual(automated.status, "queued")
        self.assertIsNone(automated.trashed_at)

    def test_read_only_cannot_delete_owner_only_actions_blocked_for_staff(self):
        item = self.letter()
        self.login(self.reader)
        self.assertEqual(self.post("trash_selected", [item.pk]).status_code, 403)
        self.login(self.staff)
        self.assertEqual(self.post("trash_all", confirmation="DELETE ALL").status_code, 403)
        self.assertEqual(self.post("empty_trash", confirmation="EMPTY TRASH").status_code, 403)
        item.refresh_from_db()
        self.assertIsNone(item.trashed_at)

    def test_empty_trash_purges_ordinary_email_but_keeps_audit_records(self):
        ordinary = self.letter(subject="Old ordinary")
        automatic = self.letter(
            subject="Accounting evidence", status="submitted",
            direction="outbound", source_key="financial:proof:1",
        )
        tracked = self.letter(
            subject="Provider delivery evidence", status="submitted",
            direction="outbound",
        )
        EmailDeliveryEvent.objects.create(
            letter=tracked, fingerprint="a" * 64,
            provider_message_id="brevo-proof",
            recipient="client@example.net", event="delivered",
            event_at=timezone.now(),
        )
        self.login(self.owner)
        self.post("trash_all", confirmation="DELETE ALL")
        result = self.post("empty_trash", confirmation="EMPTY TRASH")
        self.assertEqual(result.status_code, 302)
        self.assertFalse(EmailLetter.objects.filter(pk=ordinary.pk).exists())
        for protected in (automatic, tracked):
            protected.refresh_from_db()
            self.assertIsNotNone(protected.trashed_at)
        self.assertEqual(EmailLetter.objects.filter(
            mailbox=self.support, trashed_at__isnull=False
        ).count(), 2)

    def test_permanent_selected_purge_requires_typed_confirmation(self):
        item = self.letter()
        self.login(self.owner)
        self.post("trash_selected", [item.pk])
        self.post("purge_selected", [item.pk], confirmation="YES")
        self.assertTrue(EmailLetter.objects.filter(pk=item.pk).exists())
        self.post("purge_selected", [item.pk], confirmation="DELETE PERMANENTLY")
        self.assertFalse(EmailLetter.objects.filter(pk=item.pk).exists())

    def test_trash_cannot_mutate_messages_currently_sending(self):
        item = self.letter(status="sending", direction="outbound")
        self.login(self.owner)
        self.post("trash_selected", [item.pk])
        item.refresh_from_db()
        self.assertIsNone(item.trashed_at)

    def test_trashed_conversation_not_shown_in_inbox_or_work_until_restored(self):
        thread = EmailConversation.objects.create(
            mailbox=self.support, customer_email="client@example.net",
            subject="Hide deleted thread",
        )
        item = self.letter(conversation=thread, subject="Hide deleted thread")
        self.login(self.staff)
        self.post("trash_selected", [item.pk])
        home = self.client.get(
            reverse("email_center"), {"mailbox": self.support.pk}
        )
        work = self.client.get(reverse("email_work"))
        self.assertNotContains(home, "Hide deleted thread")
        self.assertNotContains(work, "Hide deleted thread")
        self.post("restore_selected", [item.pk])
        work = self.client.get(reverse("email_work"))
        self.assertContains(work, "Hide deleted thread")

    def test_trash_forms_use_post_and_cross_site_protection(self):
        self.login(self.owner)
        page = self.client.get(reverse("email_history"), {"mailbox": self.support.pk})
        self.assertContains(page, "Move all matching to Trash")
        self.assertEqual(self.client.get(reverse("email_trash_action")).status_code, 405)
