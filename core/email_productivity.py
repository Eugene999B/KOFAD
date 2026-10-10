"""Professional KOFAD email workflow: scoped work queues, drafts and reusable replies.

All staff-specific records are bound to the user's existing mailbox and branch permissions.
No network calls occur while a draft is saved; sending is explicitly user initiated.
"""
import csv
from datetime import timedelta
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods, require_POST

from .email_center import compose, enabled, owner, visible_mailboxes
from .email_models import (
    EmailConversation, EmailConversationReadState, EmailLetter, EmailMailbox,
    EmailSavedReply, EmailStaffDraft, EmailStaffSignature,
)
from .services import audit

MAX_BULK = 50


def _check_enabled():
    if not enabled():
        from django.http import Http404
        raise Http404("Email Centre is not enabled.")


def _base(user):
    available = list(visible_mailboxes(user))
    writable = set(visible_mailboxes(user, send=True).values_list("pk", flat=True))
    return {"mailboxes": available, "writable_ids": writable,
            "email_is_owner": owner(user)}


def _back(request, fallback="email_work"):
    target = request.POST.get("return_to", "")
    # Only known KOFAD paths are allowed, never external redirects.
    permitted = {"/email/work/", "/email/drafts/", "/email/replies/", "/email/reports/"}
    if target in permitted:
        return redirect(target)
    return redirect(fallback)


def _date(value):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValidationError("Enter a valid scheduled date and time.")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    if not timezone.now() + timedelta(minutes=1) < parsed < timezone.now() + timedelta(days=365):
        raise ValidationError("Schedule delivery between two minutes and one year from now.")
    return parsed


@login_required
@require_http_methods(["GET", "POST"])
def work(request):
    _check_enabled()
    available = visible_mailboxes(request.user)
    writeable = visible_mailboxes(request.user, send=True)
    allowed_ids = set(available.values_list("pk", flat=True))
    writable_ids = set(writeable.values_list("pk", flat=True))
    if request.method == "POST":
        action = request.POST.get("action", "")
        raw = request.POST.getlist("conversation_ids")
        if not raw or len(raw) > MAX_BULK or any(not v.isdecimal() for v in raw):
            messages.error(request, "Choose between 1 and 50 conversations.")
            return _back(request)
        ids = sorted({int(v) for v in raw})
        try:
            can_view = action in {"mark_read", "mark_unread", "star", "unstar"}
            if not can_view and action not in {
                "archive", "restore", "snooze", "unsnooze", "close", "reopen", "assign_me",
                "set_deadline", "clear_deadline"
            }:
                raise ValidationError("Unknown conversation action.")
            with transaction.atomic():
                qs = EmailConversation.objects.select_for_update().filter(
                    pk__in=ids, mailbox_id__in=allowed_ids if can_view else writable_ids
                )
                if qs.count() != len(ids):
                    raise PermissionDenied("A selected conversation is outside your mailboxes or permissions.")
                now = timezone.now()
                for thread in qs:
                    if can_view:
                        entry, _ = EmailConversationReadState.objects.get_or_create(
                            conversation=thread, user=request.user
                        )
                        if action == "mark_read":
                            entry.last_read_at = now
                            entry.save(update_fields=["last_read_at"])
                        elif action == "mark_unread":
                            entry.last_read_at = None
                            entry.save(update_fields=["last_read_at"])
                        else:
                            entry.starred = action == "star"
                            entry.save(update_fields=["starred"])
                    elif action in {"archive", "restore"}:
                        thread.archived_at = now if action == "archive" else None
                        thread.save(update_fields=["archived_at"])
                    elif action in {"snooze", "unsnooze"}:
                        hours = request.POST.get("hours", "24")
                        if action == "snooze" and hours not in {"1", "4", "24", "72", "168"}:
                            raise ValidationError("Choose a supported reminder interval.")
                        thread.snoozed_until = now + timedelta(hours=int(hours)) if action == "snooze" else None
                        thread.save(update_fields=["snoozed_until"])
                    elif action in {"set_deadline", "clear_deadline"}:
                        hours = request.POST.get("hours", "24")
                        if action == "set_deadline" and hours not in {"1", "4", "24", "72", "168"}:
                            raise ValidationError("Choose a supported follow-up interval.")
                        thread.due_at = now + timedelta(hours=int(hours)) if action == "set_deadline" else None
                        thread.save(update_fields=["due_at"])
                    elif action in {"close", "reopen"}:
                        thread.status = "closed" if action == "close" else "open"
                        thread.save(update_fields=["status"])
                    else:
                        thread.assigned_to = request.user
                        thread.save(update_fields=["assigned_to"])
                audit(request.user, None, f"email.work_{action}", ",".join(map(str, ids))[:160],
                      {"count": len(ids)})
            messages.success(request, f"Updated {len(ids)} conversation(s).")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return _back(request)

    scope = request.GET.get("scope", "active")
    if scope not in {"active", "mine", "unassigned", "unread", "starred", "archived", "snoozed", "overdue"}:
        scope = "active"
    mailbox_id = request.GET.get("mailbox", "")
    if mailbox_id and mailbox_id.isdecimal() and int(mailbox_id) in allowed_ids:
        base_qs = EmailConversation.objects.filter(mailbox_id=int(mailbox_id))
    else:
        base_qs = EmailConversation.objects.filter(mailbox_id__in=allowed_ids)
    search = request.GET.get("q", "").strip()[:100]
    if search:
        base_qs = base_qs.filter(models.Q(customer_email__icontains=search) |
                                 models.Q(subject__icontains=search))
    now = timezone.now()
    read_state = EmailConversationReadState.objects.filter(
        conversation_id=models.OuterRef("pk"), user=request.user
    )
    results = base_qs.annotate(
        mine_last_read=models.Subquery(read_state.values("last_read_at")[:1]),
        mine_starred=models.Exists(read_state.filter(starred=True)),
    )
    if scope == "archived":
        results = results.filter(archived_at__isnull=False)
    elif scope == "snoozed":
        results = results.filter(archived_at__isnull=True, snoozed_until__gt=now)
    else:
        results = results.filter(archived_at__isnull=True).filter(
            models.Q(snoozed_until__isnull=True) | models.Q(snoozed_until__lte=now)
        )
        if scope == "overdue":
            results = results.filter(due_at__lt=now).exclude(status="closed")
        elif scope == "mine":
            results = results.filter(assigned_to=request.user)
        elif scope == "unassigned":
            results = results.filter(assigned_to__isnull=True)
        elif scope == "starred":
            results = results.filter(mine_starred=True)
        elif scope == "unread":
            results = results.filter(last_customer_at__isnull=False).filter(
                models.Q(mine_last_read__isnull=True) |
                models.Q(last_customer_at__gt=models.F("mine_last_read"))
            )
    from django.core.paginator import Paginator
    rows = Paginator(
        results.select_related("mailbox", "assigned_to").order_by("-last_activity_at", "-pk"), 35
    ).get_page(request.GET.get("page", "1"))
    context = _base(request.user)
    context.update({"title": "Email Work Queue", "email_nav": "work",
                    "scope": scope, "mailbox_id": mailbox_id, "search": search,
                    "rows": rows, "now": now})
    return render(request, "email_pro_work.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def drafts(request):
    _check_enabled()
    writable = visible_mailboxes(request.user, send=True)
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "cancel_scheduled":
                with transaction.atomic():
                    letter = get_object_or_404(
                        EmailLetter.objects.select_for_update(), pk=request.POST.get("letter_id"),
                        created_by=request.user, mailbox__in=writable,
                        direction="outbound", status="queued",
                        next_attempt_at__gt=timezone.now(),
                    )
                    letter.status = "suppressed"
                    letter.last_error = "Scheduled message cancelled by its sender."
                    letter.save(update_fields=["status", "last_error"])
                    audit(request.user, letter.mailbox.branch, "email.scheduled_cancelled",
                          letter.pk, {"mailbox": letter.mailbox.address})
                messages.success(request, "Scheduled email cancelled before sending.")
            elif action == "delete":
                draft = get_object_or_404(EmailStaffDraft, pk=request.POST.get("draft_id"),
                                          author=request.user, mailbox__in=writable)
                draft.delete()
                messages.success(request, "Your draft was deleted.")
            elif action in {"save", "send"}:
                mailbox = get_object_or_404(writable, pk=request.POST.get("mailbox_id"))
                original_id = request.POST.get("draft_id") or ""
                if original_id:
                    draft = get_object_or_404(EmailStaffDraft, pk=original_id,
                                              author=request.user, mailbox=mailbox)
                else:
                    draft = EmailStaffDraft(mailbox=mailbox, author=request.user)
                recipient = (request.POST.get("recipient") or "").strip().lower()
                subject = (request.POST.get("subject") or "").strip()
                body = (request.POST.get("body") or "").strip()
                if len(subject) > 255 or "\r" in subject or "\n" in subject or len(body) > 32000:
                    raise ValidationError("Subject or message is too long.")
                scheduled = _date(request.POST.get("scheduled_for", ""))
                if action == "send":
                    if not recipient or not subject or not body:
                        raise ValidationError("To send an email, enter recipient, subject and message.")
                    if scheduled and EmailMailbox.objects.filter(address=recipient, active=True).exists():
                        raise ValidationError("Scheduling internal business messages is not yet supported.")
                    if request.POST.get("append_signature") == "yes":
                        signature = EmailStaffSignature.objects.filter(
                            mailbox=mailbox, user=request.user
                        ).first()
                        if signature and signature.body.strip():
                            body = body.rstrip() + "\n\n" + signature.body.strip()
                    with transaction.atomic():
                        sent = compose(mailbox, recipient, subject, body, request.user,
                                       conversation=draft.conversation)
                        if scheduled:
                            if sent.status != "queued":
                                raise ValidationError("Only queued external messages can be scheduled.")
                            EmailLetter.objects.filter(pk=sent.pk).update(next_attempt_at=scheduled)
                        if draft.pk:
                            draft.delete()
                        audit(request.user, mailbox.branch, "email.staff_draft_sent", sent.pk,
                              {"scheduled": bool(scheduled)})
                    messages.success(request, "Message scheduled." if scheduled else "Email queued for delivery.")
                else:
                    draft.recipient = recipient
                    draft.subject = subject
                    draft.body = body
                    draft.scheduled_for = scheduled
                    draft.save()
                    messages.success(request, "Draft saved privately. Nothing has been sent.")
            else:
                raise ValidationError("Unknown draft action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("email_drafts")
    current = None
    editing = request.GET.get("edit", "")
    if editing:
        current = get_object_or_404(EmailStaffDraft, pk=editing,
                                    author=request.user, mailbox__in=writable)
    options = _base(request.user)
    options.update({"title": "Email Drafts", "email_nav": "drafts",
                    "drafts": EmailStaffDraft.objects.filter(author=request.user,
                                                            mailbox__in=writable).select_related("mailbox")[:60],
                    "editing": current,
                    "scheduled": EmailLetter.objects.filter(
                        created_by=request.user, mailbox__in=writable,
                        direction="outbound", status="queued",
                        next_attempt_at__gt=timezone.now(),
                    ).select_related("mailbox").order_by("next_attempt_at")[:40],
                    "signatures": EmailStaffSignature.objects.filter(user=request.user,
                                                                   mailbox__in=writable)})
    return render(request, "email_pro_drafts.html", options)


@login_required
@require_http_methods(["GET", "POST"])
def library(request):
    _check_enabled()
    permitted = visible_mailboxes(request.user)
    writable = visible_mailboxes(request.user, send=True)
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "save_signature":
                mb = get_object_or_404(writable, pk=request.POST.get("mailbox_id"))
                body = request.POST.get("body", "").strip()
                if len(body) > 1200:
                    raise ValidationError("Email signatures are limited to 1,200 characters.")
                EmailStaffSignature.objects.update_or_create(
                    mailbox=mb, user=request.user, defaults={"body": body}
                )
                messages.success(request, "Signature saved for this mailbox.")
            elif action == "create_reply":
                mb_id = request.POST.get("mailbox_id")
                mb = get_object_or_404(permitted, pk=mb_id) if mb_id else None
                title = request.POST.get("title", "").strip()
                body = request.POST.get("body", "").strip()
                shared = request.POST.get("shared") == "yes"
                if shared and not owner(request.user):
                    raise PermissionDenied
                if not (0 < len(title) <= 120 and 0 < len(body) <= 10000):
                    raise ValidationError("Enter a reply title and message of appropriate length.")
                saved = EmailSavedReply.objects.create(mailbox=mb, author=request.user,
                                                       title=title, body=body, shared=shared)
                audit(request.user, getattr(mb, "branch", None), "email.saved_reply_created",
                      saved.pk, {"shared": shared})
                messages.success(request, "Saved reply added.")
            elif action == "delete_reply":
                saved = get_object_or_404(EmailSavedReply,
                    pk=request.POST.get("reply_id"))
                if not (owner(request.user) or saved.author_id == request.user.pk):
                    raise PermissionDenied
                if saved.mailbox_id and not permitted.filter(pk=saved.mailbox_id).exists():
                    raise PermissionDenied
                saved.delete()
                messages.success(request, "Saved reply removed.")
            else:
                raise ValidationError("Unknown response-library action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("email_replies")
    options = _base(request.user)
    options.update({
        "title": "Email Reply Library", "email_nav": "replies",
        "replies": EmailSavedReply.objects.filter(
            models.Q(shared=True) | models.Q(author=request.user)
        ).filter(models.Q(mailbox__isnull=True) | models.Q(mailbox__in=permitted))
         .select_related("author", "mailbox").order_by("title")[:100],
        "signatures": EmailStaffSignature.objects.filter(
            user=request.user, mailbox__in=writable
        ).select_related("mailbox"),
    })
    return render(request, "email_pro_library.html", options)


@login_required
@require_http_methods(["GET"])
def reports(request):
    _check_enabled()
    if not owner(request.user):
        raise PermissionDenied
    available = visible_mailboxes(request.user)
    chosen = request.GET.get("mailbox", "")
    mb = available.filter(pk=chosen).first() if chosen.isdecimal() else None
    q = EmailConversation.objects.all()
    letters = EmailLetter.objects.all()
    if mb:
        q = q.filter(mailbox=mb)
        letters = letters.filter(mailbox=mb)
    export = request.GET.get("export", "")
    if export == "csv":
        # Metadata only; never export OTPs, full message bodies or hidden recipients.
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="kofad-email-operations.csv"'
        response["Cache-Control"] = "private, no-store"
        writer = csv.writer(response)
        writer.writerow(["Conversation ID", "Department", "Customer", "Subject", "Status",
                         "Priority", "Assigned Staff", "Last Customer Activity",
                         "Last Activity", "Archived"])
        for item in q.select_related("mailbox", "assigned_to").order_by("-last_activity_at")[:10000]:
            def protect(text):
                v = str(text or "")
                return "'" + v if v.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else v
            writer.writerow([item.pk, protect(item.mailbox.address),
                             protect(item.customer_email), protect(item.subject),
                             item.status, item.priority,
                             protect(item.assigned_to.username if item.assigned_to else ""),
                             item.last_customer_at or "", item.last_activity_at,
                             bool(item.archived_at)])
        audit(request.user, None, "email.report_export", 0, {"mailbox_id": chosen or "all"})
        return response
    now = timezone.now()
    options = _base(request.user)
    options.update({
        "title": "Email Operations Report", "email_nav": "reports",
        "selected_mailbox": mb, "mailbox_id": chosen,
        "conversation_total": q.count(),
        "open_count": q.filter(status="open", archived_at__isnull=True).count(),
        "unassigned_count": q.filter(assigned_to__isnull=True, archived_at__isnull=True).exclude(status="closed").count(),
        "overdue_count": q.filter(status__in=["open", "pending"], due_at__lt=now,
                                 archived_at__isnull=True).count(),
        "snoozed_count": q.filter(snoozed_until__gt=now).count(),
        "received_count": letters.filter(direction="inbound").count(),
        "outbound_count": letters.filter(direction="outbound").count(),
        "submitted_count": letters.filter(status="submitted").count(),
        "attention_count": letters.filter(status__in=["failed", "uncertain"]).count(),
        "staff_load": q.filter(status__in=["open", "pending"], assigned_to__isnull=False,
                                archived_at__isnull=True).values(
            "assigned_to__username").annotate(total=models.Count("pk")).order_by("-total")[:15],
    })
    return render(request, "email_pro_reports.html", options)
