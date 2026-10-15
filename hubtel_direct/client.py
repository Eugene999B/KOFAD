"""Isolated, fail-closed Hubtel Direct Money API contract (September 2026).

No API request happens unless feature gates, collection/disbursement account,
static egress confirmation and server-only credentials are configured.
A lost POST response is NEVER retried; reconcile by saved ClientReference.
"""
import base64
import hashlib
import os
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError

RECEIVE_ORIGIN = "https://rmp.hubtel.com"
SEND_ORIGIN = "https://smp.hubtel.com"
RECEIVE_STATUS_ORIGIN = "https://api-txnstatus.hubtel.com"
SEND_STATUS_ORIGIN = "https://smrsc.hubtel.com"
CHANNELS = frozenset(("mtn-gh", "vodafone-gh", "tigo-gh"))
BANK_CODES = frozenset((
    "300302", "300303", "300304", "300305", "300306", "300307", "300309",
    "300310", "300311", "300312", "300313", "300316", "300317", "300318",
    "300319", "300320", "300322", "300323", "300324", "300325", "300329",
    "300331", "300334", "300362",
))


class SubmissionUnknown(Exception):
    """Provider might have accepted the request. Do not submit again."""


class ProviderRejected(Exception):
    def __init__(self, code):
        self.code = str(code)[:20]
        super().__init__("Hubtel declined the request.")


class StatusUnconfirmed(Exception):
    """Not a verified, final provider state."""


def money(value):
    try:
        result = Decimal(str(value))
        if (not result.is_finite() or result <= 0 or result > Decimal("999999999999.99")
                or result != result.quantize(Decimal("0.01"))):
            raise ValueError
        return result.quantize(Decimal("0.01"))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ValidationError("Enter a positive amount to two decimal places.") from exc


def seal(value):
    key = base64.urlsafe_b64encode(hashlib.sha256(
        (settings.SECRET_KEY + "|kofad-hubtel-direct-destination-v1").encode()
    ).digest())
    return Fernet(key).encrypt(str(value).encode()).decode()


def unseal(value):
    key = base64.urlsafe_b64encode(hashlib.sha256(
        (settings.SECRET_KEY + "|kofad-hubtel-direct-destination-v1").encode()
    ).digest())
    return Fernet(key).decrypt(value.encode()).decode()


def whitelisted_web_process():
    """Never send Hubtel HTTP from a different Railway service's outbound IP.

    Railway injects RAILWAY_SERVICE_NAME. Local/unit-test processes without
    a Railway identity continue to rely on explicit feature/egress gates.
    """
    deployed = os.environ.get("RAILWAY_SERVICE_NAME", "").strip().lower()
    return not deployed or deployed == "kofad-web"


def enabled(kind):
    flag = {
        "receive": "HUBTEL_DIRECT_RECEIVE_ENABLED",
        "send_momo": "HUBTEL_DIRECT_SEND_MOMO_ENABLED",
        "send_bank": "HUBTEL_DIRECT_SEND_BANK_ENABLED",
    }.get(kind)
    account = (settings.HUBTEL_COLLECTION_ACCOUNT if kind == "receive"
               else getattr(settings, "HUBTEL_DISBURSEMENT_ACCOUNT", ""))
    return bool(
        flag and getattr(settings, flag, False)
        and getattr(settings, "HUBTEL_DIRECT_EGRESS_CONFIRMED", False)
        and whitelisted_web_process()
        and settings.HUBTEL_API_ID and settings.HUBTEL_API_KEY
        and re.fullmatch(r"[0-9]{1,32}", str(account))
    )


def require_enabled(kind):
    if not enabled(kind):
        raise ValidationError("Hubtel Direct is not enabled or its whitelisted egress is not confirmed.")


def require_reconciliation_ready(kind):
    """Allow safe GET status recovery after outbound payment switches are OFF.

    Turning off a charge/payout is not permission to abandon funds already
    moving. Status checks still require credentials, the intended merchant
    account, and an explicitly confirmed whitelisted WEB network path.
    """
    if kind not in {"receive", "send_momo", "send_bank"}:
        raise ValidationError("Unexpected Hubtel transaction kind.")
    account_number = (getattr(settings, "HUBTEL_COLLECTION_ACCOUNT", "")
                      if kind == "receive" else
                      getattr(settings, "HUBTEL_DISBURSEMENT_ACCOUNT", ""))
    if not (
        getattr(settings, "HUBTEL_DIRECT_EGRESS_CONFIRMED", False)
        and whitelisted_web_process()
        and getattr(settings, "HUBTEL_API_ID", "")
        and getattr(settings, "HUBTEL_API_KEY", "")
        and re.fullmatch(r"[0-9]{1,32}", str(account_number))
    ):
        raise ValidationError("Hubtel status checking requires confirmed WEB egress and merchant credentials.")


def _headers():
    token = base64.b64encode((settings.HUBTEL_API_ID + ":" + settings.HUBTEL_API_KEY).encode()).decode()
    return {"Authorization": "Basic " + token, "Accept": "application/json", "Content-Type": "application/json"}


def _callback():
    origin = str(settings.MARKET_SITE_ORIGIN).rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or parsed.netloc != "market.kofadimpex.com" or parsed.path:
        raise ValidationError("A verified public KOFAD callback origin is required.")
    return origin + "/market/payments/hubtel-direct/callback/"


def _account(kind):
    return settings.HUBTEL_COLLECTION_ACCOUNT if kind == "receive" else settings.HUBTEL_DISBURSEMENT_ACCOUNT


def _submit_url(tx):
    account = _account(tx.kind)
    if tx.kind == "receive":
        return f"{RECEIVE_ORIGIN}/merchantaccount/merchants/{account}/receive/mobilemoney"
    if tx.kind == "send_momo":
        return f"{SEND_ORIGIN}/api/merchants/{account}/send/mobilemoney"
    if tx.kind == "send_bank" and tx.bank_code in BANK_CODES:
        return f"{SEND_ORIGIN}/api/merchants/{account}/send/bank/gh/{tx.bank_code}"
    raise ValidationError("Unsupported Hubtel destination.")


def _payload(tx):
    dest = unseal(tx.destination_encrypted)
    common = {
        "Amount": float(tx.amount), "ClientReference": tx.client_reference,
        "PrimaryCallbackURL": _callback(),
        "Description": "KOFAD transaction " + tx.client_reference[-10:],
    }
    if tx.kind == "receive":
        payload = dict(common, CustomerMsisdn=dest, Channel=tx.channel)
        if tx.recipient_name:
            payload["CustomerName"] = tx.recipient_name[:100]
        return payload
    if tx.kind == "send_momo":
        return dict(common, RecipientMsisdn=dest, RecipientName=tx.recipient_name[:100], Channel=tx.channel)
    return dict(common, BankAccountNumber=dest, BankAccountName=tx.recipient_name[:100])


def _field(obj, name):
    if not isinstance(obj, dict):
        return None
    keys = [k for k in obj if isinstance(k, str) and k.casefold() == name.casefold()]
    if len(keys) != 1:
        return None
    return obj[keys[0]]


def _parse(response):
    try:
        body = response.json()
    except (ValueError, TypeError) as exc:
        raise StatusUnconfirmed("Provider returned non-JSON data.") from exc
    if not isinstance(body, dict):
        raise StatusUnconfirmed("Provider returned invalid data.")
    data = _field(body, "Data")
    if not isinstance(data, dict):
        raise StatusUnconfirmed("Provider returned no transaction record.")
    return str(_field(body, "ResponseCode") or ""), data


def submit(tx):
    """At most one POST for each persisted reference. Caller owns state transition."""
    require_enabled(tx.kind)
    try:
        response = requests.post(
            _submit_url(tx), json=_payload(tx), headers=_headers(),
            timeout=settings.HUBTEL_TIMEOUT_SECONDS, allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            # Even an HTTP error can hide provider-side acceptance: never auto-retry.
            raise SubmissionUnknown("Hubtel POST outcome could not be confirmed.")
        code, data = _parse(response)
        if code in {"0000", "0001"}:
            if (_field(data, "ClientReference") != tx.client_reference
                    or not str(_field(data, "TransactionId") or "").strip()):
                raise SubmissionUnknown("Hubtel transaction identity could not be confirmed.")
            return code, str(_field(data, "TransactionId"))[:100]
        if code in {"2001", "3050", "4000", "4070", "4075", "4101", "4103"}:
            raise ProviderRejected(code)
        raise SubmissionUnknown("Hubtel response was not a definitive rejection.")
    except (requests.RequestException, StatusUnconfirmed) as exc:
        raise SubmissionUnknown("Hubtel POST outcome unknown; no repeat request is safe.") from exc


def check(tx):
    """Independently verify a persisted reference, not a browser/callback claim."""
    require_reconciliation_ready(tx.kind)
    acc = _account(tx.kind)
    url = (
        f"{RECEIVE_STATUS_ORIGIN}/transactions/{acc}/status" if tx.kind == "receive"
        else f"{SEND_STATUS_ORIGIN}/api/merchants/{acc}/transactions/status"
    )
    try:
        response = requests.get(
            url, params={"clientReference": tx.client_reference},
            headers=_headers(), timeout=settings.HUBTEL_TIMEOUT_SECONDS, allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            raise StatusUnconfirmed("Status endpoint unavailable.")
        code, data = _parse(response)
    except requests.RequestException as exc:
        raise StatusUnconfirmed("Status endpoint unavailable.") from exc
    if code not in {"0000", "success"}:
        raise StatusUnconfirmed("Hubtel has not confirmed a final status.")
    if _field(data, "ClientReference") != tx.client_reference:
        raise ValidationError("Hubtel returned a mismatched transaction reference.")
    received_amount = money(_field(data, "Amount"))
    if received_amount != tx.amount:
        raise ValidationError("Hubtel returned a mismatched amount.")
    currency = str(_field(data, "CurrencyCode") or "").upper()
    if currency not in {"", "GHS"}:
        raise ValidationError("Hubtel returned a mismatched currency.")
    hubtel_id = str(_field(data, "TransactionId") or "").strip()
    if not hubtel_id or (tx.hubtel_transaction_id and hubtel_id != tx.hubtel_transaction_id):
        raise ValidationError("Hubtel transaction identity was missing or changed.")
    if tx.kind == "receive":
        method = str(_field(data, "PaymentMethod") or "").lower().replace("-", "").replace("_", "")
        if method and method != "mobilemoney":
            raise ValidationError("Unexpected Hubtel receive channel.")
        state = str(_field(data, "Status") or "").strip().lower()
        final = {"paid": "success", "unpaid": "pending", "refunded": "attention"}.get(state)
    else:
        channel = str(_field(data, "Channel") or "")
        if channel and tx.kind == "send_momo" and channel != tx.channel:
            raise ValidationError("Hubtel returned a different payout network.")
        final = {"success": "success", "failed": "failed", "failure": "failed"}.get(
            str(_field(data, "transactionStatus") or "").strip().lower()
        )
    if final is None:
        raise StatusUnconfirmed("Hubtel transaction status remains unknown.")
    return final, hubtel_id
