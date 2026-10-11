"""Private encrypted retention of exact Hubtel HTTP bodies for merchant certification.

Never record Authorization headers, credentials or customer session values here.
Only owners with manage_company rights can retrieve decrypted records.
"""
import base64
import hashlib
import hmac

from cryptography.fernet import Fernet
from django.conf import settings

from .models import HubtelEvidence, MarketPaymentAttempt


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
    if direction not in {"callback", "status_check"}:
        raise ValueError("Unexpected Hubtel evidence direction.")
    if not isinstance(raw_body, bytes):
        raise TypeError("Hubtel evidence body must be bytes.")
    if len(raw_body) > 262144:
        raise ValueError("Hubtel response is too large to preserve safely.")
    if attempt is None:
        attempt = MarketPaymentAttempt.objects.filter(
            provider="hubtel", reference=reference
        ).first()
    return HubtelEvidence.objects.create(
        attempt=attempt,
        reference=reference,
        direction=direction,
        http_status=http_status,
        encrypted_body=_cipher().encrypt(raw_body).decode("ascii"),
        body_sha256=hashlib.sha256(raw_body).hexdigest(),
    )


def decrypt_exchange(exchange):
    raw = _cipher().decrypt(exchange.encrypted_body.encode("ascii"))
    if hashlib.sha256(raw).hexdigest() != exchange.body_sha256:
        raise ValueError("Stored Hubtel evidence integrity check failed.")
    return raw
