"""Realistic multi-agent shared inbox tests; no messages leave the test suite."""
import hashlib
import hmac
import time
from email.message import EmailMessage
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from core.email_center import compose, deliver_outgoing
from core.email_models import (
    EmailConversation, EmailConversationNote, EmailLetter,
    EmailMailbox, EmailMailboxMember,
)
from core.email_threads import find_incoming_conversation
from core.models import Access

MAIL = dict(
    KOFAD_EMAIL_CENTER_ENABLED=True, KOFAD_EMAIL_ENABLED=True,
    KOFAD_EMAIL_PROVIDER="brevo", KOFAD_BREVO_API_KEY="TEST_ONLY",
    KOFAD_BREVO_SECURITY_FROM_EMAIL="security@kofadimpex.com",
    KOFAD_BREVO_TRANSACTION_FROM_EMAIL="transactions@kofadimpex.com",
    KOFAD_BREVO_REGISTERED_SENDERS="transactions@kofadimpex.com",
    KOFAD_EMAIL_INGEST_SECRET="a" * 64,
)


@override_settings(**MAIL)
class TeamInboxThreadsTests(TestCase):
    def setUp(self):
        self.support = EmailMailbox.objects.get(address="support@kofadimpex.com")
        self.finance = EmailMailbox.objects.get(address="accounts@kofadimpex.com")
        self.admin = User.objects.create_superuser(
            "mailthreadadmin", "admin@example.org", "SafePassword.1234")
        self.reader = User.objects.create_user("threadreader", password="SafePassword.1234")
        self.other = User.objects.create_user("threadstranger", password="SafePassword.1234")
        EmailMailboxMember.objects.create(
            mailbox=self.support, user=self.reader, can_read=True, can_send=False)

    def login(self, user):
        access, _ = Access.objects.get_or_create(user=user)
        self.client.force_login(user)
        session = self.client.session
        session["access_version"] = access.session_version
        session.save()

    def signed_inbound(self, sender, subject, message_id, reply_id="", refs=""):
        message = EmailMessage()
        message["From"] = sender
        message["To"] = self.support.address
        message["Subject"] = subject
        message["Message-ID"] = message_id
        if reply_id:
            message["In-Reply-To"] = reply_id
        if refs:
            message["References"] = refs
        message.set_content("Customer says hello. Please respond.")
        raw = message.as_bytes()
        stamp = str(int(time.time()))
        data = (stamp + "\n" + self.support.address + "\n"
                + hashlib.sha256(raw).hexdigest()).encode()
        signature = hmac.new(("a" * 64).encode(), data, hashlib.sha256).hexdigest()
        return self.client.post(reverse("email_ingest"), raw, content_type="message/rfc822",
            HTTP_X_KOFAD_TIMESTAMP=stamp,
            HTTP_X_KOFAD_RECIPIENT=self.support.address,
            HTTP_X_KOFAD_SIGNATURE=signature)

    def test_incoming_thread_and_staff_response_stay_together(self):
        self.assertEqual(self.signed_inbound("client@example.org", "Order help",
                                             "<client-1@example.org>").status_code, 200)
        thread = EmailConversation.objects.get(customer_email="client@example.org")
        self.login(self.admin)
        response = self.client.post(reverse("email_center"), {
            "action": "thread_reply", "mailbox_id": self.support.pk,
            "conversation_id": thread.pk, "body": "We can help you with this.",
        })
        self.assertEqual(response.status_code, 302)
        reply = EmailLetter.objects.get(conversation=thread, direction="outbound")
        self.assertIn(f"[KOFAD-{thread.pk}]", reply.subject)
        self.assertEqual(reply.created_by, self.admin)
        self.assertEqual(reply.in_reply_to, "<client-1@example.org>")

    @patch("core.brevo_email.requests.post")
    def test_provider_message_id_is_saved_and_customer_reply_matches(self, post):
        post.return_value = Mock(status_code=201)
        post.return_value.json.return_value = {"messageId": "<brevo-101@smtp.example>"}
        msg = compose(self.support, "client@example.org", "Delivery", "Your order shipped", self.admin)
        self.assertEqual(deliver_outgoing(limit=2), 1)
        msg.refresh_from_db()
        self.assertEqual(msg.message_id, "<brevo-101@smtp.example>")
        response = self.signed_inbound("client@example.org", "Re: Delivery",
                                      "<client-answer@example.org>",
                                      reply_id="<brevo-101@smtp.example>")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmailConversation.objects.count(), 1)
        self.assertEqual(EmailLetter.objects.filter(conversation=msg.conversation).count(), 2)

    def test_ticket_id_cannot_join_another_customer_or_other_mailbox(self):
        msg = compose(self.support, "original@example.org", "Private", "Hello", self.admin)
        title = f"Re: Private [KOFAD-{msg.conversation_id}]"
        self.assertEqual(self.signed_inbound("intruder@example.org", title,
                                             "<intruder-1@example.org>").status_code, 200)
        self.assertEqual(EmailConversation.objects.count(), 2)
        self.assertIsNone(find_incoming_conversation(
            self.finance, "original@example.org", title))

    def test_read_only_staff_cannot_reply_or_write_internal_notes(self):
        thread = EmailConversation.objects.create(
            mailbox=self.support, customer_email="client@example.org", subject="Private")
        self.login(self.reader)
        self.assertEqual(self.client.get(reverse("email_center"), {
            "mailbox": self.support.pk, "thread": thread.pk
        }).status_code, 200)
        for action in ("thread_reply", "thread_note", "thread_update"):
            response = self.client.post(reverse("email_center"), {
                "action": action, "mailbox_id": self.support.pk,
                "conversation_id": thread.pk,
                "body": "Attempt", "note": "Attempt",
            })
            self.assertEqual(response.status_code, 404)
        self.assertFalse(EmailConversationNote.objects.exists())

    def test_cross_mailbox_thread_id_cannot_be_opened(self):
        thread = EmailConversation.objects.create(
            mailbox=self.finance, customer_email="client@example.org", subject="Invoice")
        self.login(self.reader)
        response = self.client.get(reverse("email_center"), {
            "mailbox": self.support.pk, "thread": thread.pk
        })
        self.assertEqual(response.status_code, 404)
        self.login(self.admin)
        response = self.client.post(reverse("email_center"), {
            "action": "thread_reply", "mailbox_id": self.support.pk,
            "conversation_id": thread.pk, "body": "Access denied",
        })
        self.assertEqual(response.status_code, 404)

    def test_internal_note_never_queues_email(self):
        thread = EmailConversation.objects.create(
            mailbox=self.support, customer_email="client@example.org", subject="Delivery")
        self.login(self.admin)
        response = self.client.post(reverse("email_center"), {
            "action": "thread_note", "mailbox_id": self.support.pk,
            "conversation_id": thread.pk, "note": "Call supervisor before replying.",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EmailConversationNote.objects.get().author, self.admin)
        self.assertFalse(EmailLetter.objects.exists())
        self.assertEqual(EmailConversationNote.objects.get().body,
                         "Call supervisor before replying.")

    def test_assignment_rejects_staff_without_mailbox_send_permission(self):
        thread = EmailConversation.objects.create(
            mailbox=self.support, customer_email="client@example.org", subject="Change")
        self.login(self.admin)
        response = self.client.post(reverse("email_center"), {
            "action": "thread_update", "mailbox_id": self.support.pk,
            "conversation_id": thread.pk, "thread_status": "open",
            "priority": "high", "assigned_to": self.other.pk,
        })
        self.assertEqual(response.status_code, 302)
        thread.refresh_from_db()
        self.assertIsNone(thread.assigned_to)
        self.assertEqual(thread.priority, "normal")


    def test_conversation_queue_filters_by_status_assignment_and_customer(self):
        mine = EmailConversation.objects.create(
            mailbox=self.support, customer_email="vip@example.org",
            subject="VIP product help", status="open", assigned_to=self.admin)
        pending = EmailConversation.objects.create(
            mailbox=self.support, customer_email="other@example.org",
            subject="Order progress", status="pending")
        EmailConversation.objects.create(
            mailbox=self.finance, customer_email="vip@example.org",
            subject="Confidential account help", status="open", assigned_to=self.admin)
        self.login(self.admin)
        result = self.client.get(reverse("email_center"), {
            "mailbox": self.support.pk, "thread_status": "open",
            "thread_owner": "mine", "thread_q": "VIP",
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual([x.pk for x in result.context["conversations"]], [mine.pk])
        self.assertEqual(result.context["thread_status_filter"], "open")
        self.assertEqual(result.context["thread_owner_filter"], "mine")
        self.assertContains(result, "thread_q=VIP")

        result = self.client.get(reverse("email_center"), {
            "mailbox": self.support.pk, "thread_status": "pending",
            "thread_owner": "unassigned",
        })
        self.assertEqual([x.pk for x in result.context["conversations"]], [pending.pk])

        self.login(self.reader)
        result = self.client.get(reverse("email_center"), {
            "mailbox": self.support.pk, "thread_q": "account",
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(list(result.context["conversations"]), [])

    def test_failed_and_uncertain_history_link_shows_both_without_cross_mailbox_mail(self):
        for mailbox, subject, status in (
            (self.support, "Provider failed", "failed"),
            (self.support, "Delivery uncertain", "uncertain"),
            (self.support, "Provider accepted", "submitted"),
            (self.finance, "Private finance failure", "failed"),
        ):
            EmailLetter.objects.create(
                mailbox=mailbox, direction="outbound", status=status,
                from_address=mailbox.address, to_address="buyer@example.org",
                subject=subject, body_text="Technical message",
            )
        self.login(self.admin)
        result = self.client.get(reverse("email_center"), {
            "mailbox": self.support.pk, "status": "attention",
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.context["attention_count"], 2)
        self.assertEqual(result.context["letters"].paginator.count, 2)
        self.assertContains(result, "Provider failed")
        self.assertContains(result, "Delivery uncertain")
        self.assertNotContains(result, "Private finance failure")

    def test_inbound_duplicate_does_not_create_two_conversations(self):
        for _ in range(2):
            self.assertEqual(self.signed_inbound("client@example.org", "Enquiry",
                                                 "<client-unique@example.org>").status_code, 200)
        self.assertEqual(EmailConversation.objects.count(), 1)
