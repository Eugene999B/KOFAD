"""Role-scoped KOFAD email centre and signed Cloudflare inbound gateway.

Inbound: Cloudflare Email Worker -> authenticated HTTPS -> PostgreSQL.
Outbound: KOFAD outbox -> Brevo HTTPS API (only when configured).
"""
import hashlib
import hmac
import logging
import time
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import IntegrityError, models, transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .email_models import (EmailLetter, EmailMailbox, EmailMailboxMember,
                           EmailConversation, EmailConversationNote)
from .email_threads import (record_incoming, new_outgoing_conversation, thread_subject,
                            record_outgoing_status)

logger = logging.getLogger(__name__)
MAX_INGEST_BYTES = 1024 * 1024
MAX_BODY_CHARS = 100000


def enabled():
    return getattr(settings, "KOFAD_EMAIL_CENTER_ENABLED", False)


def owner(user):
    return user.is_authenticated and user.is_active and user.is_superuser


def visible_mailboxes(user, *, send=False):
    if not user.is_authenticated or not user.is_active:
        return EmailMailbox.objects.none()
    query = EmailMailbox.objects.filter(active=True)
    if owner(user):
        return query
    if send and not user.has_perm("core.send_messages"):
        return query.none()
    grants = EmailMailboxMember.objects.filter(user=user)
    grants = grants.filter(can_send=True) if send else grants.filter(can_read=True)
    query = query.filter(pk__in=grants.values("mailbox_id"))
    # Branch restrictions must be checked on the server, not only in the UI.
    branches = user.access.branches.all() if hasattr(user, "access") else []
    return query.filter(models.Q(branch__isnull=True) | models.Q(branch__in=branches))


def _address(value):
    address = (value or "").strip().lower()
    if len(address) > 254 or "\r" in address or "\n" in address:
        raise ValidationError("Invalid email address.")
    validate_email(address)
    return address


def _queue_external(mailbox, recipient, subject, body, user=None, *, source_key=None, reply_id="", conversation=None):
    if not (getattr(settings, "KOFAD_EMAIL_ENABLED", False)
            and settings.KOFAD_EMAIL_PROVIDER == "brevo"
            and settings.KOFAD_BREVO_API_KEY):
        raise ValidationError("External email is not connected yet. Internal KOFAD messages are available.")
    if source_key:
        letter, _ = EmailLetter.objects.get_or_create(
            source_key=source_key,
            defaults=dict(mailbox=mailbox, direction="outbound", status="queued",
                          from_address=mailbox.address, to_address=recipient,
                          subject=subject, body_text=body, created_by=user,
                          in_reply_to=reply_id, conversation=conversation,
                          next_attempt_at=timezone.now()),
        )
        return letter
    return EmailLetter.objects.create(
        mailbox=mailbox, direction="outbound", status="queued",
        from_address=mailbox.address, to_address=recipient, subject=subject,
        body_text=body, created_by=user, in_reply_to=reply_id,
        conversation=conversation, next_attempt_at=timezone.now(),
    )


def compose(mailbox, recipient, subject, body, user, *, reply_id="", conversation=None):
    recipient = _address(recipient)
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject or len(subject) > 255 or "\r" in subject or "\n" in subject:
        raise ValidationError("Enter a valid single-line subject.")
    if not body or len(body) > 32000:
        raise ValidationError("Message must be between 1 and 32,000 characters.")
    if reply_id and ("\n" in reply_id or "\r" in reply_id or len(reply_id) > 255):
        raise ValidationError("Invalid reply reference.")
    # Same-domain KOFAD mailboxes can exchange internal mail without external delivery.
    internal = EmailMailbox.objects.filter(address=recipient, active=True).first()
    with transaction.atomic():
        if not internal:
            if conversation is not None:
                if conversation.mailbox_id != mailbox.pk or conversation.customer_email.lower() != recipient:
                    raise ValidationError("Conversation does not belong to this mailbox or recipient.")
            else:
                conversation = new_outgoing_conversation(mailbox, recipient, subject)
            final_subject = thread_subject(subject, conversation, reply=bool(reply_id))
            record = _queue_external(mailbox, recipient, final_subject, body, user,
                                     reply_id=reply_id, conversation=conversation)
            record_outgoing_status(conversation)
            return record
        sent = EmailLetter.objects.create(
            mailbox=mailbox, direction="outbound", status="internal",
            from_address=mailbox.address, to_address=recipient,
            subject=subject, body_text=body, created_by=user,
            in_reply_to=reply_id, submitted_at=timezone.now(),
        )
        EmailLetter.objects.create(
            mailbox=internal, direction="inbound", status="received",
            from_address=mailbox.address, to_address=recipient,
            subject=subject, body_text=body,
            in_reply_to=reply_id,
            fingerprint=hashlib.sha256(f"kofad-internal:{sent.pk}".encode()).hexdigest(),
        )
        return sent


@login_required
def inbox(request, section="inbox"):
    """One secured service behind separate, focused email workspace pages."""
    if section not in {"inbox", "history", "automations", "team"}:
        raise Http404
    if not enabled():
        raise Http404("Email Centre is not enabled yet.")
    if section in {"automations", "team"} and not owner(request.user):
        raise PermissionDenied("Only the system administrator can manage email settings.")
    # Older history links use /email/?status=... and must remain usable.
    if section == "inbox" and not request.GET.get("thread") and any(
        name in request.GET for name in ("q", "direction", "status", "page")
    ):
        section = "history"
    all_mailboxes = list(visible_mailboxes(request.user))
    can_write = set(visible_mailboxes(request.user, send=True).values_list("pk", flat=True))
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "assign":
                if not owner(request.user):
                    raise PermissionDenied
                mailbox = get_object_or_404(EmailMailbox, pk=request.POST.get("mailbox_id"))
                user = get_object_or_404(User, pk=request.POST.get("user_id"), is_active=True)
                can_read = request.POST.get("can_read") == "on"
                can_send = request.POST.get("can_send") == "on"
                if not can_read and not can_send:
                    EmailMailboxMember.objects.filter(mailbox=mailbox, user=user).delete()
                else:
                    EmailMailboxMember.objects.update_or_create(
                        mailbox=mailbox, user=user,
                        defaults={"can_read": can_read, "can_send": can_send},
                    )
                from .services import audit
                audit(request.user, mailbox.branch, "email.membership_updated", user.pk,
                      {"mailbox": mailbox.address, "read": can_read, "send": can_send})
                messages.success(request, "Staff mailbox permissions updated.")
            elif action == "bulk_assign":
                if not owner(request.user):
                    raise PermissionDenied
                if request.POST.get("confirm_replace") != "yes":
                    raise ValidationError("Confirm the mailbox permissions to save.")
                raw_read = request.POST.getlist("read_mailboxes")
                raw_send = request.POST.getlist("send_mailboxes")
                if len(raw_read) + len(raw_send) > 300:
                    raise ValidationError("Too many mailbox selections.")
                if any(not item.isdecimal() for item in raw_read + raw_send):
                    raise ValidationError("Invalid mailbox selection.")
                read_ids = {int(item) for item in raw_read}
                send_ids = {int(item) for item in raw_send}
                read_ids.update(send_ids)  # Reply access always includes reading.
                available = {
                    m.pk: m for m in EmailMailbox.objects.filter(active=True)
                }
                if not read_ids.issubset(available):
                    raise ValidationError("One or more selected mailboxes are unavailable.")
                with transaction.atomic():
                    member = get_object_or_404(
                        User.objects.select_for_update(), pk=request.POST.get("user_id"),
                        is_active=True, is_superuser=False,
                    )
                    if send_ids and not member.has_perm("core.send_messages"):
                        raise ValidationError(
                            "This staff role does not have the email sending permission. "
                            "Update the staff role first."
                        )
                    branches = (
                        set(member.access.branches.values_list("pk", flat=True))
                        if hasattr(member, "access") else set()
                    )
                    if any(
                        available[pk].branch_id is not None
                        and available[pk].branch_id not in branches
                        for pk in read_ids
                    ):
                        raise ValidationError(
                            "Staff cannot be assigned email from an unauthorised branch."
                        )
                    EmailMailboxMember.objects.filter(
                        user=member, mailbox__active=True
                    ).exclude(mailbox_id__in=read_ids).delete()
                    for mailbox_id in sorted(read_ids):
                        EmailMailboxMember.objects.update_or_create(
                            user=member, mailbox_id=mailbox_id,
                            defaults={"can_read": True,
                                      "can_send": mailbox_id in send_ids},
                        )
                    from .services import audit
                    audit(
                        request.user, None, "email.mailbox_access_bulk_updated",
                        member.pk,
                        {"read_mailbox_ids": sorted(read_ids),
                         "send_mailbox_ids": sorted(send_ids)},
                    )
                messages.success(
                    request, f"Saved {len(read_ids)} mailboxes for {member.get_full_name() or member.username}."
                )
            elif action == "new_mailbox":
                if not owner(request.user):
                    raise PermissionDenied
                address = _address(request.POST.get("address"))
                if not address.endswith("@kofadimpex.com"):
                    raise ValidationError("Use an @kofadimpex.com address.")
                label = request.POST.get("label", "").strip()[:100]
                if not label:
                    raise ValidationError("Enter a mailbox name.")
                mailbox, created = EmailMailbox.objects.get_or_create(
                    address=address, defaults={"label": label})
                if not created:
                    raise ValidationError("That address is already registered.")
                from .services import audit
                audit(request.user, None, "email.mailbox_created", mailbox.pk,
                      {"address": mailbox.address})
                messages.success(request, "Mailbox added. Cloudflare routing must also target KOFAD.")
            elif action == "approve_draft":
                mailbox = get_object_or_404(visible_mailboxes(request.user, send=True),
                                            pk=request.POST.get("mailbox_id"))
                if not (owner(request.user) or request.user.has_perm("core.operate_finance")):
                    raise PermissionDenied
                from .debt_email import email_still_allowed
                with transaction.atomic():
                    draft = get_object_or_404(EmailLetter.objects.select_for_update(),
                                              pk=request.POST.get("letter_id"),
                                              mailbox=mailbox, direction="outbound",
                                              status="draft")
                    if not email_still_allowed(draft.source_key, draft.to_address):
                        raise ValidationError("Customer permission or outstanding debt changed. This draft cannot be sent.")
                    draft.status = "queued"
                    draft.approved_by = request.user
                    draft.approved_at = timezone.now()
                    draft.next_attempt_at = timezone.now()
                    draft.save(update_fields=["status", "approved_by", "approved_at", "next_attempt_at"])
                    from .services import audit
                    audit(request.user, mailbox.branch, "email.draft_approved", draft.pk,
                          {"mailbox": mailbox.address, "destination": draft.to_address})
                messages.success(request, "Reviewed message queued for sending.")
            elif action in {"send", "reply", "thread_reply"}:
                mailbox = get_object_or_404(visible_mailboxes(request.user, send=True),
                                            pk=request.POST.get("mailbox_id"))
                reply_id = ""
                recipient = request.POST.get("to", "")
                subject = request.POST.get("subject", "")
                conversation = None
                if action == "reply":
                    original = get_object_or_404(EmailLetter, pk=request.POST.get("reply_to"),
                                                 mailbox=mailbox, direction="inbound")
                    recipient, subject, reply_id = original.from_address, original.subject, original.message_id
                    conversation = original.conversation
                    if conversation is None:
                        with transaction.atomic():
                            original = EmailLetter.objects.select_for_update().get(pk=original.pk)
                            conversation = original.conversation
                            if conversation is None:
                                conversation = EmailConversation.objects.create(
                                    mailbox=mailbox, customer_email=recipient,
                                    subject=subject[:255], last_customer_at=original.created_at,
                                )
                                original.conversation = conversation
                                original.save(update_fields=["conversation"])
                if action == "thread_reply":
                    conversation = get_object_or_404(EmailConversation,
                                                     pk=request.POST.get("conversation_id"),
                                                     mailbox=mailbox)
                    recipient, subject = conversation.customer_email, conversation.subject
                    latest_customer = conversation.letters.filter(direction="inbound").order_by("-created_at").first()
                    reply_id = latest_customer.message_id if latest_customer else ""
                created = compose(mailbox, recipient, subject, request.POST.get("body"), request.user,
                                  reply_id=reply_id, conversation=conversation)
                from .services import audit
                audit(request.user, mailbox.branch, "email.message_created", created.pk,
                      {"mailbox": mailbox.address, "direction": created.direction,
                       "conversation_id": created.conversation_id})
                messages.success(request, "Delivered internally." if created.status == "internal"
                                 else "Reply queued in the customer conversation.")
            elif action in {"thread_update", "thread_note"}:
                mailbox = get_object_or_404(visible_mailboxes(request.user, send=True),
                                            pk=request.POST.get("mailbox_id"))
                with transaction.atomic():
                    thread = get_object_or_404(EmailConversation.objects.select_for_update(),
                                               pk=request.POST.get("conversation_id"),
                                               mailbox=mailbox)
                    if action == "thread_note":
                        note = request.POST.get("note", "").strip()
                        if not note or len(note) > 4000:
                            raise ValidationError("An internal note must be 1-4,000 characters.")
                        EmailConversationNote.objects.create(
                            conversation=thread, author=request.user, body=note,
                        )
                        messages.success(request, "Private team note saved. It was not emailed.")
                    else:
                        new_status = request.POST.get("thread_status", thread.status)
                        priority = request.POST.get("priority", thread.priority)
                        if new_status not in {"open", "pending", "closed"}:
                            raise ValidationError("Unknown conversation status.")
                        if priority not in {"normal", "high"}:
                            raise ValidationError("Unknown priority.")
                        assigned = request.POST.get("assigned_to", "")
                        if assigned:
                            person = get_object_or_404(User, pk=assigned, is_active=True)
                            if not (owner(person) or visible_mailboxes(person, send=True)
                                    .filter(pk=mailbox.pk).exists()):
                                raise ValidationError("User is not authorised to send from this mailbox.")
                            thread.assigned_to = person
                        else:
                            thread.assigned_to = None
                        thread.status = new_status
                        thread.priority = priority
                        thread.save(update_fields=["assigned_to", "status", "priority"])
                        messages.success(request, "Conversation assignment and status updated.")
                    from .services import audit
                    audit(request.user, mailbox.branch, "email.conversation_" + action,
                          thread.pk, {"mailbox": mailbox.address, "status": thread.status})
            else:
                raise ValidationError("Unknown email action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        if action in {"bulk_assign", "assign", "new_mailbox"}:
            return redirect("email_team")
        if action == "approve_draft":
            return redirect("email_history")
        return redirect("email_center")
    chosen_id = request.GET.get("mailbox", "")
    chosen = next((m for m in all_mailboxes if str(m.pk) == chosen_id), None)
    chosen = chosen or (all_mailboxes[0] if all_mailboxes else None)
    direction = request.GET.get("direction", "all")
    status = request.GET.get("status", "all")
    query = request.GET.get("q", "").strip()[:100]
    if direction not in {"all", "inbound", "outbound"}:
        direction = "all"
    known_statuses = {value for value, _ in EmailLetter.STATUS}
    if status not in known_statuses | {"all", "attention"}:
        status = "all"
    letters_qs = EmailLetter.objects.filter(mailbox=chosen) if chosen else EmailLetter.objects.none()
    inbox_count = letters_qs.filter(direction="inbound").count()
    sent_count = letters_qs.filter(direction="outbound").count()
    draft_count = letters_qs.filter(status="draft").count()
    attention_count = letters_qs.filter(status__in=["failed", "uncertain"]).count()
    if direction != "all":
        letters_qs = letters_qs.filter(direction=direction)
    if status == "attention":
        letters_qs = letters_qs.filter(status__in=["failed", "uncertain"])
    elif status != "all":
        letters_qs = letters_qs.filter(status=status)
    if query:
        letters_qs = letters_qs.filter(
            models.Q(subject__icontains=query) |
            models.Q(from_address__icontains=query) |
            models.Q(to_address__icontains=query) |
            models.Q(body_text__icontains=query))
    letters = Paginator(
        letters_qs.select_related("created_by", "approved_by").order_by("-created_at", "-pk"), 20
    ).get_page(request.GET.get("page", "1"))
    thread_status = request.GET.get("thread_status", "all")
    thread_owner = request.GET.get("thread_owner", "all")
    thread_q = request.GET.get("thread_q", "").strip()[:100]
    if thread_status not in {"all", "open", "pending", "closed"}:
        thread_status = "all"
    if thread_owner not in {"all", "mine", "unassigned"}:
        thread_owner = "all"
    conversation_qs = (
        EmailConversation.objects.filter(mailbox=chosen)
        if chosen else EmailConversation.objects.none()
    )
    open_conversations = conversation_qs.filter(status="open").count()
    unassigned_conversations = conversation_qs.filter(assigned_to__isnull=True).exclude(status="closed").count()
    if thread_status != "all":
        conversation_qs = conversation_qs.filter(status=thread_status)
    if thread_owner == "mine":
        conversation_qs = conversation_qs.filter(assigned_to=request.user)
    elif thread_owner == "unassigned":
        conversation_qs = conversation_qs.filter(assigned_to__isnull=True)
    if thread_q:
        conversation_qs = conversation_qs.filter(
            models.Q(subject__icontains=thread_q) |
            models.Q(customer_email__icontains=thread_q)
        )
    conversations = conversation_qs.select_related("assigned_to").order_by("-last_activity_at")[:50]
    active_thread = None
    thread_letters = []
    thread_notes = []
    eligible_assignees = []
    thread_id = request.GET.get("thread", "")
    if chosen and thread_id:
        active_thread = get_object_or_404(EmailConversation.objects.select_related("assigned_to"),
                                          pk=thread_id, mailbox=chosen)
        thread_letters = list(active_thread.letters.select_related("created_by")
                              .order_by("created_at", "pk")[:200])
        thread_notes = list(active_thread.notes.select_related("author")
                            .order_by("created_at", "pk")[:100])
        if chosen.pk in can_write:
            eligible_assignees = list(User.objects.filter(
                is_active=True,
                pk__in=EmailMailboxMember.objects.filter(
                    mailbox=chosen, can_send=True).values("user_id")
            ).order_by("username")[:150])
            if owner(request.user) and not any(u.pk == request.user.pk for u in eligible_assignees):
                eligible_assignees.insert(0, request.user)
    staff_users = (
        list(User.objects.filter(is_active=True, is_superuser=False).order_by("username")[:300])
        if owner(request.user) else []
    )
    staff_id = request.GET.get("staff", "")
    selected_staff = next((u for u in staff_users if str(u.pk) == staff_id), None)
    if selected_staff is None and staff_users:
        selected_staff = staff_users[0]
    grants = {
        m.mailbox_id: m for m in EmailMailboxMember.objects.filter(
            user=selected_staff, mailbox__active=True
        )
    } if selected_staff else {}
    allowed_staff_branches = (
        set(selected_staff.access.branches.values_list("pk", flat=True))
        if selected_staff and hasattr(selected_staff, "access") else set()
    )
    staff_can_reply = bool(
        selected_staff and selected_staff.has_perm("core.send_messages")
    )
    permission_rows = [
        {"mailbox": mailbox,
         "can_read": bool(grants.get(mailbox.pk) and grants[mailbox.pk].can_read),
         "can_send": bool(grants.get(mailbox.pk) and grants[mailbox.pk].can_send),
         "assignable": mailbox.branch_id is None
         or mailbox.branch_id in allowed_staff_branches}
        for mailbox in all_mailboxes
    ] if owner(request.user) else []
    verified_senders = {
        value.strip().lower()
        for value in getattr(settings, "KOFAD_BREVO_REGISTERED_SENDERS",
                             getattr(settings, "KOFAD_BREVO_TRANSACTION_FROM_EMAIL", "")).split(",")
        if value.strip()
    }
    shared_sender_email = getattr(settings, "KOFAD_BREVO_TRANSACTION_FROM_EMAIL", "")
    uses_shared_sender = bool(
        chosen and chosen.address.lower() not in verified_senders
    )
    return render(request, "email_center.html", {
        "title": "Email Centre", "mailboxes": all_mailboxes, "selected": chosen,
        "email_section": section, "email_selected_staff": selected_staff,
        "email_permission_rows": permission_rows,
        "email_staff_can_reply": staff_can_reply,
        "email_shared_sender": shared_sender_email,
        "email_uses_shared_sender": uses_shared_sender,
        "letters": letters, "writable_ids": can_write,
        "conversations": conversations, "active_thread": active_thread,
        "thread_letters": thread_letters, "thread_notes": thread_notes,
        "eligible_assignees": eligible_assignees,
        "mail_query": query, "mail_direction": direction, "mail_status": status,
        "thread_status_filter": thread_status, "thread_owner_filter": thread_owner,
        "thread_search": thread_q, "open_conversations": open_conversations,
        "unassigned_conversations": unassigned_conversations,
        "inbox_count": inbox_count, "sent_count": sent_count, "draft_count": draft_count,
        "attention_count": attention_count,
        "memberships": EmailMailboxMember.objects.select_related("user", "mailbox").filter(
            mailbox__in=all_mailboxes).order_by("mailbox__address", "user__username")
        if owner(request.user) else [],
        "staff_users": staff_users,
        "is_mail_owner": owner(request.user),
        "daily_email_usage": __import__("core.brevo_email", fromlist=["usage_today"]).usage_today(),
        "email_queued_count": EmailLetter.objects.filter(direction="outbound", status__in=["queued", "failed"]).count() if owner(request.user) else 0,
        "system_notice_history": list(__import__("marketplace.models", fromlist=["EmailNotice"]).EmailNotice.objects.order_by("-created_at")[:20]) if owner(request.user) else [],
        "external_ready": bool(getattr(settings, "KOFAD_EMAIL_ENABLED", False)
                               and settings.KOFAD_EMAIL_PROVIDER == "brevo"
                               and settings.KOFAD_BREVO_API_KEY),
    })


@csrf_exempt
@require_POST
def ingest(request):
    """Authenticated binary RFC822 ingestion, never a public mail submission form."""
    secret = getattr(settings, "KOFAD_EMAIL_INGEST_SECRET", "")
    if not enabled() or len(secret) < 32:
        return HttpResponse(status=503)
    raw = request.body
    if not raw or len(raw) > MAX_INGEST_BYTES:
        return HttpResponse(status=413)
    recipient = request.headers.get("X-Kofad-Recipient", "").lower().strip()
    timestamp = request.headers.get("X-Kofad-Timestamp", "")
    signature = request.headers.get("X-Kofad-Signature", "")
    try:
        if abs(int(time.time()) - int(timestamp)) > 300:
            return HttpResponse(status=403)
        recipient = _address(recipient)
    except (TypeError, ValueError, ValidationError):
        return HttpResponse(status=403)
    material = (timestamp + "\n" + recipient + "\n"
                + hashlib.sha256(raw).hexdigest()).encode()
    expected = hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return HttpResponse(status=403)
    mailbox = EmailMailbox.objects.filter(address=recipient, active=True).first()
    if not mailbox:
        return HttpResponse(status=404)
    try:
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        from_address = _address(parseaddr(parsed.get("From", ""))[1])
        subject = str(parsed.get("Subject", ""))[:255].replace("\r", " ").replace("\n", " ")
        message_id = str(parsed.get("Message-ID", ""))[:255]
        reply_id = str(parsed.get("In-Reply-To", ""))[:255]
        plain = parsed.get_body(preferencelist=("plain",))
        attachments = any(True for _ in parsed.iter_attachments()) if parsed.is_multipart() else False
        # Do not silently drop attachments or HTML-only content. Cloudflare
        # falls back to the verified business Gmail when we decline those.
        if attachments or not plain:
            return HttpResponse(status=422)
        content = plain.get_content()
        if not isinstance(content, str):
            return HttpResponse(status=422)
        fingerprint = hashlib.sha256(recipient.encode() + b"\n" + raw).hexdigest()
        record_incoming(
            mailbox, sender=from_address, subject=subject, reply_id=reply_id,
            references=str(parsed.get("References", ""))[:2048],
            body=content[:MAX_BODY_CHARS], message_id=message_id,
            fingerprint=fingerprint, attachments=attachments,
        )
        result = JsonResponse({"accepted": True})
        result["Cache-Control"] = "no-store"
        return result
    except (ValueError, LookupError, IntegrityError, UnicodeError) as exc:
        logger.warning("Email ingest could not parse message: %s", type(exc).__name__)
        return HttpResponse(status=422)


def deliver_outgoing(limit=10):
    """One existing Railway worker processes the bounded outbox over HTTPS."""
    if not (enabled() and getattr(settings, "KOFAD_EMAIL_ENABLED", False)
            and settings.KOFAD_EMAIL_PROVIDER == "brevo"
            and settings.KOFAD_BREVO_API_KEY):
        return 0
    now = timezone.now()
    # An interrupted provider request might have succeeded before the process died.
    # Flag it for review rather than silently causing a duplicate financial email.
    EmailLetter.objects.filter(
        direction="outbound", status="sending",
        next_attempt_at__lt=now - timedelta(minutes=20),
    ).update(status="uncertain", last_error="Interrupted send; review before retry.")
    ids = list(EmailLetter.objects.filter(
        direction="outbound", status__in=["queued", "failed"], attempts__lt=3,
        next_attempt_at__lte=now,
    ).order_by("created_at").values_list("pk", flat=True)[:limit])
    submitted = 0
    for pk in ids:
        with transaction.atomic():
            claimed = EmailLetter.objects.filter(
                pk=pk, direction="outbound", status__in=["queued", "failed"],
                attempts__lt=3, next_attempt_at__lte=timezone.now(),
            ).update(status="sending", attempts=models.F("attempts") + 1,
                     next_attempt_at=timezone.now())
        if not claimed:
            continue
        row = EmailLetter.objects.get(pk=pk)
        # Preserve part of today's limited free allowance for security codes,
        # receipts and staff correspondence instead of exhausting it on campaigns.
        if row.source_key and (row.source_key.startswith("campaign:") or ":reminder:" in row.source_key and row.source_key.startswith("debtmail:")):
            from .brevo_email import usage_today
            allowance = usage_today()
            safety_reserve = min(50, allowance["limit"] // 5)
            if allowance["remaining"] <= safety_reserve:
                tomorrow = (timezone.localtime().replace(hour=0, minute=10, second=0,
                                                         microsecond=0) + timedelta(days=1))
                EmailLetter.objects.filter(pk=pk).update(
                    status="failed", next_attempt_at=tomorrow,
                    attempts=models.F("attempts") - 1,
                    last_error="Reserved daily email allowance for security and transactions.",
                )
                continue
        from .debt_email import email_still_allowed
        if not email_still_allowed(row.source_key, row.to_address, for_delivery=True, approved=bool(row.approved_at)):
            EmailLetter.objects.filter(pk=pk).update(
                status="suppressed", last_error="Customer email preference or current debt state changed.")
            continue
        from .email_campaigns import is_campaign_recipient_allowed
        if not is_campaign_recipient_allowed(row.source_key, row.to_address):
            EmailLetter.objects.filter(pk=pk).update(
                status="suppressed", last_error="Recipient did not opt in or campaign was paused.")
            continue
        try:
            from .brevo_email import send_brevo
            provider_id = send_brevo(subject=row.subject, body=row.body_text,
                       recipient=row.to_address, purpose="transaction",
                       sender_email=row.from_address, return_message_id=True)
        except __import__("core.brevo_email", fromlist=["UncertainEmailDelivery"]).UncertainEmailDelivery:
            EmailLetter.objects.filter(pk=pk).update(
                status="uncertain",
                last_error="Delivery status unknown; review before retry.",
            )
        except __import__("core.brevo_email", fromlist=["DailyEmailLimitExceeded"]).DailyEmailLimitExceeded:
            # Do not burn retry attempts while waiting for tomorrow's free allowance.
            tomorrow = (timezone.localtime().replace(hour=0, minute=10, second=0,
                                                     microsecond=0) + timedelta(days=1))
            EmailLetter.objects.filter(pk=pk).update(
                status="failed", next_attempt_at=tomorrow,
                attempts=models.F("attempts") - 1,
                last_error="Daily send allowance reached; queued for tomorrow.",
            )
        except Exception:
            EmailLetter.objects.filter(pk=pk).update(
                status="failed", next_attempt_at=timezone.now()
                + timedelta(minutes=min(60, 5 * row.attempts)),
                last_error="Provider did not accept this email.",
            )
        else:
            EmailLetter.objects.filter(pk=pk).update(
                status="submitted", submitted_at=timezone.now(), last_error="",
                message_id=provider_id or "",
            )
            submitted += 1
    return submitted


def enqueue_closing_report(closing):
    """Send to verified, opted-in authorized reporting staff once per closing."""
    if not (enabled() and getattr(settings, "KOFAD_EMAIL_ENABLED", False)
            and settings.KOFAD_EMAIL_PROVIDER == "brevo"):
        return 0
    mailbox = EmailMailbox.objects.filter(address="reports@kofadimpex.com", active=True).first()
    if not mailbox:
        return 0
    from marketplace.models import EmailIdentity
    summary = closing.summary or {}
    subject = f"KOFAD daily closing — {closing.branch.name} — {closing.date}"
    body = ("Daily closing summary for " + closing.branch.name + "\n"
            + "Date: " + str(closing.date) + "\n"
            + "Sales: GHS " + str(summary.get("sales_total", "0.00")) + "\n"
            + "Expenses: GHS " + str(summary.get("expenses_total", "0.00")) + "\n"
            + "Debt collections: GHS " + str(summary.get("debt_collections", "0.00")) + "\n"
            + "Expected cash: GHS " + str(closing.expected.get("cash", "0.00")) + "\n"
            + "Counted cash: GHS " + str(closing.counted.get("cash", "0.00")) + "\n"
            + "View the full verified report in KOFAD.")
    count = 0
    for contact in EmailIdentity.objects.filter(
            kind="staff", verified_at__isnull=False, notifications_enabled=True):
        user = User.objects.filter(pk=contact.owner_id, is_active=True).first()
        if not user or not (user.is_superuser or
                            (user.has_perm("core.view_reports") and
                             hasattr(user, "access") and
                             user.access.branches.filter(pk=closing.branch_id).exists())):
            continue
        _queue_external(mailbox, contact.email, subject, body,
                        source_key=f"closing-report:{closing.pk}:{user.pk}")
        count += 1
    return count
