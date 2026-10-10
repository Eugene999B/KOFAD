"""Shared mailbox conversation matching without leaking messages between customers.

KOFAD ticket tokens are in subjects because Brevo's HTTP API does not allow
standard In-Reply-To and Message-ID request headers. Provider-issued Message-IDs
are retained and additionally used to recognise inbound customer replies.
"""
import re

from django.db import transaction
from django.utils import timezone

from .email_models import EmailConversation, EmailLetter

TOKEN = re.compile(r"\[KOFAD-(\d{1,16})\]", re.IGNORECASE)
MESSAGE_IDS = re.compile(r"<[^<>\r\n]{1,250}>")
PREFIX = re.compile(r"^(?:\s*(?:re|fw|fwd)\s*:\s*)+", re.IGNORECASE)


def thread_subject(text, thread, *, reply=True):
    value = (text or "").replace("\r", " ").replace("\n", " ").strip()
    value = TOKEN.sub("", value)
    value = PREFIX.sub("", value).strip() or "Your message"
    marker = f"[KOFAD-{thread.pk}]"
    prefix = "Re: " if reply else ""
    return f"{prefix}{value[:max(1, 253 - len(marker) - len(prefix))]} {marker}"[:255]


def _ids(text):
    return MESSAGE_IDS.findall((text or "")[:2048])[:15]


def find_incoming_conversation(mailbox, sender, subject, reply_id="", references=""):
    sender = sender.strip().lower()
    match = TOKEN.search(subject or "")
    if match:
        conversation = EmailConversation.objects.filter(
            pk=int(match.group(1)), mailbox=mailbox,
            customer_email__iexact=sender,
        ).first()
        if conversation:
            return conversation
    identifiers = _ids(reply_id) + _ids(references)
    if identifiers:
        letter = (EmailLetter.objects.filter(
            mailbox=mailbox, conversation__customer_email__iexact=sender,
            message_id__in=identifiers,
        ).exclude(conversation__isnull=True).order_by("-created_at").first())
        if letter:
            return letter.conversation
    # Do NOT merge on a similar subject alone: multiple customers and unrelated
    # account matters often use identical subjects. New thread is safer.
    return None


def record_incoming(mailbox, *, sender, subject, reply_id, references, body,
                    message_id, fingerprint, attachments=False):
    with transaction.atomic():
        existing = EmailLetter.objects.filter(mailbox=mailbox, fingerprint=fingerprint,
                                               direction="inbound").first()
        if existing:
            return existing
        thread = find_incoming_conversation(mailbox, sender, subject, reply_id, references)
        if thread is None:
            thread = EmailConversation.objects.create(
                mailbox=mailbox, customer_email=sender,
                subject=TOKEN.sub("", PREFIX.sub("", subject)).strip()[:255] or "(No subject)",
                status="open", last_customer_at=timezone.now(),
            )
        else:
            thread.status = "open"
            thread.snoozed_until = None
            thread.archived_at = None
            thread.last_customer_at = timezone.now()
            thread.last_activity_at = timezone.now()
            thread.save(update_fields=[
                "status", "snoozed_until", "archived_at",
                "last_customer_at", "last_activity_at",
            ])
        return EmailLetter.objects.create(
            mailbox=mailbox, conversation=thread, fingerprint=fingerprint,
            direction="inbound", status="received", from_address=sender,
            to_address=mailbox.address, subject=subject, body_text=body,
            message_id=message_id, in_reply_to=reply_id,
            had_attachments=attachments,
        )


def new_outgoing_conversation(mailbox, recipient, subject):
    return EmailConversation.objects.create(
        mailbox=mailbox, customer_email=recipient, subject=subject[:255],
        status="pending",
    )


def record_outgoing_status(conversation):
    EmailConversation.objects.filter(pk=conversation.pk).update(
        status="pending", last_activity_at=timezone.now()
    )
