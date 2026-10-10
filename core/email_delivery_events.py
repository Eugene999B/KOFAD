"""Authenticated Brevo delivery receipts; no user content or credentials are logged.

Brevo transactional webhooks can be configured with Bearer-token auth. Until a
private >=32-character token is configured, this endpoint accepts no events.
Events never cause a message to be resent.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models, transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .email_models import EmailDeliveryEvent, EmailLetter

EVENT_TYPES = {
    "request": "requested",
    "sent": "requested",
    "delivered": "delivered",
    "deferred": "deferred",
    "soft_bounce": "soft_bounce",
    "hard_bounce": "hard_bounce",
    "blocked": "blocked",
    "spam": "spam",
    "invalid_email": "invalid_email",
    "error": "error",
    "unsubscribed": "unsubscribed",
}
TAG_RE = re.compile(r"^kofad-letter-(\d{1,16})$")


def _provider_id(value):
    if not isinstance(value, str) or len(value) > 255:
        return ""
    return value.strip().strip("<>").casefold()


def _match_letter(payload, provider_id, address):
    tags = payload.get("tags", [])
    if isinstance(tags, list):
        for tag in tags[:10]:
            match = TAG_RE.fullmatch(tag) if isinstance(tag, str) else None
            if not match:
                continue
            candidate = EmailLetter.objects.filter(
                pk=int(match.group(1)), direction="outbound",
                status__in=["sending", "submitted", "uncertain"],
            ).first()
            if candidate and candidate.message_id and (
                _provider_id(candidate.message_id) != provider_id
            ):
                continue
            if candidate and address in {
                candidate.to_address.lower(),
                *candidate.cc_addresses.lower().split(","),
                *candidate.bcc_addresses.lower().split(","),
            }:
                return candidate
    # Older messages sent before tags existed can be matched via Brevo's ID.
    for row in EmailLetter.objects.filter(
        models.Q(message_id__iexact=provider_id)
        | models.Q(message_id__iexact="<" + provider_id + ">"),
        direction="outbound", status__in=["submitted", "uncertain"],
    )[:10]:
        if address in {
            row.to_address.lower(),
            *row.cc_addresses.lower().split(","),
            *row.bcc_addresses.lower().split(","),
        }:
            return row
    return None


@csrf_exempt
@require_POST
def brevo_delivery_callback(request):
    key = getattr(settings, "KOFAD_BREVO_WEBHOOK_TOKEN", "")
    if not isinstance(key, str) or len(key) < 32:
        return HttpResponse(status=503)
    header = request.headers.get("Authorization", "")
    expected = "Bearer " + key
    if not hmac.compare_digest(header.encode("utf-8")[:1024], expected.encode("utf-8")):
        return HttpResponse(status=403)
    if (request.content_type or "").lower() != "application/json":
        return HttpResponse(status=415)
    raw = request.body
    if not raw or len(raw) > 8192:
        return HttpResponse(status=413)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        return HttpResponse(status=400)
    if not isinstance(payload, dict):
        return HttpResponse(status=400)
    event = payload.get("event")
    if not isinstance(event, str) or event not in EVENT_TYPES:
        return JsonResponse({"accepted": True, "tracked": False})
    recipient = payload.get("email", "")
    provider_id = _provider_id(payload.get("message-id"))
    timestamp = payload.get("ts_event", payload.get("ts"))
    if not isinstance(recipient, str) or len(recipient) > 254 or not provider_id:
        return HttpResponse(status=400)
    recipient = recipient.strip().lower()
    try:
        validate_email(recipient)
        if isinstance(timestamp, bool) or not str(timestamp).isdigit():
            raise ValueError
        when = datetime.fromtimestamp(int(timestamp), tz=dt_timezone.utc)
    except (ValidationError, ValueError, OverflowError, OSError):
        return HttpResponse(status=400)
    now = timezone.now()
    if not now - timedelta(days=366) <= when <= now + timedelta(minutes=10):
        return HttpResponse(status=400)
    with transaction.atomic():
        letter = _match_letter(payload, provider_id, recipient)
        if not letter:
            # Do not create orphaned/untrusted associations or expose matches.
            return JsonResponse({"accepted": True, "tracked": False})
        locked = EmailLetter.objects.select_for_update().get(pk=letter.pk)
        signature = hashlib.sha256(
            f"{locked.pk}|{provider_id}|{recipient}|{event}|{int(timestamp)}".encode()
        ).hexdigest()
        _, created = EmailDeliveryEvent.objects.get_or_create(
            fingerprint=signature,
            defaults={
                "letter": locked, "provider_message_id": provider_id,
                "recipient": recipient, "event": event, "event_at": when,
            },
        )
        # A BCC/CC delivery event does not prove delivery to the main customer.
        if created and recipient == locked.to_address.lower():
            # Honor verified marketing unsubscribes and hard failures before
            # any queued campaign can reach the same customer again.
            if event in {"unsubscribed", "spam", "hard_bounce", "invalid_email"}:
                key_parts = (locked.source_key or "").split(":")
                if (len(key_parts) == 4 and key_parts[0] == "campaign"
                        and key_parts[2] == "customer"
                        and key_parts[1].isdigit() and key_parts[3].isdigit()):
                    from marketplace.models import EmailIdentity
                    EmailIdentity.objects.filter(
                        kind="customer", owner_id=int(key_parts[3]),
                        email__iexact=recipient, marketing_emails_enabled=True,
                    ).update(marketing_emails_enabled=False)
            newer = (
                locked.delivery_updated_at is None
                or when >= locked.delivery_updated_at
            )
            new_status = EVENT_TYPES[event]
            if newer and not (
                new_status == "requested" and locked.delivery_status != "unknown"
            ):
                locked.delivery_status = new_status
                locked.delivery_updated_at = when
                locked.save(update_fields=["delivery_status", "delivery_updated_at"])
    return JsonResponse({"accepted": True, "tracked": True})
