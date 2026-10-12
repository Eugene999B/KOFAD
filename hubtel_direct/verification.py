"""Hubtel wallet registration/name inquiry, with no money movement.

Reference: Verification API / Mobile Money Registration & Username Query.
The account holder name is provider-supplied and should never be invented.
"""
import hashlib

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError

from core.identity import normalize_ghana_phone
from . import client

VERIFY_ORIGIN = "https://rnv.hubtel.com"


def inquiry(phone, channel):
    client.require_enabled("receive")
    if channel not in client.CHANNELS:
        raise ValidationError("Choose MTN, Telecel, or AirtelTigo.")
    msisdn = normalize_ghana_phone(phone).lstrip("+")
    url = (
        f"{VERIFY_ORIGIN}/merchantaccount/merchants/"
        f"{settings.HUBTEL_COLLECTION_ACCOUNT}/mobilemoney/verify"
    )
    try:
        response = requests.get(
            url,
            params={"channel": channel, "customerMsisdn": msisdn},
            headers=client._headers(),
            timeout=min(settings.HUBTEL_TIMEOUT_SECONDS, 12),
            allow_redirects=False,
        )
        if response.status_code in {401, 403}:
            raise ValidationError(
                "Hubtel wallet verification is not authorized. Ask Hubtel to enable the verification API scope and IP access."
            )
        if not 200 <= response.status_code < 300:
            raise ValidationError("Hubtel could not verify this wallet. No payment request was sent.")
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError("Hubtel wallet verification is temporarily unavailable. No payment request was sent.") from exc
    if not isinstance(body, dict):
        raise ValidationError("Hubtel returned an invalid wallet verification response.")
    code = str(client._field(body, "ResponseCode") or "")
    data = client._field(body, "Data")
    if code != "0000" or not isinstance(data, dict):
        raise ValidationError("Hubtel could not confirm this Mobile Money wallet.")
    registered = client._field(data, "IsRegistered")
    name = client._field(data, "Name")
    status = str(client._field(data, "Status") or "").strip().lower()
    if registered is not True or not isinstance(name, str) or not 2 <= len(name.strip()) <= 140:
        raise ValidationError("This Mobile Money wallet could not be verified.")
    if status and status not in {"active", "registered"}:
        raise ValidationError("This Mobile Money wallet is not active.")
    return {"phone": msisdn, "channel": channel, "wallet_name": name.strip(), "registered": True}


def staff_throttle(user_id, phone):
    """Global app-cache throttling; never store or log the full phone in cache keys."""
    digest = hashlib.sha256(str(phone).encode()).hexdigest()[:16]
    key = f"hubtel-verify-staff:{user_id}"
    phone_key = f"hubtel-verify-phone:{digest}"
    for current, limit in ((key, 20), (phone_key, 8)):
        if cache.add(current, 1, timeout=3600):
            continue
        try:
            count = cache.incr(current)
        except ValueError:
            cache.set(current, 1, timeout=3600)
            count = 1
        if count > limit:
            raise ValidationError("Too many wallet verification requests. Try again later.")
