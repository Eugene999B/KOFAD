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
    # Name-only inquiry uses verified collection credentials and whitelisted web egress.
    # A disabled payment-collection switch must not disable harmless name verification.
    client.require_reconciliation_ready("receive")
    if channel not in client.CHANNELS:
        raise ValidationError("Choose MTN, Telecel, or AirtelTigo.")
    msisdn = normalize_ghana_phone(phone).lstrip("+")
    url = (
        f"{VERIFY_ORIGIN}/v2/merchantaccount/merchants/"
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
                "Wallet-name verification is not enabled for this business account. Ask an administrator to review service access."
            )
        if not 200 <= response.status_code < 300:
            raise ValidationError("Could not verify this wallet. No payment request was sent.")
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError("Wallet-name verification is temporarily unavailable. No payment request was sent.") from exc
    if not isinstance(body, dict):
        raise ValidationError("The wallet verification service returned an invalid response.")
    code = str(client._field(body, "ResponseCode") or "")
    data = client._field(body, "Data")
    if code != "0000" or not isinstance(data, dict):
        raise ValidationError("The wallet verification service could not confirm this number.")
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


def bank_inquiry(bank_code, bank_number):
    """Registered bank-account name from Hubtel, not a guessed payroll alias."""
    # Name-only inquiry uses verified collection credentials and whitelisted web egress.
    # A disabled payment-collection switch must not disable harmless name verification.
    client.require_reconciliation_ready("receive")  # API keys and only web service's IP.
    bank_code = str(bank_code or "")
    number = str(bank_number or "").strip()
    if bank_code not in client.BANK_CODES or not number.isdigit() or not 4 <= len(number) <= 30:
        raise ValidationError("Enter a listed Ghana bank and valid account number.")
    url = (
        f"{VERIFY_ORIGIN}/v2/merchantaccount/merchants/"
        f"{settings.HUBTEL_COLLECTION_ACCOUNT}/bank/verify/{bank_code}/{number}"
    )
    try:
        response = requests.get(
            url, headers=client._headers(),
            timeout=min(settings.HUBTEL_TIMEOUT_SECONDS, 12),
            allow_redirects=False,
        )
        if response.status_code in (401, 403):
            raise ValidationError("Bank account verification is not enabled. Ask an administrator to review service access.")
        if not 200 <= response.status_code < 300:
            raise ValidationError("The bank verification service could not confirm this account.")
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError("Bank verification is temporarily unavailable.") from exc
    if not isinstance(body, dict) or str(client._field(body, "responseCode")) != "0000":
        raise ValidationError("The bank account could not be verified.")
    data = client._field(body, "data")
    name = client._field(data, "name")
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 140:
        raise ValidationError("The bank verification service did not return a registered account name.")
    return {"bank_code": bank_code, "account_suffix": number[-4:], "name": name.strip(), "verified": True}
