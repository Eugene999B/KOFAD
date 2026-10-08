import hashlib
import hmac
import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Message, WhatsAppAttempt, WhatsAppWebhookEvent
from .services import audit


STATUS_ORDER = {"sending": 0, "accepted": 1, "sent": 2, "delivered": 3, "read": 4}


def signature_is_valid(raw_body, signature):
    secret = settings.WHATSAPP_APP_SECRET
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    supplied = signature.split("=", 1)[1].strip()
    if len(supplied) != 64:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), supplied.encode())


def _fingerprint(entry_id, field, event_type, payload):
    canonical = json.dumps(
        {"entry": entry_id, "field": field, "type": event_type, "payload": payload},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _transition(current, incoming):
    current = str(current or "").lower()
    incoming = str(incoming or "").lower()
    if incoming == "failed":
        return current if current in {"delivered", "read"} else "failed"
    if incoming not in STATUS_ORDER:
        return current or incoming
    if current not in STATUS_ORDER:
        return incoming
    return incoming if STATUS_ORDER[incoming] >= STATUS_ORDER[current] else current


@transaction.atomic
def _reconcile_status(provider_id, status, payload):
    if not provider_id:
        return
    attempt = (
        WhatsAppAttempt.objects.select_for_update()
        .select_related("message", "message__branch")
        .filter(provider_id=provider_id)
        .order_by("-started_at")
        .first()
    )
    if not attempt:
        return
    next_status = _transition(attempt.status, status)
    attempt.status = next_status
    attempt.error_code = ""
    attempt.error_detail = ""
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if next_status == "failed" and isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        attempt.error_code = str(first.get("code") or "")[:80]
        attempt.error_detail = str(first.get("title") or first.get("message") or "")[:240]
    attempt.save(update_fields=["status", "error_code", "error_detail", "updated_at"])

    message = Message.objects.select_for_update().get(pk=attempt.message_id)
    message.status = _transition(message.status, next_status)
    if attempt.error_detail:
        message.last_error = attempt.error_detail
        message.save(update_fields=["status", "last_error"])
    else:
        message.save(update_fields=["status"])
    audit(
        None, message.branch, "whatsapp.status", message.pk,
        {"provider_id": provider_id, "status": message.status},
        category="communications", entity_type="message", entity_id=str(message.pk),
    )


@transaction.atomic
def ingest_webhook(payload):
    created = 0
    if not isinstance(payload, dict):
        return 0
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "")[:80]
        if settings.WHATSAPP_BUSINESS_ACCOUNT_ID and entry_id != settings.WHATSAPP_BUSINESS_ACCOUNT_ID:
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            field = str(change.get("field") or "")[:40]
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            phone_number_id = str(metadata.get("phone_number_id") or "")[:80]

            if field != "messages":
                continue
            if settings.WHATSAPP_PHONE_NUMBER_ID and phone_number_id != settings.WHATSAPP_PHONE_NUMBER_ID:
                continue
            messages = value.get("messages")
            for message in messages if isinstance(messages, list) else []:
                if not isinstance(message, dict):
                    continue
                fingerprint = _fingerprint(entry_id, field, "message", message)
                event, was_created = WhatsAppWebhookEvent.objects.get_or_create(
                    fingerprint=fingerprint,
                    defaults={
                        "waba_id": entry_id,
                        "phone_number_id": phone_number_id,
                        "event_type": "message",
                        "provider_message_id": str(message.get("id") or "")[:180],
                        "wa_id": str(message.get("from") or "")[:40],
                        "payload": message,
                    },
                )
                created += int(was_created)
                if was_created:
                    from .whatsapp_bot import receive
                    receive(event)

            statuses = value.get("statuses")
            for status in statuses if isinstance(statuses, list) else []:
                if not isinstance(status, dict):
                    continue
                provider_id = str(status.get("id") or "")[:180]
                state = str(status.get("status") or "")[:20]
                fingerprint = _fingerprint(entry_id, field, "status", status)
                _, was_created = WhatsAppWebhookEvent.objects.get_or_create(
                    fingerprint=fingerprint,
                    defaults={
                        "waba_id": entry_id,
                        "phone_number_id": phone_number_id,
                        "event_type": "status",
                        "provider_message_id": provider_id,
                        "wa_id": str(status.get("recipient_id") or "")[:40],
                        "status": state,
                        "payload": status,
                    },
                )
                if was_created:
                    created += 1
                    _reconcile_status(provider_id, state, status)
                    from .whatsapp_bot import reconcile_reply
                    reconcile_reply(provider_id, state)

            errors = value.get("errors") or []
            if errors:
                fingerprint = _fingerprint(entry_id, field, "error", errors)
                _, was_created = WhatsAppWebhookEvent.objects.get_or_create(
                    fingerprint=fingerprint,
                    defaults={
                        "waba_id": entry_id,
                        "phone_number_id": phone_number_id,
                        "event_type": "error",
                        "payload": {"errors": errors},
                    },
                )
                created += int(was_created)
    return created



def purge_webhook_evidence():
    """Delete stale raw Meta payloads after normalized delivery/support state is retained."""
    cutoff = timezone.now() - timedelta(days=max(int(settings.WHATSAPP_WEBHOOK_RETENTION_DAYS), 1))
    deleted, _ = WhatsAppWebhookEvent.objects.filter(received_at__lt=cutoff).delete()
    return deleted
