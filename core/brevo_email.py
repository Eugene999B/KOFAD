"""Transactional KOFAD email through Brevo HTTPS API (no SMTP sockets).

Requires an approved sender domain in Brevo and an API key stored only in
Railway secure variables. This does not create or host incoming mailboxes.
"""
import logging
import requests

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

logger = logging.getLogger(__name__)
API_URL = "https://api.brevo.com/v3/smtp/email"


def ready():
    return bool(
        getattr(settings, "KOFAD_BREVO_API_KEY", "")
        and getattr(settings, "KOFAD_BREVO_SECURITY_FROM_EMAIL", "")
        and getattr(settings, "KOFAD_BREVO_TRANSACTION_FROM_EMAIL", "")
    )


def send_brevo(*, subject, body, recipient, purpose="transaction"):
    """Send one plain-text message from an explicitly verified business role."""
    if not ready():
        raise ValidationError("Business email sending is not configured.")
    if purpose not in {"security", "transaction"}:
        raise ValueError("Invalid KOFAD business email purpose.")
    sender = (
        settings.KOFAD_BREVO_SECURITY_FROM_EMAIL if purpose == "security"
        else settings.KOFAD_BREVO_TRANSACTION_FROM_EMAIL
    )
    try:
        for address in (recipient, sender):
            validate_email(address)
        reply = getattr(settings, "KOFAD_SUPPORT_REPLY_TO_EMAIL", "")
        if reply:
            validate_email(reply)
        payload = {
            "sender": {"name": "KOFAD IMPEX ENTERPRISE", "email": sender},
            "to": [{"email": recipient}],
            "subject": subject,
            "textContent": body,
        }
        if reply:
            payload["replyTo"] = {"email": reply, "name": "KOFAD Support"}
        response = requests.post(
            API_URL,
            json=payload,
            headers={
                "api-key": settings.KOFAD_BREVO_API_KEY,
                "Content-Type": "application/json",
                "accept": "application/json",
            },
            timeout=15,
            allow_redirects=False,
        )
        response.raise_for_status()
        # Brevo returns HTTP 201 when the message is accepted for delivery.
        if response.status_code != 201:
            raise ValueError("Unexpected sender response status.")
        return 1
    except (requests.RequestException, ValueError, ValidationError) as exc:
        # Do not include customer recipient, message body or provider response
        # details in application logs.
        logger.warning("KOFAD Brevo API delivery unavailable; retry is managed by the outbox")
        raise ValidationError("Email sending is temporarily unavailable.") from exc
