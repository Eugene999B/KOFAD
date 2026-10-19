"""Opt-in, minimized PostHog counters for KOFAD's public information pages.

The browser never contacts PostHog. No URL, IP, browser header, identity,
referrer, search string, cart, order, message or customer record is forwarded.
Each page view has a disposable random event ID, not a visitor profile.
"""
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import unquote

import requests
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from core.cookie_preferences import PUBLIC_PATHS

logger = logging.getLogger(__name__)
PUBLIC_HOSTS = {"kofadimpex.com", "www.kofadimpex.com", "market.kofadimpex.com"}
POSTHOG_ENDPOINT = "https://eu.i.posthog.com/i/v0/e/"
DAILY_CAP = 3000
CONSENT_MAX_AGE_MS = 180 * 86400000


def _has_analytics_consent(request):
    cookie_name = "__Host-kofad_consent" if request.is_secure() else "kofad_consent"
    raw = request.COOKIES.get(cookie_name, "")
    if len(raw) > 500:
        return False
    try:
        saved = json.loads(unquote(raw))
        if not isinstance(saved, dict):
            return False
        now_ms = time.time() * 1000
        age = now_ms - saved["savedAt"]
        return (
            saved.get("version") == 2
            and saved.get("analytics") is True
            and isinstance(saved["savedAt"], (int, float))
            and not isinstance(saved["savedAt"], bool)
            and 0 <= age < CONSENT_MAX_AGE_MS
        )
    except (TypeError, KeyError, ValueError, OverflowError):
        return False


def _reserve_daily_slot():
    """Best-effort safety cap; fail closed when the cache is unavailable."""
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    key = "kofad:posthog:public-pageviews:" + day
    try:
        if cache.add(key, 1, 86400):
            return True
        return cache.incr(key) <= DAILY_CAP
    except (ValueError, TypeError, ConnectionError):
        return False


@never_cache
@csrf_protect
@require_POST
def public_page_view(request):
    token = getattr(settings, "KOFAD_POSTHOG_PROJECT_TOKEN", "")
    if not (
        getattr(settings, "KOFAD_POSTHOG_ENABLED", False)
        and re.fullmatch(r"phc_[A-Za-z0-9]{20,120}", token)
    ):
        return HttpResponse(status=404)
    host = request.get_host().split(":")[0].lower()
    if host not in PUBLIC_HOSTS:
        return HttpResponse(status=404)
    if request.content_type != "application/json" or len(request.body) > 256:
        return JsonResponse({"error": "Invalid analytics request."}, status=400)
    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid analytics request."}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("path"), str) or body["path"] not in PUBLIC_PATHS:
        return JsonResponse({"error": "Unsupported public page."}, status=400)
    # Host and path must be an existing public page on that particular origin.
    if (body["path"] == "/market/") != (host == "market.kofadimpex.com"):
        return JsonResponse({"error": "Invalid website origin."}, status=400)
    # No consent means no provider request, event, identifiers or error details.
    if not _has_analytics_consent(request):
        return HttpResponse(status=204)
    if not _reserve_daily_slot():
        return HttpResponse(status=429)
    # No stable visitor identity is created; reports count pageviews, not users.
    payload = {
        "api_key": token,
        "event": "kofad public page viewed",
        "distinct_id": "one_time_" + secrets.token_hex(12),
        "properties": {
            "public_page": body["path"],
            "$process_person_profile": False,
            "$geoip_disable": True,
        },
    }
    try:
        response = requests.post(POSTHOG_ENDPOINT, json=payload, timeout=(2, 3))
    except requests.RequestException:
        logger.warning("KOFAD consented page event unavailable.")
        return HttpResponse(status=503)
    if response.status_code not in (200, 201, 202):
        logger.warning("KOFAD page event provider returned HTTP %s.", response.status_code)
        return HttpResponse(status=503)
    return HttpResponse(status=204)
