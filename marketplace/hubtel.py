"""Server-only Hubtel Online Checkout, using the public status endpoint agreed with Hubtel."""
import base64
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from . import services
from .models import MarketPaymentAttempt, OnlineOrder, OrderEvent, PaymentConfiguration

INITIATE_URL = "https://payproxyapi.hubtel.com/items/initiate"
STATUS_ORIGIN = "https://rmsc.hubtel.com/v1/merchantaccount/merchants/"
PUBLIC_ORIGIN = "https://market.kofadimpex.com"
logger = logging.getLogger(__name__)


def configured():
    return bool(settings.HUBTEL_API_ID and settings.HUBTEL_API_KEY
                and re.fullmatch(r"[0-9]{1,32}", settings.HUBTEL_COLLECTION_ACCOUNT))


def selected_provider():
    return PaymentConfiguration.objects.filter(pk=1).values_list("provider", flat=True).first() or ("hubtel" if configured() else "paystack")


def ready(provider=None):
    provider = provider or selected_provider()
    if provider == "hubtel":
        return configured() and settings.HUBTEL_CHECKOUT_ENABLED
    return bool(settings.PAYSTACK_SECRET_KEY)


def availability_notice():
    provider = selected_provider()
    if provider == "hubtel" and configured() and not settings.HUBTEL_CHECKOUT_ENABLED:
        return "Hubtel is connected. Checkout will open after payment testing and activation."
    if not ready(provider):
        return ("Hubtel" if provider == "hubtel" else "Paystack") + " is not available for new payments yet."
    return ""


def headers():
    if not configured():
        raise ValidationError("Hubtel payment is not configured yet. Please contact KOFAD.")
    token = base64.b64encode(
        (settings.HUBTEL_API_ID + ":" + settings.HUBTEL_API_KEY).encode()
    ).decode("ascii")
    return {"Authorization": "Basic " + token, "Accept": "application/json", "Content-Type": "application/json"}


def initialize_payment(order, callback_url):
    # An unresolved attempt must stay on its original provider after a settings change.
    existing = order.payment_attempts.filter(
        status__in=["initializing", "submission_unknown", "pending", "attention"]
    ).order_by("-created_at").first()
    provider = existing.provider if existing else selected_provider()
    if provider == "hubtel":
        return initialize(order)
    return services.initialize_paystack(order, callback_url)


def initialize(order):
    auth = headers()
    if not settings.HUBTEL_CHECKOUT_ENABLED:
        raise ValidationError("Hubtel checkout is awaiting activation. Your order is saved.")
    # Persist the intent before contacting Hubtel. Lost responses must never create a new charge.
    with transaction.atomic():
        order = OnlineOrder.objects.select_for_update().get(pk=order.pk)
        if order.payment_status in {"paid", "refunded"} or order.status != "awaiting_payment":
            raise ValidationError("This order cannot start another payment.")
        existing = order.payment_attempts.filter(
            status__in=["initializing", "submission_unknown", "pending", "attention"]
        ).order_by("-created_at").first()
        if existing:
            if existing.provider != "hubtel":
                raise ValidationError("Complete or reconcile the previous payment before changing provider.")
            if existing.status == "attention":
                raise ValidationError("This payment needs staff review. Please do not pay again.")
            if existing.authorization_url:
                return existing
            raise ValidationError("We are checking the previous payment request. Please do not pay again.")
        services.refresh_order_reservations(order)
        reference = secrets.token_hex(16)  # Hubtel allows at most 32 characters.
        attempt = MarketPaymentAttempt.objects.create(
            order=order, provider="hubtel", reference=reference, amount=order.total,
            currency="GHS", status="initializing",
            next_check_at=timezone.now() + timedelta(seconds=10),
        )
        order.payment_status = "pending"
        order.payment_reference = reference
        order.save(update_fields=["payment_status", "payment_reference", "updated_at"])
    payload = {
        "totalAmount": float(attempt.amount),
        "description": "KOFAD order " + re.sub(r"[^A-Za-z0-9 ]", "", order.public_reference),
        "merchantAccountNumber": settings.HUBTEL_COLLECTION_ACCOUNT,
        "clientReference": reference,
        "callbackUrl": PUBLIC_ORIGIN + "/market/payments/hubtel/callback/",
        "returnUrl": PUBLIC_ORIGIN + "/market/payments/hubtel/return/?reference=" + reference,
        "cancellationUrl": PUBLIC_ORIGIN + "/market/payments/hubtel/return/?reference=" + reference,
    }
    try:
        response = requests.post(INITIATE_URL, headers=auth, json=payload,
                                 timeout=settings.HUBTEL_TIMEOUT_SECONDS, allow_redirects=False)
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        url = data.get("checkoutUrl", "") if isinstance(data, dict) else ""
        parsed = urlsplit(url) if isinstance(url, str) else urlsplit("")
        valid = (
            200 <= response.status_code < 300 and isinstance(body, dict)
            and body.get("responseCode") == "0000" and isinstance(data, dict)
            and data.get("clientReference") == reference and data.get("checkoutId")
            and parsed.scheme == "https" and parsed.netloc == "pay.hubtel.com"
            and bool(parsed.path.strip("/")) and len(url) <= 200
        )
    except (requests.RequestException, ValueError, TypeError):
        valid = False
    if not valid:
        MarketPaymentAttempt.objects.filter(pk=attempt.pk, status="initializing").update(
            status="submission_unknown", provider_message="Checkout response was not confirmed; reconciliation required."
        )
        raise ValidationError("The payment request could not be confirmed. Your order is saved; do not pay again while we check.")
    MarketPaymentAttempt.objects.filter(pk=attempt.pk, status="initializing").update(
        status="pending", access_code=str(data["checkoutId"])[:120], authorization_url=url,
        provider_message="Secure Hubtel checkout created.",
    )
    attempt.refresh_from_db()
    return attempt


def _normalise_keys(payload):
    """Accept casing variations of Hubtel's documented fields without weakening validation."""
    if not isinstance(payload, dict):
        return payload
    canonical = {
        "responsecode": "responseCode", "data": "data", "message": "message",
        "clientreference": "clientReference", "status": "status",
        "transactionid": "transactionId", "externaltransactionid": "externalTransactionId",
        "paymentmethod": "paymentMethod", "currencycode": "currencyCode",
        "amount": "amount", "charges": "charges", "amountaftercharges": "amountAfterCharges",
        "isfulfilled": "isFulfilled", "date": "date",
    }
    normalised = {}
    for key, value in payload.items():
        raw = str(key)
        name = canonical.get(raw.casefold(), raw[:1].lower() + raw[1:])
        if name in normalised and normalised[name] != value:
            raise services.PaymentVerificationUnavailable("Ambiguous Hubtel response.")
        normalised[name] = value
    return normalised


def _status_candidates(raw_data):
    """Flatten the safe response shapes used by Hubtel status endpoints."""
    if isinstance(raw_data, list):
        return [_normalise_keys(item) for item in raw_data if isinstance(item, dict)]
    if not isinstance(raw_data, dict):
        return []
    data = _normalise_keys(raw_data)
    if any(key in data for key in ("clientReference", "status", "transactionId", "amount")):
        return [data]
    for key in ("transactions", "items", "records", "results", "result"):
        nested = data.get(key)
        if isinstance(nested, list):
            return [_normalise_keys(item) for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            return [_normalise_keys(nested)]
    return []


def _status_data(body, reference):
    if not isinstance(body, dict):
        return None
    candidates = _status_candidates(body.get("data"))
    matching = [
        item for item in candidates
        if str(item.get("clientReference", "")).strip() == reference
    ]
    if len(matching) == 1:
        return matching[0]
    if len(matching) > 1:
        fingerprints = {
            (
                str(item.get("status", "")).strip().lower(),
                str(item.get("transactionId", "")).strip(),
                str(item.get("amount", "")).strip(),
            )
            for item in matching
        }
        if len(fingerprints) == 1:
            return matching[0]
        raise services.PaymentVerificationUnavailable("Hubtel returned conflicting payment records.")
    return None


def verify(reference):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", str(reference or "")):
        raise ValidationError("Invalid payment reference.")
    try:
        response = requests.get(
            STATUS_ORIGIN + settings.HUBTEL_COLLECTION_ACCOUNT + "/transactions/status",
            params={"clientReference": reference}, headers=headers(),
            timeout=settings.HUBTEL_TIMEOUT_SECONDS, allow_redirects=False,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Hubtel status request failed ref_suffix=%s", str(reference)[-6:])
        raise services.PaymentVerificationUnavailable("Payment confirmation is temporarily unavailable.") from exc

    body = _normalise_keys(body)
    response_code = str(body.get("responseCode", "")).strip() if isinstance(body, dict) else ""
    raw_data = body.get("data") if isinstance(body, dict) else None
    data = _status_data(body, reference)
    safe_status = str(data.get("status", "")).strip() if isinstance(data, dict) else ""
    safe_amount = data.get("amount") if isinstance(data, dict) else None
    shape = type(raw_data).__name__
    shape_count = len(raw_data) if isinstance(raw_data, (list, dict)) else 0
    shape_keys = sorted(str(key)[:40] for key in raw_data.keys())[:20] if isinstance(raw_data, dict) else []
    logger.info(
        "Hubtel status check http=%s code=%s data_shape=%s count=%s keys=%s status=%s amount=%s transaction=%s ref_suffix=%s",
        response.status_code, response_code, shape, shape_count, ",".join(shape_keys),
        safe_status[:24], safe_amount,
        bool(data.get("transactionId")) if isinstance(data, dict) else False,
        str(reference)[-6:],
    )

    if not 200 <= response.status_code < 300 or not isinstance(body, dict) or response_code != "0000":
        raise services.PaymentVerificationUnavailable("Hubtel has not confirmed this payment yet.")
    if not isinstance(data, dict):
        raise services.PaymentVerificationUnavailable("Payment verification did not match the saved reference.")

    status_key = str(data.get("status", "")).strip().lower()
    status_map = {"paid": "Paid", "unpaid": "Unpaid", "refunded": "Refunded"}
    if status_key not in status_map:
        raise services.PaymentVerificationUnavailable("Hubtel returned an unrecognised payment status.")
    data["status"] = status_map[status_key]
    data["clientReference"] = str(data.get("clientReference", "")).strip()
    return data


def reconcile(reference):
    attempt = MarketPaymentAttempt.objects.filter(provider="hubtel", reference=reference).first()
    if not attempt:
        raise ValidationError("Unknown Hubtel payment.")
    if attempt.status == "success":
        return attempt.order
    # Database lease limits callbacks/refreshes and survives multiple app processes.
    now = timezone.now()
    claimed = MarketPaymentAttempt.objects.filter(pk=attempt.pk).filter(
        next_check_at__lte=now
    ).update(next_check_at=now + timedelta(seconds=20), check_count=F("check_count") + 1)
    if not claimed:
        raise services.PaymentVerificationUnavailable("Payment is being checked. Please check again shortly.")
    try:
        data = verify(reference)
        summary = {key: data.get(key) for key in (
            "status", "clientReference", "transactionId", "currencyCode", "amount", "paymentMethod"
        )}
        summary = {key: str(value) if isinstance(value, Decimal) else value for key, value in summary.items()}
        MarketPaymentAttempt.objects.filter(pk=attempt.pk).update(verification_summary=summary)
        if data["status"] != "Paid":
            raise services.PaymentVerificationUnavailable("Payment is not confirmed. If you were debited, do not pay again.")
        try:
            amount = Decimal(str(data.get("amount")))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValidationError("Invalid verified payment amount.") from exc
        currency = str(data.get("currencyCode") or "").strip().upper()
        if (not amount.is_finite() or amount != attempt.amount or amount <= 0
                or currency not in {"", "GHS"} or not data.get("transactionId")):
            raise ValidationError("Verified payment details do not match the saved order.")
        # Hubtel documents a nullable currencyCode. This account and all orders are GHS only.
        method_key = re.sub(r"[^a-z]", "", str(data.get("paymentMethod", "")).lower())
        channel = {"mobilemoney": "mobile_money", "bankcard": "card", "card": "card"}.get(
            method_key, method_key or "bank"
        )
        order = services.finalize_payment(reference, {
            "status": "success", "amount": int(amount * 100), "currency": "GHS",
            "channel": channel,
        }, expected_provider="hubtel")
        MarketPaymentAttempt.objects.filter(pk=attempt.pk).update(next_check_at=None, provider_message="Verified by Hubtel status check.")
        return order
    except services.PaymentVerificationUnavailable:
        raise
    except ValidationError:
        MarketPaymentAttempt.objects.filter(pk=attempt.pk).update(
            status="attention", next_check_at=None, provider_message="Payment verification requires staff review."
        )
        OrderEvent.objects.get_or_create(
            order=attempt.order, status="payment_attention", title="Payment needs review",
            defaults={"note": "Hubtel payment details could not be matched. Reconcile before fulfilment.", "customer_visible": False},
        )
        raise


def _retry_delay(attempt, now=None):
    now = now or timezone.now()
    age = now - attempt.created_at
    if age < timedelta(minutes=5):
        return timedelta(seconds=10)
    if age < timedelta(minutes=30):
        return timedelta(seconds=30)
    if age < timedelta(hours=2):
        return timedelta(minutes=2)
    return timedelta(minutes=5)


def reconcile_due(limit=10):
    """Verify pending payments independently of the customer's browser session."""
    if not configured():
        return 0
    now = timezone.now()
    attempts = list(MarketPaymentAttempt.objects.filter(
        provider="hubtel", status__in=["initializing", "submission_unknown", "pending"],
        next_check_at__lte=now,
    ).order_by("next_check_at")[:limit])
    for attempt in attempts:
        if now - attempt.created_at >= timedelta(hours=72):
            MarketPaymentAttempt.objects.filter(pk=attempt.pk).update(
                status="attention", next_check_at=None,
                provider_message="Hubtel has not supplied a final status after 72 hours; staff review required."
            )
            continue
        try:
            reconcile(attempt.reference)
        except services.PaymentVerificationUnavailable:
            # Provider is pending/unavailable: keep checking server-side even if the buyer closed the browser.
            MarketPaymentAttempt.objects.filter(pk=attempt.pk, status__in=[
                "initializing", "submission_unknown", "pending"
            ]).update(
                next_check_at=timezone.now() + _retry_delay(attempt),
                provider_message="Awaiting Hubtel final transaction status.",
            )
        except ValidationError:
            # A verified mismatch is handled by reconcile() and must not be silently retried as a normal payment.
            continue
    return len(attempts)
