"""Regression coverage for staff email productivity and branch isolation."""
from datetime import timedelta
from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.email_models import (
    EmailConversation, EmailConversationReadState, EmailLetter,
    EmailMailbox, EmailMailboxMember, EmailSavedReply, EmailStaffDraft,
)
from core.models import Access


@override_settings(
    KOFAD_EMAIL_CENTER_ENABLED=True, KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_PROVIDER="brevo", KOFAD_BREVO_API_KEY="test-only",
    KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
    KOFAD_BREVO_SECURITY_FROM_EMAIL="transactions@kofadimpex.com"
)
class ProfessionalEmailWorkflowTests(TestCase):
    def setUp(self):
        self.support = EmailMailbox.objects.get(address="support@kofadimpex.com")
        self.accounts = EmailMailbox.objects.get(address="accounts@kofadimpex.com")
        self.owner = User.objects.create_superuser(
            "email-work-owner", "owner@example.net", "Long-Safe-Password-123"
        )
        self.staff = User.objects.create_user(
            "email-work-staff", password="Long-Safe-Password-123"
        )
        self.other = User.objects.create_user(
            "email-work-other", password="Long-Safe-Password-123"
        )
        perm = Permission.objects.get(codename="send_messages", content_type__app_label="core")
        self.staff.user_permissions.add(perm)
        EmailMailboxMember.objects.create(user=self.staff, mailbox=self.support,
                                          can_read=True, can_send=True)
        EmailMailboxMember.objects.create(user=self.other, mailbox=self.accounts,
                                          can_read=True, can_send=False)
        self.thread = EmailConversation.objects.create(
            mailbox=self.support, customer_email="one@example.net", subject="Question",
            status="open", last_customer_at=timezone.now()
        )
        self.private = EmailConversation.objects.create(
            mailbox=self.accounts, customer_email="finance@example.net",
            subject="Private finances", status="open", last_customer_at=timezone.now()
        )

    def login(self, user):
        access, _ = Access.objects.get_or_create(user=user)
        self.client.force_login(user)
        session = self.client.session
        session["access_version"] = access.session_version
        session.save()

    def test_distinct_workflow_pages_render_without_cross_mailbox_leak(self):
        self.login(self.staff)
        for route in ("email_work", "email_drafts", "email_replies"):
            r = self.client.get(reverse(route))
            self.assertEqual(r.status_code, 200, route)
            self.assertNotContains(r, "Private finances")
        self.assertEqual(self.client.get(reverse("email_reports")).status_code, 403)
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("email_reports")).status_code, 200)

    def test_bulk_work_actions_cannot_cross_mailbox(self):
        self.login(self.staff)
        path = reverse("email_work")
        r = self.client.post(path, {"action": "close",
                                   "conversation_ids": [self.thread.pk, self.private.pk]})
        self.assertEqual(r.status_code, 403)
        self.thread.refresh_from_db()
        self.private.refresh_from_db()
        self.assertEqual(self.thread.status, "open")
        self.assertEqual(self.private.status, "open")
        self.client.post(path, {"action": "archive", "conversation_ids": [self.thread.pk]})
        self.thread.refresh_from_db()
        self.assertIsNotNone(self.thread.archived_at)
        self.client.post(path, {"action": "restore", "conversation_ids": [self.thread.pk]})
        self.thread.refresh_from_db()
        self.assertIsNone(self.thread.archived_at)

    def test_read_and_star_status_is_per_employee(self):
        self.login(self.owner)
        self.client.post(reverse("email_work"), {
            "action": "mark_read", "conversation_ids": [self.thread.pk]
        })
        entry = EmailConversationReadState.objects.get(
            conversation=self.thread, user=self.owner
        )
        self.assertIsNotNone(entry.last_read_at)
        self.assertFalse(EmailConversationReadState.objects.filter(
            conversation=self.thread, user=self.staff
        ).exists())
        self.client.post(reverse("email_work"), {
            "action": "star", "conversation_ids": [self.thread.pk]
        })
        entry.refresh_from_db()
        self.assertTrue(entry.starred)

    def test_private_draft_save_edit_and_queue_only_on_explicit_send(self):
        self.login(self.staff)
        payload = {
            "action": "save", "mailbox_id": self.support.pk,
            "recipient": "customer@example.net", "subject": "Follow up",
            "body": "Thank you for your note."
        }
        response = self.client.post(reverse("email_drafts"), payload)
        self.assertRedirects(response, reverse("email_drafts"))
        draft = EmailStaffDraft.objects.get(author=self.staff)
        self.assertFalse(EmailLetter.objects.filter(subject__icontains="Follow up").exists())
        self.login(self.other)
        response = self.client.get(reverse("email_drafts"), {"edit": draft.pk})
        self.assertEqual(response.status_code, 404)
        self.login(self.staff)
        send = {**payload, "action": "send", "draft_id": draft.pk}
        r = self.client.post(reverse("email_drafts"), send)
        self.assertRedirects(r, reverse("email_drafts"))
        self.assertFalse(EmailStaffDraft.objects.filter(pk=draft.pk).exists())
        self.assertTrue(EmailLetter.objects.filter(mailbox=self.support, direction="outbound",
                                                   status="queued", to_address="customer@example.net").exists())

    def test_scheduled_external_send_and_internal_rejection(self):
        self.login(self.staff)
        later = timezone.localtime(timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(reverse("email_drafts"), {
            "action": "send", "mailbox_id": self.support.pk,
            "recipient": "external@example.net", "subject": "Appointment reminder",
            "body": "Two days later", "scheduled_for": later
        })
        self.assertRedirects(response, reverse("email_drafts"))
        notice = EmailLetter.objects.get(subject__icontains="Appointment reminder")
        self.assertEqual(notice.status, "queued")
        self.assertGreater(notice.next_attempt_at, timezone.now() + timedelta(days=1))
        r = self.client.post(reverse("email_drafts"), {
            "action": "send", "mailbox_id": self.support.pk,
            "recipient": self.accounts.address, "subject": "Don't schedule internal",
            "body": "Should be rejected", "scheduled_for": later
        })
        self.assertRedirects(r, reverse("email_drafts"))
        self.assertFalse(EmailLetter.objects.filter(subject="Don't schedule internal").exists())

    def test_shared_reply_requires_owner_and_private_reply_isolation(self):
        self.login(self.staff)
        response = self.client.post(reverse("email_replies"), {
            "action": "create_reply", "title": "Delivery answer", "body": "Thank you",
            "shared": "yes"
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(EmailSavedReply.objects.filter(title="Delivery answer").exists())
        self.client.post(reverse("email_replies"), {
            "action": "create_reply", "title": "Private wording", "body": "My own response"
        })
        self.login(self.other)
        page = self.client.get(reverse("email_replies"))
        self.assertNotContains(page, "Private wording")

    def test_export_metadata_only_and_unauthorised_staff_blocked(self):
        self.login(self.staff)
        self.assertEqual(self.client.get(reverse("email_reports"), {"export": "csv"}).status_code, 403)
        self.login(self.owner)
        r = self.client.get(reverse("email_reports"), {"export": "csv"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("one@example.net", r.content.decode())
        self.assertNotIn("Email body", r.content.decode())
