"""Permission-scoped shared email Trash, recoverable deletion, and explicit purge.

Deleting a message from a shared departmental mailbox affects other readers:
only reply-authorised staff can move selected mail to Trash or restore it.
Only the system administrator can delete all matching records or purge Trash.
Queued messages are cancelled, not silently delivered after removal.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .email_center import enabled, owner, visible_mailboxes
from .email_models import EmailDeliveryEvent, EmailLetter
from .services import audit

MAX_SELECTED = 100
MAX_ALL = 50000
CANCEL_STATUSES = ("queued", "failed", "draft")
AUDIT_RETAINED_MESSAGE = "Auto-generated or provider-tracked mail remains in Trash for audit."


def _filters(qs, data):
    direction = data.get("direction", "all")
    status = data.get("status", "all")
    q = str(data.get("q", "")).strip()[:100]
    valid_statuses = {key for key, _ in EmailLetter.STATUS}
    if direction not in {"all", "inbound", "outbound"}:
        raise ValidationError("Invalid mail direction.")
    if status not in valid_statuses | {"all", "attention"}:
        raise ValidationError("Invalid mail status.")
    if direction != "all":
        qs = qs.filter(direction=direction)
    if status == "attention":
        qs = qs.filter(status__in=["failed", "uncertain"])
    elif status != "all":
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(
            Q(subject__icontains=q) | Q(from_address__icontains=q)
            | Q(to_address__icontains=q) | Q(body_text__icontains=q)
        )
    return qs


def _selected(qs, data):
    raw = data.getlist("letter_ids")
    if not raw or len(raw) > MAX_SELECTED or any(not v.isdecimal() for v in raw):
        raise ValidationError("Select between 1 and 100 email records.")
    ids = {int(v) for v in raw}
    if 0 in ids or qs.filter(pk__in=ids).count() != len(ids):
        raise PermissionDenied("Some messages are unavailable in this mailbox or view.")
    return qs.filter(pk__in=ids)


def _confirmation(data, phrase):
    if data.get("confirmation", "").strip() != phrase:
        raise ValidationError(f"Type {phrase} to confirm this operation.")


def _safe_purge(qs):
    # Preserve automated accounting, debt/security notifications, and Brevo
    # delivery evidence even when a user empties Trash. Their active contents
    # stay hidden from all normal mailbox lists, not erased without audit.
    return qs.filter(Q(source_key__isnull=True) | Q(source_key="")).annotate(
        has_delivery_evidence=Exists(
            EmailDeliveryEvent.objects.filter(letter_id=OuterRef("pk"))
        )
    ).filter(has_delivery_evidence=False).exclude(status="sending")


def _return_to(data, mailbox_id, *, trash=False):
    page = "email_trash" if trash else "email_history"
    from urllib.parse import urlencode
    params = {"mailbox": mailbox_id}
    for field in ("q", "direction", "status"):
        value = data.get(field, "")
        if value:
            params[field] = str(value)[:100]
    return redirect(reverse(page) + "?" + urlencode(params))


@login_required
@require_POST
def action(request):
    if not enabled():
        raise Http404
    action_name = request.POST.get("action", "")
    mailbox = get_object_or_404(
        visible_mailboxes(request.user),
        pk=request.POST.get("mailbox_id"),
    )
    is_owner = owner(request.user)
    can_modify = is_owner or visible_mailboxes(request.user, send=True).filter(
        pk=mailbox.pk
    ).exists()
    if action_name not in {
        "trash_selected", "trash_all", "restore_selected",
        "purge_selected", "empty_trash",
    }:
        messages.error(request, "Unknown email deletion action.")
        return _return_to(request.POST, mailbox.pk)
    if action_name in {"trash_all", "purge_selected", "empty_trash"} and not is_owner:
        raise PermissionDenied("Only the system administrator can delete all or purge Trash.")
    if not can_modify:
        raise PermissionDenied("You need reply permission to move or restore messages.")
    is_trash = action_name in {"restore_selected", "purge_selected", "empty_trash"}
    qs = EmailLetter.objects.filter(
        mailbox=mailbox,
        trashed_at__isnull=not is_trash,
    )
    try:
        with transaction.atomic():
            if action_name in {"trash_selected", "restore_selected", "purge_selected"}:
                target = _selected(qs, request.POST)
            else:
                target = _filters(qs, request.POST) if not is_trash else qs
                if action_name == "trash_all":
                    _confirmation(request.POST, "DELETE ALL")
                else:
                    _confirmation(request.POST, "EMPTY TRASH")
                if target.count() > MAX_ALL:
                    raise ValidationError("Too many messages in this selection. Narrow your filters.")

            if action_name == "purge_selected":
                _confirmation(request.POST, "DELETE PERMANENTLY")
            count = target.count()
            if count == 0:
                raise ValidationError("No matching email records found.")
            if action_name in {"trash_selected", "trash_all"}:
                if target.filter(status="sending").exists():
                    raise ValidationError(
                        "A selected email is currently sending. Retry after delivery finishes; "
                        "nothing was moved to Trash."
                    )
                now = timezone.now()
                # Cancel queued/deferred messages before they are claimed by the
                # worker; restore never queues the old send again automatically.
                target.update(trashed_at=now, trashed_by=request.user)
                EmailLetter.objects.filter(
                    mailbox=mailbox, trashed_at=now, trashed_by=request.user,
                    status__in=CANCEL_STATUSES,
                ).update(
                    status="suppressed",
                    last_error="Moved to Trash; any pending send was cancelled.",
                )
                verb = "moved to Trash"
            elif action_name == "restore_selected":
                target.update(trashed_at=None, trashed_by=None)
                verb = "restored (cancelled messages remain cancelled)"
            else:
                eligible = _safe_purge(target)
                purged = eligible.count()
                eligible.delete()
                retained = count - purged
                audit(request.user, mailbox.branch, "email.trash_purge",
                      mailbox.pk, {"purged": purged, "retained_for_audit": retained})
                if retained:
                    messages.warning(
                        request,
                        f"{retained} automated or delivery-tracked message(s) are retained in "
                        "Trash for business audit and cannot be permanently erased here.",
                    )
                messages.success(request, f"Permanently deleted {purged} email record(s).")
                return _return_to(request.POST, mailbox.pk, trash=True)
            audit(request.user, mailbox.branch, f"email.{action_name}",
                  mailbox.pk, {"count": count, "mailbox": mailbox.address})
        messages.success(request, f"{count} email record(s) {verb}.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return _return_to(request.POST, mailbox.pk, trash=is_trash)


@login_required
@require_GET
def trash(request):
    if not enabled():
        raise Http404
    mailboxes = list(visible_mailboxes(request.user))
    chosen_id = request.GET.get("mailbox", "")
    mailbox = next((m for m in mailboxes if str(m.pk) == chosen_id), None)
    mailbox = mailbox or (mailboxes[0] if mailboxes else None)
    q = request.GET.get("q", "").strip()[:100]
    rows = EmailLetter.objects.filter(
        mailbox=mailbox, trashed_at__isnull=False,
    ) if mailbox else EmailLetter.objects.none()
    if q:
        rows = rows.filter(
            Q(subject__icontains=q) | Q(from_address__icontains=q)
            | Q(to_address__icontains=q)
        )
    page = Paginator(
        rows.select_related("created_by", "trashed_by")
        .order_by("-trashed_at", "-pk"), 20,
    ).get_page(request.GET.get("page", "1"))
    writable_ids = set(visible_mailboxes(request.user, send=True).values_list("pk", flat=True))
    return render(request, "email_pro_trash.html", {
        "title": "Email Trash", "email_nav": "trash",
        "email_is_owner": owner(request.user), "mailboxes": mailboxes,
        "selected": mailbox, "rows": page, "search": q, "writable_ids": writable_ids,
        "trash_count": rows.count(),
    })
