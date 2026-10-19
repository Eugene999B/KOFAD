"""Owner-initiated IPinfo Lite country/ASN lookups; never part of login decisions.

Only explicitly supplied global IPv4/IPv6 addresses are accepted. Provider data
is whitelisted; the queried IP, response body and bearer token are not logged
or persisted. Cached network context expires after one hour and uses a keyed
digest, never the raw IP address, as its cache key.
"""
import hashlib
import hmac
import ipaddress
import re

import requests
from django.conf import settings
from django.core.cache import cache


class InvalidPublicIP(ValueError):
    """A supplied IP address is malformed or not globally routable."""


class IPinfoUnavailable(RuntimeError):
    """The IPinfo provider is disabled, unavailable or returned bad data."""


FIELDS = ("country_code", "country", "continent_code", "continent",
          "asn", "as_name", "as_domain")
CACHE_SECONDS = 60 * 60
TIMEOUT = (2, 4)


def is_configured():
    return bool(
        getattr(settings, "KOFAD_IPINFO_LITE_ENABLED", False)
        and getattr(settings, "KOFAD_IPINFO_LITE_TOKEN", "")
    )


def public_address(value):
    """Validate an address, not a URL/hostname, before interpolating the path."""
    if not isinstance(value, str) or len(value) > 45 or not value.strip():
        raise InvalidPublicIP("Enter a valid public IPv4 or IPv6 address.")
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        raise InvalidPublicIP("Enter a valid public IPv4 or IPv6 address.") from None
    if not address.is_global:
        raise InvalidPublicIP("Only publicly routable IP addresses may be inspected.")
    return address


def _safe_text(value, *, maximum=100):
    if not isinstance(value, str):
        return ""
    cleaned = "".join(char for char in value if char.isprintable()).strip()
    return cleaned[:maximum]


def _normalise(data, address):
    if not isinstance(data, dict):
        raise IPinfoUnavailable("Provider returned an unexpected response.")
    try:
        if ipaddress.ip_address(data.get("ip", "")) != address:
            raise IPinfoUnavailable("Provider returned a different IP address.")
    except (ValueError, TypeError):
        raise IPinfoUnavailable("Provider returned an unexpected response.") from None
    result = {field: _safe_text(data.get(field)) for field in FIELDS}
    if not re.fullmatch(r"[A-Z]{2}", result["country_code"]):
        raise IPinfoUnavailable("Provider returned incomplete country data.")
    if result["asn"] and not re.fullmatch(r"AS[0-9]{1,12}", result["asn"]):
        result["asn"] = ""
    return result


def lookup_public_ip(value, *, use_cache=True):
    """Return bounded Lite metadata, or an intentionally non-sensitive error."""
    address = public_address(value)
    if not is_configured():
        raise IPinfoUnavailable("IPinfo Lite is not configured.")
    # The cache key is a one-way keyed hash, so a cache diagnostic will not
    # reveal queried IPs. The cached value is country/ASN metadata only.
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        address.compressed.encode("ascii"), hashlib.sha256,
    ).hexdigest()
    key = "kofad:ipinfo-lite:v1:" + digest
    try:
        cached = cache.get(key) if use_cache else None
        if isinstance(cached, dict) and set(cached) == set(FIELDS):
            return cached
    except Exception:
        pass  # Cache outages must not make an optional lookup fail.

    try:
        response = requests.get(
            "https://api.ipinfo.io/lite/" + address.compressed,
            headers={
                "Authorization": "Bearer " + settings.KOFAD_IPINFO_LITE_TOKEN,
                "Accept": "application/json",
            },
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException:
        raise IPinfoUnavailable("IPinfo Lite could not be reached.") from None
    if response.status_code != 200:
        raise IPinfoUnavailable("IPinfo Lite rejected the request or is unavailable.")
    try:
        result = _normalise(response.json(), address)
    except (ValueError, TypeError):
        raise IPinfoUnavailable("IPinfo Lite returned an invalid response.") from None
    try:
        cache.set(key, result, CACHE_SECONDS)
    except Exception:
        pass
    return result
