"""CSRF-protected public KOFAD AI chat endpoint; no customer data to AI."""
import hashlib
import json
import logging
from datetime import datetime, timezone

from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import services
from .kofad_ai import answer, free_configured, reserve_free_request

log = logging.getLogger(__name__)


def _quota(key, ceiling, period):
    """Best-effort distributed cache throttle, fail closed when cache fails."""
    try:
        inserted = cache.add(key, 1, period)
        if inserted:
            return True
        return cache.incr(key) <= ceiling
    except (ValueError, TypeError, ConnectionError):
        return False


@never_cache
@require_POST
def message(request):
    if request.content_type != "application/json" or len(request.body) > 4096:
        return JsonResponse({"error": "Unsupported chat request."}, status=400)
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid message."}, status=400)
    if not isinstance(data, dict) or not isinstance(data.get("message"), str):
        return JsonResponse({"error": "Please enter your question."}, status=400)
    if len(data["message"]) > 600:
        return JsonResponse({"error": "Keep your question under 600 characters."}, status=400)
    customer = services.customer_from_session(request)
    if not request.session.session_key:
        request.session.create()
    from django.conf import settings
    ip = (request.META.get("REMOTE_ADDR") or "unknown").strip()[:60]
    identity_hash = hashlib.sha256(
        (settings.SECRET_KEY + ":kofad-ai:" + str(request.session.session_key)).encode()
    ).hexdigest()[:20]
    ip_hash = hashlib.sha256(
        (settings.SECRET_KEY + ":kofad-ai-ip:" + ip).encode()
    ).hexdigest()[:20]
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    if not (
        _quota(f"kofad:ai:session:{identity_hash}:{day}", 80 if customer else 40, 86400)
        and _quota(f"kofad:ai:source:{ip_hash}:{day}", 120, 86400)
    ):
        return JsonResponse({
            "error": "This chat has reached its daily request limit. Please use KOFAD Market or customer support."
        }, status=429)
    # Explicit user consent before forwarding PUBLIC questions to the free provider.
    consent = data.get("ai_consent") is True
    active = free_configured()
    ai_allowed = bool(consent and active)
    mode = "consent_required" if active and not consent else "offline"
    if ai_allowed:
        # Telegram and website share one zero-cost allowance. After the
        # cap is reached, continue helping locally rather than charging.
        ai_allowed = reserve_free_request("web", identity_hash, day)
        mode = "ready" if ai_allowed else "daily_limit"
    reply, source = answer(
        data["message"], customer=customer, ai_allowed=ai_allowed,
        history=data.get("history") if isinstance(data.get("history"), list) else None,
    )
    response = JsonResponse({
        "reply": reply, "source": source,
        "ai_mode": ("free" if source == "free-ai" else
                    "private" if source == "private" else
                    "provider_unavailable" if mode == "ready" else mode),
        "ai_active": active,
        "customer_connected": bool(customer),
    })
    response["Cache-Control"] = "no-store, private"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
