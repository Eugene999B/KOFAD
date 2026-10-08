"""Verified email login aliases and an opt-in SMTP notification outbox.

Identity is established only after a code reaches that mailbox, never from a
self-reported contact address. Password and existing phone/MFA checks remain.
"""
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from marketplace.models import CustomerAccount, EmailIdentity, EmailNotice

logger = logging.getLogger(__name__)


def normalize_email(value):
    address = (value or "").strip().casefold()
    if len(address) > 254:
        raise ValidationError("Enter a valid email address.")
    validate_email(address)
    return address


def delivery_ready():
    if not getattr(settings, "KOFAD_EMAIL_ENABLED", False):
        return False
    provider = getattr(settings, "KOFAD_EMAIL_PROVIDER", "gmail_api")
    if provider == "brevo":
        from .brevo_email import ready as brevo_ready
        return brevo_ready()
    if provider == "gmail_api":
        from .gmail_api import ready as gmail_ready
        return gmail_ready()
    if provider == "smtp":
        return bool(
            settings.EMAIL_HOST and settings.EMAIL_HOST_USER
            and settings.EMAIL_HOST_PASSWORD and settings.DEFAULT_FROM_EMAIL
        )
    return False


def _send_kofad_mail(subject, body, recipients, *, purpose="security"):
    """Send one email to each recipient through the selected verified provider."""
    provider = getattr(settings, "KOFAD_EMAIL_PROVIDER", "gmail_api")
    if provider == "brevo":
        from .brevo_email import send_brevo
        return sum(
            send_brevo(subject=subject, body=body, recipient=address, purpose=purpose)
            for address in recipients
        )
    if provider == "gmail_api":
        from .gmail_api import send_gmail
        return sum(
            send_gmail(subject=subject, body=body, recipient=address)
            for address in recipients
        )
    if provider == "smtp":
        return send_mail(
            subject, body, settings.DEFAULT_FROM_EMAIL, recipients,
            fail_silently=False,
        )
    raise ValidationError("KOFAD email provider is not configured.")


def _digest(kind, owner_id, email, code):
    return salted_hmac(
        "kofad-verify-email:v1",
        f"{kind}:{owner_id}:{email}:{code}",
        algorithm="sha256",
    ).hexdigest()


def verified_identity(kind, email):
    address = normalize_email(email)
    return EmailIdentity.objects.filter(
        kind=kind, email=address, verified_at__isnull=False,
    ).first()


def request_code(kind, owner_id, email):
    if kind not in {"staff", "customer"}:
        raise ValueError("Invalid email identity type.")
    address = normalize_email(email)
    if not delivery_ready():
        raise ValidationError("Email verification is being connected. Phone sign-in remains available.")
    now = timezone.now()
    code = f"{secrets.randbelow(1000000):06d}"
    with transaction.atomic():
        if EmailIdentity.objects.filter(
            kind=kind, email=address, verified_at__isnull=False
        ).exclude(owner_id=owner_id).exists():
            raise ValidationError("This email is already linked to another account.")
        identity, _ = EmailIdentity.objects.select_for_update().get_or_create(
            kind=kind, owner_id=owner_id,
        )
        if identity.last_sent_at and identity.last_sent_at > now - timedelta(seconds=60):
            raise ValidationError("Please wait a minute before requesting another code.")
        if not identity.sends_window_start or identity.sends_window_start <= now - timedelta(hours=1):
            identity.sends_window_start = now
            identity.sends_in_window = 0
        if identity.sends_in_window >= 3:
            raise ValidationError("Too many codes requested. Try again later.")
        identity.pending_email = address
        identity.code_digest = _digest(kind, owner_id, address, code)
        identity.requested_at = now
        identity.expires_at = now + timedelta(minutes=10)
        identity.last_sent_at = now
        identity.sends_in_window += 1
        identity.code_attempts = 0
        identity.save()
    try:
        _send_kofad_mail(
            "Verify your KOFAD email",
            f"Your KOFAD email verification code is {code}. It expires in 10 minutes. "
            "If you did not request it, ignore this message. Never share this code.",
            [address],
        )
    except Exception as exc:
        # Do not expose SMTP details or authentication secrets in the UI or logs.
        EmailIdentity.objects.filter(
            pk=identity.pk, code_digest=_digest(kind, owner_id, address, code)
        ).update(code_digest="", expires_at=now)
        logger.warning("KOFAD email verification delivery failed kind=%s", kind)
        raise ValidationError("We could not send the email right now. Please try later.") from exc
    return identity


def confirm_code(kind, owner_id, code):
    candidate = str(code or "").strip()
    if len(candidate) != 6 or not candidate.isascii() or not candidate.isdecimal():
        raise ValidationError("Enter the six-digit code from your email.")
    now = timezone.now()
    try:
        with transaction.atomic():
            identity = EmailIdentity.objects.select_for_update().get(kind=kind, owner_id=owner_id)
            if not identity.pending_email or not identity.code_digest or not identity.expires_at or identity.expires_at <= now:
                raise ValidationError("This code has expired. Request another.")
            if identity.code_attempts >= 5:
                raise ValidationError("Too many incorrect codes. Request another.")
            matched = constant_time_compare(
                identity.code_digest,
                _digest(kind, owner_id, identity.pending_email, candidate),
            )
            if not matched:
                identity.code_attempts += 1
                if identity.code_attempts >= 5:
                    identity.code_digest = ""
                    identity.expires_at = now
                identity.save(update_fields=["code_attempts", "code_digest", "expires_at"])
                return None
            if EmailIdentity.objects.filter(
                kind=kind, email=identity.pending_email, verified_at__isnull=False
            ).exclude(pk=identity.pk).exists():
                raise ValidationError("This email is already linked to another account.")
            identity.email = identity.pending_email
            identity.pending_email = ""
            identity.verified_at = now
            identity.code_digest = ""
            identity.expires_at = None
            identity.code_attempts = 0
            identity.save()
            # Contact address is only set after ownership has been proven.
            if kind == "staff":
                User.objects.filter(pk=owner_id).update(email=identity.email)
            else:
                CustomerAccount.objects.filter(pk=owner_id).update(email=identity.email)
            return identity
    except (EmailIdentity.DoesNotExist, IntegrityError) as exc:
        raise ValidationError("Email verification is unavailable. Request a new code.") from exc


def set_notifications(kind, owner_id, enabled):
    with transaction.atomic():
        identity = EmailIdentity.objects.select_for_update().filter(
            kind=kind, owner_id=owner_id, verified_at__isnull=False
        ).first()
        if not identity:
            raise ValidationError("Verify your email address before enabling notifications.")
        identity.notifications_enabled = bool(enabled)
        identity.save(update_fields=["notifications_enabled"])


def enqueue_notice(kind, owner_id, event_key, subject, body):
    """Idempotent and opt-in: does not send before SMTP is enabled."""
    if not getattr(settings, "KOFAD_EMAIL_NOTIFICATIONS_ENABLED", False):
        return None
    identity = EmailIdentity.objects.filter(
        kind=kind, owner_id=owner_id, verified_at__isnull=False,
        notifications_enabled=True,
    ).first()
    if not identity:
        return None
    notice, _ = EmailNotice.objects.get_or_create(
        event_key=event_key[:160],
        defaults={"email": identity.email, "subject": subject[:200], "body": body},
    )
    return notice


def enqueue_staff_payment_alerts(order):
    """Alert only opted-in staff authorised to view this branch's reports."""
    if not getattr(settings, "KOFAD_EMAIL_NOTIFICATIONS_ENABLED", False):
        return 0
    count = 0
    identities = EmailIdentity.objects.filter(
        kind="staff", verified_at__isnull=False, notifications_enabled=True,
    ).only("owner_id")
    for identity in identities:
        user = User.objects.filter(pk=identity.owner_id, is_active=True).first()
        if not user or not (user.is_superuser or (
            user.has_perm("core.view_reports")
            and user.access.branches.filter(pk=order.branch_id).exists()
        )):
            continue
        if enqueue_notice(
            "staff", user.pk, f"staff-order-paid:{user.pk}:{order.pk}",
            "KOFAD online payment confirmed",
            f"Verified online payment for {order.customer_reference}. "
            f"Amount: GHS {order.total:.2f}. Review the order from your private staff dashboard.",
        ):
            count += 1
    return count



def deliver_pending(limit=20):
    """Run from dedicated email worker/cron, not inside a customer HTTP request."""
    if not delivery_ready() or not getattr(settings, "KOFAD_EMAIL_NOTIFICATIONS_ENABLED", False):
        return 0
    now = timezone.now()
    delivered = 0
    keys = list(EmailNotice.objects.filter(
        status__in=["queued", "failed", "sending"], next_attempt_at__lte=now, attempts__lt=5,
    ).order_by("created_at").values_list("pk", flat=True)[:limit])
    for pk in keys:
        with transaction.atomic():
            claimed = EmailNotice.objects.filter(
                pk=pk, status__in=["queued", "failed", "sending"], next_attempt_at__lte=now,
                attempts__lt=5,
            ).update(
                status="sending", attempts=models.F("attempts") + 1,
                next_attempt_at=now + timedelta(minutes=5),
            )
        if not claimed:
            continue
        notice = EmailNotice.objects.get(pk=pk)
        try:
            _send_kofad_mail(notice.subject, notice.body, [notice.email], purpose="transaction")
        except Exception:
            # Retry only after a bounded delay; no credentials or email body in logs.
            EmailNotice.objects.filter(pk=pk).update(
                status="failed", next_attempt_at=timezone.now() + timedelta(minutes=min(60, 5 * notice.attempts))
            )
            logger.warning("KOFAD email notice could not be delivered")
        else:
            EmailNotice.objects.filter(pk=pk).update(
                status="sent", sent_at=timezone.now()
            )
            delivered += 1
    return delivered
