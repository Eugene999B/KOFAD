"""KOFAD business email via Brevo HTTPS, with a conservative daily send cap."""
import logging
import requests

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)
API_URL = "https://api.brevo.com/v3/smtp/email"


class DailyEmailLimitExceeded(ValidationError):
    pass


class DefiniteEmailRejection(ValidationError):
    """Brevo returned an explicit non-accepted HTTP status; no acceptance was recorded."""


class UncertainEmailDelivery(Exception):
    """A timeout after request submission may still have delivered the email."""


def daily_limit():
    return min(300, max(1, int(getattr(settings, "KOFAD_BREVO_DAILY_LIMIT", 300))))


def ready():
    return bool(
        getattr(settings, "KOFAD_BREVO_API_KEY", "")
        and getattr(settings, "KOFAD_BREVO_SECURITY_FROM_EMAIL", "")
        and getattr(settings, "KOFAD_BREVO_TRANSACTION_FROM_EMAIL", "")
    )


def _reserve():
    from .email_models import EmailDailyUsage
    with transaction.atomic():
        row, _ = EmailDailyUsage.objects.get_or_create(day=timezone.localdate())
        row = EmailDailyUsage.objects.select_for_update().get(pk=row.pk)
        if row.attempted >= daily_limit():
            raise DailyEmailLimitExceeded("KOFAD daily outgoing email allowance has been reached.")
        row.attempted += 1
        row.save(update_fields=["attempted"])
        return row.pk


def usage_today():
    from .email_models import EmailDailyUsage
    today = timezone.localdate()
    row = EmailDailyUsage.objects.filter(day=today).first()
    attempted = row.attempted if row else 0
    return {
        "date": today, "limit": daily_limit(), "attempted": attempted,
        "accepted": row.accepted if row else 0,
        "failed": row.failed if row else 0,
        "remaining": max(0, daily_limit() - attempted),
    }


def send_brevo(*, subject, body, recipient, purpose="transaction", sender_email=None,
               return_message_id=False):
    if not ready():
        raise ValidationError("Business email sending is not configured.")
    if purpose not in {"security", "transaction"}:
        raise ValueError("Invalid KOFAD business email purpose.")
    requested_sender = sender_email or (
        settings.KOFAD_BREVO_SECURITY_FROM_EMAIL if purpose == "security"
        else settings.KOFAD_BREVO_TRANSACTION_FROM_EMAIL
    )
    for address in (recipient, requested_sender):
        validate_email(address)
    if not requested_sender.lower().endswith("@kofadimpex.com"):
        raise ValidationError("Unverified KOFAD business sender.")
    # A domain can be authenticated while individual Brevo senders remain
    # unregistered. Use the explicitly verified shared sender until each
    # department is registered; preserve the department as Reply-To.
    verified = {
        x.strip().lower()
        for x in getattr(
            settings, "KOFAD_BREVO_REGISTERED_SENDERS",
            settings.KOFAD_BREVO_TRANSACTION_FROM_EMAIL,
        ).split(",") if x.strip()
    }
    sender = requested_sender if requested_sender.lower() in verified else settings.KOFAD_BREVO_TRANSACTION_FROM_EMAIL
    if sender.lower() not in verified or not sender.lower().endswith("@kofadimpex.com"):
        raise ValidationError("A verified KOFAD email sender is required.")
    reply = getattr(settings, "KOFAD_SUPPORT_REPLY_TO_EMAIL", "")
    if reply:
        validate_email(reply)
    if not isinstance(body, str) or len(body) > 100000 or len(subject) > 255:
        raise ValidationError("Email content is too long.")

    # Reserve before calling Brevo: concurrent services cannot exceed our cap.
    # Attempts remain counted on timeouts because delivery status is unknown.
    pk = _reserve()
    from .email_models import EmailDailyUsage
    payload = {
        "sender": {"name": "KOFAD IMPEX ENTERPRISE", "email": sender},
        "to": [{"email": recipient}], "subject": subject, "textContent": body,
        "replyTo": {"email": requested_sender if requested_sender != sender else (reply or sender)},
    }
    try:
        response = requests.post(
            API_URL, json=payload,
            headers={"api-key": settings.KOFAD_BREVO_API_KEY,
                     "Content-Type": "application/json", "accept": "application/json"},
            timeout=15, allow_redirects=False,
        )
    except requests.Timeout as exc:
        logger.warning("KOFAD email provider timeout; status uncertain")
        raise UncertainEmailDelivery from exc
    except requests.RequestException as exc:
        EmailDailyUsage.objects.filter(pk=pk).update(failed=models.F("failed") + 1)
        raise ValidationError("Email sending temporarily unavailable.") from exc
    if response.status_code != 201:
        EmailDailyUsage.objects.filter(pk=pk).update(failed=models.F("failed") + 1)
        raise DefiniteEmailRejection("Email provider did not accept the message.")
    EmailDailyUsage.objects.filter(pk=pk).update(accepted=models.F("accepted") + 1)
    if return_message_id:
        try:
            provider_id = str(response.json().get("messageId") or "")[:255]
        except (TypeError, ValueError, AttributeError):
            provider_id = ""
        return provider_id
    return 1
