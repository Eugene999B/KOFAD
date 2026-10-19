"""Optional Upstash Redis REST service for shared, atomic metered API budgets.

No customer IPs, identities, secrets, response bodies or payment data are sent.
The module makes no network calls on import. Activation requires all three
explicit settings and a valid Upstash HTTPS endpoint.
"""
import re
from urllib.parse import urlsplit

import requests
from django.conf import settings


class UpstashUnavailable(RuntimeError):
    """The optional shared counter provider is not usable."""


TIMEOUT = (2, 4)
TTL_SECONDS = 75
# One EVAL is atomic across both keys. A non-atomic REST pipeline must not
# be used for limits that are intended to apply to every Django process.
MAP_BUDGET_SCRIPT = """
local global = redis.call('INCR', KEYS[1])
if global == 1 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3])) end
if global > tonumber(ARGV[1]) then return 0 end
local session = redis.call('INCR', KEYS[2])
if session == 1 then redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3])) end
if session > tonumber(ARGV[2]) then return 0 end
return 1
""".strip()


def enabled():
    return bool(getattr(settings, "KOFAD_UPSTASH_REST_ENABLED", False))


def _credentials(*, require_enabled=True):
    if require_enabled and not enabled():
        raise UpstashUnavailable("Upstash is disabled.")
    endpoint = getattr(settings, "KOFAD_UPSTASH_REST_URL", "").strip()
    token = getattr(settings, "KOFAD_UPSTASH_REST_TOKEN", "").strip()
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        valid = (
            parsed.scheme == "https"
            and bool(host)
            and parsed.netloc == host  # Reject userinfo, ports and obfuscated authority.
            and parsed.path in ("", "/")
            and not parsed.query and not parsed.fragment
            and bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*\.upstash\.io", host))
        )
    except ValueError:
        valid = False
    if not valid or not token:
        raise UpstashUnavailable("Upstash credentials are not configured.")
    return "https://" + host, token


def configured():
    try:
        _credentials(require_enabled=False)
        return True
    except UpstashUnavailable:
        return False


def _command(parts, *, require_enabled=True):
    endpoint, token = _credentials(require_enabled=require_enabled)
    try:
        response = requests.post(
            endpoint, json=parts, timeout=TIMEOUT, allow_redirects=False,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        )
        if response.status_code != 200:
            raise UpstashUnavailable("Upstash REST returned a non-success status.")
        result = response.json()
    except (requests.RequestException, ValueError):
        raise UpstashUnavailable("Upstash REST is unavailable.") from None
    if not isinstance(result, dict) or "error" in result or "result" not in result:
        raise UpstashUnavailable("Upstash REST returned an invalid response.")
    return result["result"]


def ping():
    """Live authenticated read-only check, never called during page render."""
    if _command(["PING"], require_enabled=False) != "PONG":
        raise UpstashUnavailable("Upstash PING was not acknowledged.")
    return True


def consume_map_budget(session_hash, minute, global_limit, session_limit):
    """Atomically enforce the deployment-wide and per-session minute quotas.

    The caller supplies a SHA-256 HMAC of the Django session key, never the
    session cookie itself. Provider errors fail closed for paid map lookups.
    """
    if not isinstance(session_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", session_hash):
        raise UpstashUnavailable("Invalid anonymous session key.")
    if type(minute) is not int or not 0 <= minute < 1_000_000_000:
        raise UpstashUnavailable("Invalid budget window.")
    if not all(type(x) is int and 1 <= x <= 1_000_000 for x in (global_limit, session_limit)):
        raise UpstashUnavailable("Invalid budget limit.")
    response = _command([
        "EVAL", MAP_BUDGET_SCRIPT, "2",
        f"kofad:map:global:{minute}",
        f"kofad:map:session:{session_hash}:{minute}",
        str(global_limit), str(session_limit), str(TTL_SECONDS),
    ])
    if type(response) is not int or response not in (0, 1):
        raise UpstashUnavailable("Invalid atomic budget response.")
    return response == 1
