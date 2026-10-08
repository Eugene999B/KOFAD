"""Meta Cloud API delivery with durable claims and conservative timeout handling."""
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import CommunicationSettings, Message, WhatsAppAttempt, WhatsAppWebhookEvent
from .services import audit, permit
from .sms.service import _recipient_is_current, validate_current_context


SETTLED = {"sending", "accepted", "sent", "delivered", "read", "unknown"}


def configuration_error(template_name=None):
    if not settings.WHATSAPP_ENABLED:
        return "WhatsApp Cloud delivery is disabled in deployment settings."
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        return "Add the WhatsApp access token and phone-number ID in Railway to enable Cloud delivery."
    if not settings.WHATSAPP_APP_SECRET:
        return "Configure the WhatsApp app secret so delivery callbacks can be verified."
    if not re.fullmatch(r"v[0-9]+\.[0-9]+", settings.WHATSAPP_GRAPH_VERSION):
        return "Configure a valid WhatsApp Graph API version."
    if not str(settings.WHATSAPP_PHONE_NUMBER_ID).isdigit():
        return "The WhatsApp phone-number ID must be numeric."
    if template_name is not None and not template_name:
        return "Choose a Meta-approved notification template before enabling automatic WhatsApp."
    return ""


def send_whatsapp(user, branch, message_id, *, automatic=False, retry=False):
    if not automatic:
        permit(user, branch, "send_messages")
    error = configuration_error()
    if error:
        raise ValidationError(error)
    policy = CommunicationSettings.objects.first() or CommunicationSettings()
    with transaction.atomic():
        message = Message.objects.select_for_update(of=("self",)).select_related(
            "party", "management_contact").get(pk=message_id, branch=branch, channel="whatsapp")
        if message.status in SETTLED:
            return message
        if message.status == "failed" and not retry:
            return message
        if not _recipient_is_current(message):
            raise ValidationError("This recipient has changed or is no longer eligible.")
        validate_current_context(message)
        recipient = message.recipient.lstrip("+")
        inbound_events = WhatsAppWebhookEvent.objects.filter(
            event_type="message", wa_id=recipient,
            phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
            received_at__gte=timezone.now() - timedelta(hours=24),
        ).values_list("payload", flat=True)
        now_seconds = timezone.now().timestamp()
        recent_inbound = False
        for event in inbound_events:
            try:
                age = now_seconds - float(event.get("timestamp", 0))
                if 0 <= age < 24 * 3600:
                    recent_inbound = True
                    break
            except (ValueError, TypeError, AttributeError):
                continue
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": recipient}
        template = ""
        if recent_inbound and not automatic:
            payload.update(type="text", text={"preview_url": False, "body": message.body})
        else:
            template = policy.whatsapp_template_name
            if not template:
                raise ValidationError("An approved WhatsApp template is required outside the 24-hour customer conversation window.")
            if len(message.body) > 1024:
                raise ValidationError("Keep template notifications within 1,024 characters.")
            payload.update(type="template", template={
                "name": template,
                "language": {"code": policy.whatsapp_template_language},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": message.body}]}],
            })
        message.attempts += 1
        message.status = "sending"
        message.provider = "whatsapp-cloud"
        message.sandbox = False
        message.last_error = ""
        message.submitted_by = user
        message.save(update_fields=["attempts", "status", "provider", "sandbox", "last_error", "submitted_by"])
        attempt = WhatsAppAttempt.objects.create(message=message, number=message.attempts,
                                                status="sending", template_name=template)

    # Never hold a database transaction while waiting for Meta.
    status, provider_id, detail, code, http_status = "unknown", "", "", "", None
    try:
        response = requests.post(
            f"https://graph.facebook.com/{settings.WHATSAPP_GRAPH_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
            headers={"Authorization": "Bearer " + settings.WHATSAPP_ACCESS_TOKEN},
            json=payload, timeout=settings.WHATSAPP_TIMEOUT_SECONDS, allow_redirects=False,
        )
        http_status = response.status_code
        result = response.json()
        if response.ok and result.get("messages"):
            provider_id = str(result["messages"][0].get("id") or "")
            status = "accepted" if provider_id else "unknown"
        elif response.status_code < 500:
            status = "failed"
            error = result.get("error") or {}
            code = str(error.get("code") or "provider_rejected")
            detail = str(error.get("message") or "WhatsApp rejected this message.")[:240]
        else:
            detail = "WhatsApp returned an uncertain result. Check delivery before retrying."
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
        detail = "WhatsApp delivery could not be confirmed. Check delivery before retrying."
    with transaction.atomic():
        attempt = WhatsAppAttempt.objects.select_for_update().get(pk=attempt.pk)
        message = Message.objects.select_for_update().get(pk=message.pk)
        attempt.status, attempt.provider_id = status, provider_id
        attempt.http_status, attempt.error_code, attempt.error_detail = http_status, code, detail
        attempt.save()
        message.status, message.last_error = status, detail
        message.save(update_fields=["status", "last_error"])
        audit(user, branch, "whatsapp.submitted", message.pk, {"status": status, "provider_id": provider_id})
    # A webhook can arrive before the response is saved. Reconcile already stored events.
    if provider_id:
        from .whatsapp import _reconcile_status
        for event in WhatsAppWebhookEvent.objects.filter(
                event_type="status", provider_message_id=provider_id).order_by("received_at"):
            _reconcile_status(provider_id, event.status, event.payload)
        message.refresh_from_db()
    return message


def recover_stale_whatsapp():
    cutoff = timezone.now() - timedelta(minutes=5)
    with transaction.atomic():
        attempts = WhatsAppAttempt.objects.select_for_update().filter(status="sending", started_at__lt=cutoff)
        for attempt in attempts:
            attempt.status = "unknown"
            attempt.error_detail = "Submission was interrupted. Check Meta delivery before retrying."
            attempt.save(update_fields=["status", "error_detail", "updated_at"])
            Message.objects.filter(pk=attempt.message_id, status="sending").update(
                status="unknown", last_error=attempt.error_detail)


def queue_whatsapp(user, branch, message_id, *, automatic=False, retry=False):
    """Persist delivery work so a slow provider cannot hold up checkout or closing."""
    if not automatic:
        permit(user, branch, "send_messages")
    error = configuration_error()
    if error:
        raise ValidationError(error)
    policy = CommunicationSettings.objects.first() or CommunicationSettings()
    if automatic and not policy.whatsapp_template_name:
        raise ValidationError("Choose an approved WhatsApp notification template first.")
    with transaction.atomic():
        message = Message.objects.select_for_update(of=("self",)).select_related(
            "party", "management_contact").get(pk=message_id, branch=branch, channel="whatsapp")
        if message.status in SETTLED or message.status == "queued":
            return message
        if message.status == "failed" and not retry:
            return message
        if not _recipient_is_current(message):
            raise ValidationError("This recipient has changed or is no longer eligible.")
        validate_current_context(message)
        if message.provider == "whatsapp-link":
            raise ValidationError("This item is a manual WhatsApp share. Compose a new Cloud message to send directly.")
        message.status = "queued"
        message.provider = "whatsapp-cloud"
        message.sandbox = False
        message.submitted_by = user
        message.last_error = ""
        message.save(update_fields=["status", "provider", "sandbox", "submitted_by", "last_error"])
        audit(user, branch, "whatsapp.queued", message.pk, {})
        return message


def process_whatsapp_queue():
    # One bounded provider call per worker pass keeps SMS tracking responsive.
    if configuration_error():
        return 0
    message = Message.objects.filter(channel="whatsapp", status="queued").select_related(
        "branch", "submitted_by", "created_by").order_by("created_at", "pk").first()
    if message is None:
        return 0
    actor = message.submitted_by or message.created_by
    try:
        send_whatsapp(actor, message.branch, message.pk, automatic=not message.manual_override)
    except (ValidationError, PermissionDenied) as exc:
        Message.objects.filter(pk=message.pk, status="queued").update(
            status="failed", last_error=str(exc)[:240])
    return 1
