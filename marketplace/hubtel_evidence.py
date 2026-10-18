"""Private encrypted retention of exact Hubtel HTTP bodies for merchant certification.

Never record Authorization headers, credentials or customer session values here.
Only owners with manage_company rights can retrieve decrypted records.
"""
import base64
import hashlib
import hmac
import logging
from datetime import timedelta

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .models import HubtelEvidence, MarketPaymentAttempt

# Callback payloads are untrusted; independent provider checks settle payments.
# Retain distinct original callback bodies for investigation without allowing a
# known reference to turn the evidence table into an unbounded write sink.
CALLBACK_EVIDENCE_LIMIT_PER_HOUR = 24


def _cipher():
    # Stable for the life of SECRET_KEY. Do not rotate it without migrating
    # existing evidence; a new key cannot decrypt historical records.
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        b"kofad:hubtel-evidence-encryption:v1",
        hashlib.sha256,
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def save_exchange(*, reference, direction, raw_body, http_status=None, attempt=None):
    """Keep the *actual* provider body, rather than a normalized/rebuilt JSON."""
    if direction not in {"callback", "status_check", "wallet_verify", "bank_verify", "direct_submit", "direct_status", "wallet_query", "direct_request", "preapprove_req", "preapprove_rsp", "status_request", "direct_check"}:
        raise ValueError("Unexpected Hubtel evidence direction.")
    if not isinstance(raw_body, bytes):
        raise TypeError("Hubtel evidence body must be bytes.")
    if len(raw_body) > 262144:
        raise ValueError("Hubtel response is too large to preserve safely.")
    if attempt is None:
        attempt = MarketPaymentAttempt.objects.filter(
            provider="hubtel", reference=reference
        ).first()
    record = {
        "attempt": attempt,
        "reference": reference,
        "direction": direction,
        "http_status": http_status,
        "encrypted_body": _cipher().encrypt(raw_body).decode("ascii"),
        "body_sha256": hashlib.sha256(raw_body).hexdigest(),
    }
    if direction != "callback":
        return HubtelEvidence.objects.create(**record)

    # Hubtel callbacks have no source-authenticated signature. A forged caller
    # who learns a legitimate reference must not exhaust database storage.
    # PostgreSQL's non-blocking transaction lock serializes the hourly check
    # across all web workers; on contention, omit the *untrusted* evidence.
    # Settlement is NEVER authorized by this callback.
    lock_key = int.from_bytes(
        hashlib.sha256(("kofad:hubtel:callback:" + reference).encode()).digest()[:8],
        byteorder="big", signed=True,
    )
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [lock_key])
            if not cursor.fetchone()[0]:
                return None
        recent = HubtelEvidence.objects.filter(
            reference=reference, direction="callback",
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()
        if recent >= CALLBACK_EVIDENCE_LIMIT_PER_HOUR:
            return None
        return HubtelEvidence.objects.create(**record)


def decrypt_exchange(exchange):
    raw = _cipher().decrypt(exchange.encrypted_body.encode("ascii"))
    if hashlib.sha256(raw).hexdigest() != exchange.body_sha256:
        raise ValueError("Stored Hubtel evidence integrity check failed.")
    return raw


def capture_response(response, *, direction, reference):
    """Persist exact provider response bytes, including HTTP 4xx, without affecting payments.

    Never store provider Authorization headers or browser session information.
    """
    body = getattr(response, "content", None)
    if not isinstance(body, bytes) or len(body) > 262144:
        return
    try:
        save_exchange(reference=reference, direction=direction,
                      raw_body=body, http_status=response.status_code)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Hubtel evidence capture unavailable type=%s", type(exc).__name__
        )


def capture_request(response, *, direction, reference, capture_url=False):
    """Store prepared outbound request bytes, NEVER request Authorization headers.

    Whitelist only wallet-name GET query or non-OTP transaction/preapproval
    initiation; callers must not use this helper for PIN/OTP/password requests.
    """
    if direction not in {"wallet_query", "direct_request", "preapprove_req", "status_request", "direct_check"}:
        raise ValueError("Unexpected request evidence type.")
    prepared = getattr(response, "request", None)
    if prepared is None:
        return
    raw = (getattr(prepared, "url", "") or "").encode("utf-8") if capture_url else getattr(prepared, "body", b"")
    if raw is None:
        raw = b""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or len(raw) > 262144:
        return
    try:
        save_exchange(reference=reference, direction=direction,
                      raw_body=raw, http_status=response.status_code)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Hubtel outbound evidence capture unavailable type=%s", type(exc).__name__
        )
