"""Hubtel October 2026 debt preapproval, refund and wallet authorization API.

Sensitive actions are gated and use the whitelisted WEB service only.
A client-side callback is NEVER sufficient to create authorization or settle debt.
"""
import re
import uuid

import requests
from django.conf import settings
from django.core.exceptions import ValidationError

from . import client

PREAPPROVAL_ORIGIN = "https://preapproval.hubtel.com"
REFUND_ORIGIN = "https://refund-api.hubtel.com"
DEBIT_CHANNELS = frozenset(("mtn-gh-direct-debit", "vodafone-gh-direct-debit"))
FREQUENCIES = frozenset(("once", "daily", "weekly", "monthly", "quarterly", "yearly"))


def gate(feature):
    flags = {
        "preapproval": "HUBTEL_DEBT_PREAPPROVAL_ENABLED",
        "charge": "HUBTEL_DEBT_AUTO_DEBIT_ENABLED",
        "refund": "HUBTEL_REFUNDS_ENABLED",
    }
    if feature not in flags or not getattr(settings, flags[feature], False):
        raise ValidationError("This Hubtel financial service is awaiting activation.")
    client.require_enabled("receive")


def account():
    acc = str(settings.HUBTEL_COLLECTION_ACCOUNT)
    if not re.fullmatch(r"[0-9]{1,32}", acc):
        raise ValidationError("Hubtel collection account is not configured.")
    return acc


def number(value):
    from core.identity import normalize_ghana_phone
    return normalize_ghana_phone(value).lstrip("+")


def reference():
    return uuid.uuid4().hex


def callback(kind="preapproval"):
    base = str(settings.MARKET_SITE_ORIGIN).rstrip("/")
    if base != "https://market.kofadimpex.com":
        raise ValidationError("Verified KOFAD Market HTTPS callback required.")
    route = {
        "preapproval": "/market/payments/hubtel-direct/preapproval/callback/",
        "charge": "/market/payments/hubtel-direct/callback/",
        "refund": "/market/payments/hubtel-direct/refund/callback/",
    }.get(kind)
    if not route:
        raise ValidationError("Invalid callback purpose.")
    return base + route


def _request(method, url, *, payload=None):
    try:
        response = requests.request(
            method, url, json=payload if method == "POST" else None,
            headers=client._headers(), allow_redirects=False,
            timeout=min(settings.HUBTEL_TIMEOUT_SECONDS, 15),
        )
        if not 200 <= response.status_code < 300:
            raise client.StatusUnconfirmed("Hubtel authorization or refund service is unavailable.")
        body = response.json()
        if not isinstance(body, dict):
            raise client.StatusUnconfirmed("Hubtel returned invalid transaction evidence.")
        return body
    except (requests.RequestException, ValueError) as exc:
        raise client.StatusUnconfirmed("Hubtel request outcome is unconfirmed; do not retry the same action.") from exc


def _record(body):
    data = client._field(body, "data")
    if not isinstance(data, dict):
        raise client.StatusUnconfirmed("Missing Hubtel provider record.")
    return data


def initiate_preapproval(*, phone, channel, client_reference):
    gate("preapproval")
    if channel not in DEBIT_CHANNELS or not re.fullmatch(r"[a-zA-Z0-9]{1,36}", client_reference):
        raise ValidationError("Unsupported debit channel or preapproval reference.")
    url = f"{PREAPPROVAL_ORIGIN}/api/v2/merchant/{account()}/preapproval/initiate"
    body = _request("POST", url, payload={
        "clientReferenceId": client_reference, "customerMsisdn": number(phone),
        "channel": channel, "callbackUrl": callback(),
    })
    data = _record(body)
    if str(client._field(body, "responseCode")) != "2000":
        raise client.StatusUnconfirmed("Hubtel preapproval has not been accepted.")
    if client._field(data, "clientReferenceId") != client_reference:
        raise ValidationError("Hubtel preapproval reference did not match.")
    provider_id = str(client._field(data, "hubtelPreApprovalId") or "")
    verification = str(client._field(data, "verificationType") or "").upper()
    if not provider_id or verification not in {"USSD", "OTP"}:
        raise client.StatusUnconfirmed("Hubtel did not provide a verifiable preapproval.")
    return {
        "provider_id": provider_id[:100],
        "verification_type": verification,
        "otp_prefix": str(client._field(data, "otpPrefix") or "")[:8],
        "status": str(client._field(data, "preapprovalStatus") or "PENDING").upper(),
    }


def verify_preapproval_otp(*, phone, provider_id, client_reference, otp):
    gate("preapproval")
    # The Hubtel prefix (4 letters) plus code (4 digits) belongs to
    # customer authorization. Never store the full OTP.
    if not re.fullmatch(r"[A-Z]{4}-[0-9]{4}", str(otp or "")):
        raise ValidationError("Enter the Hubtel code in ABCD-1234 format.")
    url = f"{PREAPPROVAL_ORIGIN}/api/v2/merchant/{account()}/preapproval/verifyotp"
    body = _request("POST", url, payload={
        "customerMsisdn": number(phone), "hubtelPreApprovalId": provider_id,
        "clientReferenceId": client_reference, "otpCode": otp,
    })
    if str(client._field(body, "responseCode")) != "2000":
        raise client.StatusUnconfirmed("Hubtel has not confirmed the OTP yet.")
    data = _record(body)
    if str(client._field(data, "hubtelPreApprovalId")) != provider_id:
        raise ValidationError("Hubtel OTP response belongs to a different authorization.")
    return str(client._field(data, "preapprovalStatus") or "PENDING").upper()


def preapproval_status(client_reference, *, expected_phone=None, expected_provider_id=None):
    # Existing consent must remain independently auditable after initiation
    # has been paused. This GET never initiates an authorization.
    client.require_reconciliation_ready("receive")
    if not re.fullmatch(r"[a-zA-Z0-9]{1,36}", client_reference):
        raise ValidationError("Invalid preapproval reference.")
    url = f"{PREAPPROVAL_ORIGIN}/api/v2/merchant/{account()}/preapproval/{client_reference}/status"
    data = _record(_request("GET", url))
    if str(client._field(data, "clientReferenceId")) != client_reference:
        raise ValidationError("Preapproval status returned a different reference.")
    if expected_phone and str(client._field(data, "customerMsisdn") or "") != number(expected_phone):
        raise ValidationError("Preapproval status returned a different wallet.")
    if expected_provider_id and str(client._field(data, "hubtelPreApprovalId") or "") != expected_provider_id:
        raise ValidationError("Preapproval status returned a different provider identity.")
    state = str(client._field(data, "preapprovalStatus") or "").upper()
    if state not in {"APPROVED", "PENDING", "REJECTED", "FAILED", "EXPIRED", "CANCELLED"}:
        raise client.StatusUnconfirmed("Hubtel approval status is unknown.")
    return state


def cancel_preapproval(phone):
    # Customer-initiated revocation must remain possible even after finance
    # turns OFF the switch for NEW preapprovals. Network/credential gates stay.
    client.require_reconciliation_ready("receive")
    url = f"{PREAPPROVAL_ORIGIN}/api/v2/merchant/{account()}/preapproval/{number(phone)}/cancel"
    # Hubtel documents a GET for cancellation. KOFAD only exposes a
    # CSRF-protected authenticated POST; this GET never appears as a browser link.
    body = _request("GET", url)
    if str(client._field(body, "responseCode")) != "2000" or client._field(body, "data") is not True:
        raise client.StatusUnconfirmed("Hubtel has not confirmed preapproval cancellation.")
    return True


def reactivate_preapproval(phone):
    gate("preapproval")
    url = f"{PREAPPROVAL_ORIGIN}/api/v2/merchant/{account()}/preapproval/reactivate"
    body = _request("POST", url, payload={"callbackUrl": callback(), "customerMsisdn": number(phone)})
    data = _record(body)
    if str(client._field(body, "responseCode")) != "2000":
        raise client.StatusUnconfirmed("Hubtel did not confirm reactivation request.")
    return data


def debit_charge(tx):
    """Only an independently approved bounded agreement may call this; no retries."""
    gate("charge")
    from .models import DebtDebitAttempt, OnlineDebtSettings
    from .mandates import outstanding, UNRESOLVED_CHARGE_STATES

    # tx can have been fetched before a customer cancelled the mandate or
    # finance placed the account on hold. Never rely on that stale object.
    current = DebtDebitAttempt.objects.select_related("mandate__party").get(pk=tx.pk)
    mandate = current.mandate
    policy = OnlineDebtSettings.objects.filter(branch=mandate.branch).first()
    if not policy or policy.pause_auto_collection:
        raise ValidationError("Automatic Online Debt collection is paused for this location.")
    if mandate.status != "approved" or current.status != "sending" or not current.sent_at:
        raise ValidationError("A current approved mandate and durable one-time send claim are required.")
    if current.reference != tx.reference or current.amount != tx.amount:
        raise ValidationError("Debit attempt changed after it was scheduled.")
    if (current.amount > mandate.maximum_total - mandate.collected_total
            or current.amount > outstanding(mandate.party)):
        raise ValidationError("The real customer balance or authorized ceiling has changed.")
    if DebtDebitAttempt.objects.filter(
        mandate=mandate, status__in=UNRESOLVED_CHARGE_STATES,
    ).exclude(pk=current.pk).exists():
        raise ValidationError("Another debit or provider-paid finance review is not resolved.")
    tx = current
    if tx.mandate.channel not in DEBIT_CHANNELS:
        raise ValidationError("Direct Debit only supports MTN and Telecel.")
    if not re.fullmatch(r"[a-f0-9]{32}", tx.reference):
        raise ValidationError("An immutable transaction reference is required.")
    amount = client.money(tx.amount)
    payload = {
        "CustomerMsisdn": number(client.unseal(tx.mandate.phone_encrypted)),
        "Channel": tx.mandate.channel, "Amount": float(amount),
        "PrimaryCallbackURL": callback("charge"),
        "Description": "KOFAD authorized debt installment",
        "ClientReference": tx.reference,
    }
    url = f"{client.RECEIVE_ORIGIN}/merchantaccount/merchants/{account()}/receive/mobilemoney"
    body = _request("POST", url, payload=payload)
    data = _record(body)
    code = str(client._field(body, "responseCode") or "")
    if code not in {"0000", "0001"} or client._field(data, "ClientReference") != tx.reference:
        raise client.StatusUnconfirmed("Direct Debit transaction was not confirmed; never retry this reference.")
    transaction_id = str(client._field(data, "TransactionId") or "")
    if not transaction_id:
        raise client.StatusUnconfirmed("Hubtel transaction identifier is missing.")
    return transaction_id[:100]


def submit_refund(*, hubtel_order_id, callback_url=None):
    gate("refund")
    if not re.fullmatch(r"[a-fA-F0-9]{24,64}", str(hubtel_order_id or "")):
        raise ValidationError("A verified original Hubtel order ID is mandatory.")
    url = f"{REFUND_ORIGIN}/refund/{account()}/order/{hubtel_order_id}"
    body = _request("POST", url, payload={"callbackUrl": callback("refund")})
    data = _record(body)
    if str(client._field(body, "responseCode")) != "0001" or str(client._field(data, "orderId")) != hubtel_order_id:
        raise client.StatusUnconfirmed("Refund status is unresolved; do not submit again for this order.")
    return {"order_id": hubtel_order_id, "status": "pending"}

