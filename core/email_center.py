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

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, models, transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .email_models import EmailLetter, EmailMailbox, EmailMailboxMember

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


def _queue_external(mailbox, recipient, subject, body, user=None, *, source_key=None, reply_id=""):
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
                          in_reply_to=reply_id, next_attempt_at=timezone.now()),
        )
        return letter
    return EmailLetter.objects.create(
        mailbox=mailbox, direction="outbound", status="queued",
        from_address=mailbox.address, to_address=recipient, subject=subject,
        body_text=body, created_by=user, in_reply_to=reply_id,
        next_attempt_at=timezone.now(),
    )


def compose(mailbox, recipient, subject, body, user, *, reply_id=""):
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
            return _queue_external(mailbox, recipient, subject, body, user, reply_id=reply_id)
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
def inbox(request):
    if not enabled():
        raise Http404("Email Centre is not enabled yet.")
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
            elif action in {"send", "reply"}:
                mailbox = get_object_or_404(visible_mailboxes(request.user, send=True),
                                            pk=request.POST.get("mailbox_id"))
                reply_id = ""
                recipient = request.POST.get("to", "")
                subject = request.POST.get("subject", "")
                if action == "reply":
                    original = get_object_or_404(EmailLetter, pk=request.POST.get("reply_to"),
                                                 mailbox=mailbox, direction="inbound")
                    recipient = original.from_address
                    subject = "Re: " + original.subject[:250].removeprefix("Re: ")
                    reply_id = original.message_id
                created = compose(mailbox, recipient, subject, request.POST.get("body"), request.user,
                                  reply_id=reply_id)
                from .services import audit
                audit(request.user, mailbox.branch, "email.message_created", created.pk,
                      {"mailbox": mailbox.address, "direction": created.direction})
                messages.success(request, "Delivered internally." if created.status == "internal"
                                 else "Message queued for the email delivery service.")
            else:
                raise ValidationError("Unknown email action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("email_center")
    chosen_id = request.GET.get("mailbox", "")
    chosen = next((m for m in all_mailboxes if str(m.pk) == chosen_id), None)
    chosen = chosen or (all_mailboxes[0] if all_mailboxes else None)
    letters = (EmailLetter.objects.filter(mailbox=chosen).order_by("-created_at")[:75]
               if chosen else [])
    return render(request, "email_center.html", {
        "title": "Email Centre", "mailboxes": all_mailboxes, "selected": chosen,
        "letters": letters, "writable_ids": can_write,
        "memberships": EmailMailboxMember.objects.select_related("user", "mailbox").filter(
            mailbox__in=all_mailboxes).order_by("mailbox__address", "user__username")
        if owner(request.user) else [],
        "staff_users": User.objects.filter(is_active=True).order_by("username")[:300]
        if owner(request.user) else [],
        "is_mail_owner": owner(request.user),
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
        EmailLetter.objects.get_or_create(
            mailbox=mailbox, fingerprint=fingerprint,
            defaults={"direction": "inbound", "status": "received",
                      "from_address": from_address, "to_address": recipient,
                      "subject": subject, "body_text": content[:MAX_BODY_CHARS],
                      "message_id": message_id, "in_reply_to": reply_id,
                      "had_attachments": attachments},
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
            ).update(status="sending", attempts=models.F("attempts") + 1)
        if not claimed:
            continue
        row = EmailLetter.objects.get(pk=pk)
        try:
            from .brevo_email import send_brevo
            send_brevo(subject=row.subject, body=row.body_text,
                       recipient=row.to_address, purpose="transaction",
                       sender_email=row.from_address)
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
