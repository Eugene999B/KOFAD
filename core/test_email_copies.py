"""Business mail recipient privacy, CC/BCC delivery, and staff-reviewed forwarding."""
from unittest.mock import Mock, patch

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.email_center import compose, deliver_outgoing
from core.email_models import EmailLetter, EmailMailbox, EmailMailboxMember
from core.models import Access


@override_settings(
    KOFAD_EMAIL_CENTER_ENABLED=True, KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_PROVIDER="brevo", KOFAD_BREVO_API_KEY="TEST-NO-REAL-MAIL",
    KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
    KOFAD_BREVO_SECURITY_FROM_EMAIL="transactions@kofadimpex.com",
    KOFAD_BREVO_REGISTERED_SENDERS="transactions@kofadimpex.com",
)
class EmailCopiesAndForwardTests(TestCase):
    def setUp(self):
        self.support = EmailMailbox.objects.get(address="support@kofadimpex.com")
        self.accounts = EmailMailbox.objects.get(address="accounts@kofadimpex.com")
        self.admin = User.objects.create_superuser(
            "mail-copy-owner", "owner@example.net", "SafePassword1234")
        self.staff = User.objects.create_user(
            "mail-copy-staff", password="SafePassword1234")
        self.other = User.objects.create_user(
            "mail-copy-outsider", password="SafePassword1234")
        permission = Permission.objects.get(
            codename="send_messages", content_type__app_label="core")
        self.staff.user_permissions.add(permission)
        EmailMailboxMember.objects.create(
            mailbox=self.support, user=self.staff, can_read=True, can_send=True
        )
        EmailMailboxMember.objects.create(
            mailbox=self.support, user=self.other, can_read=True, can_send=False
        )

    def login(self, user):
        access, _ = Access.objects.get_or_create(user=user)
        self.client.force_login(user)
        session = self.client.session
        session["access_version"] = access.session_version
        session.save()

    @patch("core.brevo_email.requests.post")
    def test_cc_bcc_are_explicit_and_brevo_payload_hides_bcc_in_to(self, post):
        post.return_value = Mock(status_code=201)
        post.return_value.json.return_value = {"messageId": "<provider-one@test>"}
        queued = compose(
            self.support, "customer@example.org", "Order status", "On the way.",
            self.admin, cc="manager@example.org; ops@example.org",
            bcc="audit@example.org"
        )
        self.assertEqual(queued.cc_addresses, "manager@example.org,ops@example.org")
        self.assertEqual(queued.bcc_addresses, "audit@example.org")
        self.assertEqual(deliver_outgoing(limit=1), 1)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["to"], [{"email": "customer@example.org"}])
        self.assertEqual(body["cc"], [
            {"email": "manager@example.org"}, {"email": "ops@example.org"}
        ])
        self.assertEqual(body["bcc"], [{"email": "audit@example.org"}])
        self.assertNotIn("audit@example.org", str(body["to"]))
        queued.refresh_from_db()
        self.assertEqual(queued.status, "submitted")

    def test_invalid_duplicate_or_injected_copies_never_enqueue(self):
        bad = [
            ("customer@example.org", ""),
            ("a@example.org, a@example.org", ""),
            ("malicious@example.org\r\nBcc:foo@example.org", ""),
            ("Person <someone@example.org>", ""),
            ("", "customer@example.org"),
            ("one@example.org", "one@example.org"),
            (",".join(f"member{i}@example.org" for i in range(6)), ""),
        ]
        for cc, bcc in bad:
            with self.subTest(cc=cc, bcc=bcc):
                with self.assertRaises(ValidationError):
                    compose(self.support, "customer@example.org", "Subject", "Body",
                            self.admin, cc=cc, bcc=bcc)
        self.assertFalse(EmailLetter.objects.filter(direction="outbound").exists())

    def test_internal_mail_with_external_copies_is_rejected_not_dropped(self):
        with self.assertRaises(ValidationError):
            compose(self.support, self.accounts.address, "Private", "Details",
                    self.admin, cc="outside@example.net")
        self.assertEqual(EmailLetter.objects.count(), 0)

    def test_forward_source_requires_reply_permission_in_same_mailbox(self):
        original = EmailLetter.objects.create(
            mailbox=self.support, direction="inbound", status="received",
            from_address="customer@example.org", to_address=self.support.address,
            subject="Invoice question", body_text="Invoice number 234"
        )
        hidden = EmailLetter.objects.create(
            mailbox=self.accounts, direction="inbound", status="received",
            from_address="finance@example.org", to_address=self.accounts.address,
            subject="Private financial details", body_text="Sensitive account"
        )
        self.login(self.staff)
        allowed = self.client.get(reverse("email_drafts"), {"forward": original.pk})
        self.assertEqual(allowed.status_code, 200)
        self.assertContains(allowed, "Invoice number 234")
        self.assertContains(allowed, "Forwarded: Invoice question")
        self.assertEqual(self.client.get(
            reverse("email_drafts"), {"forward": hidden.pk}
        ).status_code, 404)
        self.login(self.other)
        self.assertEqual(self.client.get(
            reverse("email_drafts"), {"forward": original.pk}
        ).status_code, 404)
        self.assertFalse(EmailLetter.objects.filter(direction="outbound").exists())

    def test_cc_bcc_only_visible_to_author_or_owner(self):
        outgoing = compose(
            self.support, "customer@example.org", "Account question", "Reply",
            self.staff, cc="colleague@example.org", bcc="private@example.org"
        )
        self.login(self.staff)
        mine = self.client.get(reverse("email_history"), {"mailbox": self.support.pk})
        self.assertContains(mine, "private@example.org")
        self.login(self.other)
        others = self.client.get(reverse("email_history"), {"mailbox": self.support.pk})
        self.assertContains(others, "colleague@example.org")
        self.assertNotContains(others, "private@example.org")
        self.assertEqual(outgoing.status, "queued")

    def test_private_draft_copies_are_saved_not_delivered(self):
        self.login(self.staff)
        response = self.client.post(reverse("email_drafts"), {
            "action": "save", "mailbox_id": self.support.pk,
            "recipient": "customer@example.org",
            "cc": "colleague@example.org", "bcc": "finance@example.org",
            "subject": "Contract", "body": "Draft only."
        })
        self.assertEqual(response.status_code, 302)
        from core.email_models import EmailStaffDraft
        row = EmailStaffDraft.objects.get(author=self.staff)
        self.assertEqual(row.cc_addresses, "colleague@example.org")
        self.assertEqual(row.bcc_addresses, "finance@example.org")
        self.assertFalse(EmailLetter.objects.filter(direction="outbound").exists())
