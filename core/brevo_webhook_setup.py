"""Opt-in, idempotent Brevo transactional delivery webhook registration.

Run by the communications worker, never by migrations or payment code.
Sensitive credentials, provider responses and recipient data are not logged.
"""
import hmac

import requests
from django.conf import settings

URL = "https://api.brevo.com/v3/webhooks"
CALLBACK = "https://staff.kofadimpex.com/email/events/brevo/"
EVENTS = frozenset({
    "sent", "delivered", "hardBounce", "softBounce", "blocked",
    "spam", "invalid", "deferred", "unsubscribed",
})


def ensure_delivery_webhook():
    """Return an opaque setup status, and never weaken an existing webhook."""
    token = getattr(settings, "KOFAD_BREVO_WEBHOOK_TOKEN", "")
    api_key = getattr(settings, "KOFAD_BREVO_API_KEY", "")
    if not (getattr(settings, "KOFAD_BREVO_WEBHOOK_AUTO_REGISTER", False)
            and getattr(settings, "KOFAD_EMAIL_PROVIDER", "") == "brevo"
            and isinstance(token, str) and len(token) >= 32
            and isinstance(api_key, str) and len(api_key) >= 20):
        return "disabled"

    headers = {"api-key": api_key, "Accept": "application/json"}
    try:
        result = requests.get(
            URL, params={"type": "transactional"}, headers=headers,
            timeout=10, allow_redirects=False,
        )
        if result.status_code != 200:
            return "unavailable"
        records = result.json().get("webhooks", [])
        if not isinstance(records, list):
            return "unavailable"
        for entry in records:
            if not isinstance(entry, dict) or entry.get("url") != CALLBACK:
                continue
            authorization = entry.get("auth") or {}
            if not isinstance(authorization, dict):
                return "requires_review"
            existing_token = authorization.get("token")
            enabled_events = entry.get("events", [])
            if (authorization.get("type") == "bearer"
                    and isinstance(existing_token, str)
                    and hmac.compare_digest(existing_token, token)
                    and isinstance(enabled_events, list)
                    and EVENTS.issubset(set(enabled_events))):
                return "ready"
            # Never overwrite an existing provider configuration silently.
            return "requires_review"

        created = requests.post(
            URL,
            json={
                "type": "transactional", "channel": "email",
                "description": "KOFAD verified transactional delivery receipts",
                "url": CALLBACK, "events": sorted(EVENTS),
                "auth": {"type": "bearer", "token": token},
                "batched": False,
            },
            headers={**headers, "Content-Type": "application/json"},
            timeout=10, allow_redirects=False,
        )
        return "created" if created.status_code == 201 else "unavailable"
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        # Provider network failures are retried at a low frequency. No
        # attempted registration can send an email or change email settings.
        return "unavailable"
