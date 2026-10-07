"""Server-only Hubtel Online Checkout, using the public status endpoint agreed with Hubtel."""
import base64
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
            next_check_at=timezone.now() + timedelta(minutes=5),
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
        raise services.PaymentVerificationUnavailable("Payment confirmation is temporarily unavailable.") from exc
    if isinstance(body, dict):
        # The public endpoint uses PascalCase; the published endpoint uses camelCase.
        normalised = {}
        for key, value in body.items():
            name = key[:1].lower() + key[1:]
            if name in normalised and normalised[name] != value:
                raise services.PaymentVerificationUnavailable("Ambiguous payment response.")
            normalised[name] = value
        body = normalised
    if not 200 <= response.status_code < 300 or not isinstance(body, dict) or body.get("responseCode") != "0000":
        raise services.PaymentVerificationUnavailable("Hubtel has not confirmed this payment yet.")
    data = body.get("data")
    if isinstance(data, dict):
        normalised = {}
        for key, value in data.items():
            name = key[:1].lower() + key[1:]
            if name in normalised and normalised[name] != value:
                raise services.PaymentVerificationUnavailable("Ambiguous payment details.")
            normalised[name] = value
        data = normalised
    if not isinstance(data, dict) or data.get("clientReference") != reference:
        raise services.PaymentVerificationUnavailable("Payment verification did not match the saved reference.")
    # Public endpoint contract must match these documented fields; fail closed otherwise.
    status = data.get("status")
    if status not in {"Paid", "Unpaid", "Refunded"}:
        raise services.PaymentVerificationUnavailable("Hubtel returned an unrecognised payment status.")
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
    ).update(next_check_at=now + timedelta(minutes=1), check_count=F("check_count") + 1)
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
        currency = data.get("currencyCode")
        if (not amount.is_finite() or amount != attempt.amount or amount <= 0
                or currency not in {None, "", "GHS"} or not data.get("transactionId")):
            raise ValidationError("Verified payment details do not match the saved order.")
        # Hubtel documents a nullable currencyCode. This account and all orders are GHS only.
        order = services.finalize_payment(reference, {
            "status": "success", "amount": int(amount * 100), "currency": "GHS",
            "channel": {"mobilemoney": "mobile_money", "bankcard": "card"}.get(
                str(data.get("paymentMethod", "")).lower(), str(data.get("paymentMethod", "")).lower()
            ),
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


def reconcile_due(limit=5):
    if not configured():
        return 0
    attempts = list(MarketPaymentAttempt.objects.filter(
        provider="hubtel", status__in=["initializing", "submission_unknown", "pending"],
        next_check_at__lte=timezone.now(),
    ).order_by("next_check_at")[:limit])
    for attempt in attempts:
        if attempt.check_count >= 60:
            MarketPaymentAttempt.objects.filter(pk=attempt.pk).update(
                status="attention", next_check_at=None,
                provider_message="Automatic checks exhausted. Staff reconciliation required."
            )
            continue
        try:
            reconcile(attempt.reference)
        except ValidationError:
            # Keep uncertain payments pending; retain evidence for manual reconciliation.
            MarketPaymentAttempt.objects.filter(pk=attempt.pk, status__in=[
                "initializing", "submission_unknown", "pending"
            ]).update(next_check_at=timezone.now() + timedelta(minutes=5))
    return len(attempts)
