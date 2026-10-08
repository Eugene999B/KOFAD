import hmac
import json

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .whatsapp import ingest_webhook, signature_is_valid


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request):
    if request.method == "GET":
        expected = settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN
        supplied = request.GET.get("hub.verify_token", "")
        mode = request.GET.get("hub.mode", "")
        challenge = request.GET.get("hub.challenge", "")
        if (
            expected
            and mode == "subscribe"
            and supplied
            and hmac.compare_digest(supplied.encode(), expected.encode())
            and challenge
            and len(challenge) <= 2048
        ):
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponse("Webhook verification rejected.", status=403, content_type="text/plain")

    raw = request.body
    if len(raw) > settings.WHATSAPP_WEBHOOK_MAX_BYTES:
        return HttpResponse("Payload too large.", status=413, content_type="text/plain")
    if not settings.WHATSAPP_APP_SECRET:
        return HttpResponse("Webhook signature verification is not configured.", status=503, content_type="text/plain")
    if not signature_is_valid(raw, request.headers.get("X-Hub-Signature-256", "")):
        return HttpResponse("Invalid webhook signature.", status=403, content_type="text/plain")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponse("Invalid JSON.", status=400, content_type="text/plain")
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return HttpResponse("Unsupported webhook object.", status=400, content_type="text/plain")

    ingest_webhook(payload)
    return HttpResponse("EVENT_RECEIVED", content_type="text/plain")
