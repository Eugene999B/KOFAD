"""Cloudflare Turnstile verification for the public password and access gateways.

The public site key is rendered in templates. The secret remains in Railway
environment variables and is never exposed to the browser or application logs.
"""
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_HOSTNAMES = {
    "market": "market.kofadimpex.com",
    "staff": "staff.kofadimpex.com",
}


def verify_turnstile(request, *, kind, action):
    """Fail closed when enabled: a missing, replayed or wrong-host token cannot log in."""
    if not settings.KOFAD_TURNSTILE_ENABLED:
        return True
    if kind not in _HOSTNAMES:
        return False

    prefix = "KOFAD_TURNSTILE_" + kind.upper()
    sitekey = getattr(settings, prefix + "_SITE_KEY", "")
    secret = getattr(settings, prefix + "_SECRET_KEY", "")
    token = request.POST.get("cf-turnstile-response", "")
    if not sitekey or not secret or not isinstance(token, str) or not 1 <= len(token) <= 2048:
        return False

    payload = urlencode({"secret": secret, "response": token}).encode("utf-8")
    call = Request(
        SITEVERIFY_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(call, timeout=5) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        logger.warning("Cloudflare Turnstile verification unavailable (%s)", kind)
        return False

    return bool(
        isinstance(result, dict)
        and result.get("success") is True
        and result.get("hostname") == _HOSTNAMES[kind]
        and result.get("action") == action
    )
