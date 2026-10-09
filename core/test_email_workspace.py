"""Focused Email Centre workspace, owner-level bulk grants, and delivery safety."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.email_center import deliver_outgoing
from core.email_models import EmailConversation, EmailLetter, EmailMailbox, EmailMailboxMember
from core.models import Access


SETTINGS = {
    "KOFAD_EMAIL_CENTER_ENABLED": True,
    "KOFAD_EMAIL_ENABLED": True,
    "KOFAD_EMAIL_PROVIDER": "brevo",
    "KOFAD_BREVO_API_KEY": "TEST-ONLY-NOT-LIVE",
    "KOFAD_BREVO_TRANSACTION_FROM_EMAIL": "transactions@kofadimpex.com",
    "KOFAD_BREVO_SECURITY_FROM_EMAIL": "transactions@kofadimpex.com",
}


@override_settings(**SETTINGS)
class EmailWorkspaceTests(TestCase):
    def setUp(self):
        self.support = EmailMailbox.objects.get(address="support@kofadimpex.com")
        self.accounts = EmailMailbox.objects.get(address="accounts@kofadimpex.com")
        self.admin = User.objects.create_superuser(
            "mail-owner-v2", "owner@example.org", "SafePassword.1234"
        )
        self.staff = User.objects.create_user(
            "mail-staff-v2", password="SafePassword.1234"
        )
        self.read_only = User.objects.create_user(
            "mail-reader-v2", password="SafePassword.1234"
        )
        EmailMailboxMember.objects.create(
            user=self.read_only, mailbox=self.support, can_read=True, can_send=False
        )
        sending_perm = Permission.objects.get(
            codename="send_messages", content_type__app_label="core"
        )
        self.staff.user_permissions.add(sending_perm)

    def login(self, user):
        access, _ = Access.objects.get_or_create(user=user)
        self.client.force_login(user)
        session = self.client.session
        session["access_version"] = access.session_version
        session.save()

    def post_bulk(self, *, user=None, read=None, send=None, confirm="yes"):
        return self.client.post(reverse("email_team"), {
            "action": "bulk_assign",
            "user_id": (user or self.staff).pk,
            "read_mailboxes": read or [],
            "send_mailboxes": send or [],
            "confirm_replace": confirm,
        })

    def test_workspace_sections_have_dedicated_pages(self):
        self.login(self.admin)
        urls = [
            ("email_center", "Shared inbox"),
            ("email_history", "Mail history"),
            ("email_automations", "Automations & delivery"),
            ("email_team", "Staff mailbox access"),
        ]
        for name, heading in urls:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, heading)
        home = self.client.get(reverse("email_center"))
        self.assertNotContains(home, "Current access overview")
        history = self.client.get(reverse("email_history"))
        self.assertNotContains(history, "Assign mailboxes")

    def test_one_staff_receives_multiple_mailboxes_in_one_save(self):
        self.login(self.admin)
        response = self.post_bulk(
            read=[str(self.support.pk), str(self.accounts.pk)],
            send=[str(self.support.pk)]
        )
        self.assertRedirects(response, reverse("email_team"))
        grants = {g.mailbox_id: g for g in EmailMailboxMember.objects.filter(user=self.staff)}
        self.assertEqual(set(grants), {self.support.pk, self.accounts.pk})
        self.assertTrue(grants[self.support.pk].can_read)
        self.assertTrue(grants[self.support.pk].can_send)
        self.assertTrue(grants[self.accounts.pk].can_read)
        self.assertFalse(grants[self.accounts.pk].can_send)

        team = self.client.get(reverse("email_team"), {"staff": self.staff.pk})
        self.assertEqual(team.status_code, 200)
        self.assertContains(team, "Save all mailbox permissions")

        self.login(self.staff)
        self.assertEqual(set(self.staff.kofad_mailbox_memberships.values_list("mailbox_id", flat=True)),
                         {self.support.pk, self.accounts.pk})
        self.assertEqual(self.client.get(reverse("email_center")).status_code, 200)

    def test_reply_checkbox_includes_read_and_full_replacement(self):
        self.login(self.admin)
        self.post_bulk(send=[str(self.support.pk)])
        grant = EmailMailboxMember.objects.get(user=self.staff, mailbox=self.support)
        self.assertTrue(grant.can_send)
        self.assertTrue(grant.can_read)
        self.post_bulk(read=[str(self.accounts.pk)])
        self.assertFalse(EmailMailboxMember.objects.filter(user=self.staff, mailbox=self.support).exists())
        self.assertTrue(EmailMailboxMember.objects.filter(
            user=self.staff, mailbox=self.accounts, can_read=True, can_send=False
        ).exists())
        self.post_bulk()
        self.assertFalse(EmailMailboxMember.objects.filter(user=self.staff).exists())

    def test_invalid_selection_does_not_erase_existing_access(self):
        self.login(self.admin)
        self.post_bulk(read=[str(self.support.pk)])
        self.post_bulk(read=["999999999"])
        self.assertEqual(
            list(EmailMailboxMember.objects.filter(user=self.staff).values_list("mailbox_id", flat=True)),
            [self.support.pk],
        )
        self.post_bulk(read=["not-an-id"])
        self.assertTrue(EmailMailboxMember.objects.filter(user=self.staff, mailbox=self.support).exists())

    def test_no_send_grant_without_staff_role_permission(self):
        self.login(self.admin)
        self.post_bulk(send=[str(self.accounts.pk)], user=self.read_only)
        grant = EmailMailboxMember.objects.get(user=self.read_only, mailbox=self.support)
        self.assertFalse(grant.can_send)
        self.assertFalse(EmailMailboxMember.objects.filter(
            user=self.read_only, mailbox=self.accounts
        ).exists())

    def test_non_owner_cannot_view_or_change_staff_permissions(self):
        self.login(self.read_only)
        for name in ("email_team", "email_automations"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.assertEqual(self.post_bulk(read=[str(self.accounts.pk)]).status_code, 403)
        self.assertFalse(EmailMailboxMember.objects.filter(
            user=self.staff, mailbox=self.accounts
        ).exists())

    def test_filtered_conversations_remain_scoped_to_visible_mailbox(self):
        mine = EmailConversation.objects.create(
            mailbox=self.support, customer_email="vip@example.org", subject="Need delivery help",
            status="open", assigned_to=self.admin
        )
        EmailConversation.objects.create(
            mailbox=self.accounts, customer_email="vip@example.org",
            subject="Private financial question", status="open"
        )
        self.login(self.admin)
        response = self.client.get(reverse("email_center"), {
            "mailbox": self.support.pk, "thread_q": "vip",
            "thread_status": "open", "thread_owner": "mine",
        })
        self.assertEqual([x.pk for x in response.context["conversations"]], [mine.pk])
        self.assertNotContains(response, "Private financial question")

    def test_attention_view_includes_failed_and_uncertain_not_other_mailbox(self):
        for mb, subject, status in (
            (self.support, "Delivery failure", "failed"),
            (self.support, "Possibly delivered", "uncertain"),
            (self.support, "Already submitted", "submitted"),
            (self.accounts, "Private account failure", "failed"),
        ):
            EmailLetter.objects.create(
                mailbox=mb, direction="outbound", status=status,
                from_address=mb.address, to_address="test@example.org",
                subject=subject, body_text="No customer data."
            )
        self.login(self.admin)
        response = self.client.get(reverse("email_history"), {
            "mailbox": self.support.pk, "status": "attention"
        })
        self.assertEqual(response.context["letters"].paginator.count, 2)
        self.assertContains(response, "Delivery failure")
        self.assertContains(response, "Possibly delivered")
        self.assertNotContains(response, "Private account failure")

    @patch("core.brevo_email.requests.post")
    def test_interrupted_sending_requires_manual_review_not_duplicate(self, post):
        letter = EmailLetter.objects.create(
            mailbox=self.support, direction="outbound", status="sending",
            from_address=self.support.address, to_address="safe@example.org",
            subject="Test message", body_text="No real delivery.", attempts=1,
            next_attempt_at=timezone.now() - timedelta(minutes=24)
        )
        self.assertEqual(deliver_outgoing(limit=3), 0)
        letter.refresh_from_db()
        self.assertEqual(letter.status, "uncertain")
        self.assertIn("review", letter.last_error)
        post.assert_not_called()
