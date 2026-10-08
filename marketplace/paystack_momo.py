"""Customer MoMo prompts. A provider response never substitutes for verification."""
from datetime import timedelta
import secrets
import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from core.identity import normalize_ghana_phone
from . import services
from .models import MarketPaymentAttempt, OnlineOrder

ACTIVE_STATUSES = ["initializing", "submission_unknown", "pending", "attention"]
NETWORKS = {"mtn", "atl", "vod"}


def ready():
    return bool(settings.PAYSTACK_SECRET_KEY and settings.PAYSTACK_CUSTOMER_MOMO_ENABLED)


def initialize(order, phone, network):
    if not ready():
        raise ValidationError("Mobile Money prompts are not available yet. Choose secure checkout.")
    if network not in NETWORKS:
        raise ValidationError("Choose MTN, AT Money or Telecel.")
    phone = normalize_ghana_phone(phone)
    validate_email(order.email)
    with transaction.atomic():
        order = OnlineOrder.objects.select_for_update().get(pk=order.pk)
        if order.payment_status in {"paid", "refunded"} or order.status != "awaiting_payment":
            raise ValidationError("This order cannot start another payment.")
        existing = order.payment_attempts.filter(status__in=ACTIVE_STATUSES).first()
        if existing:
            # Replay the saved request; never create a second charge or change provider.
            if existing.provider != "paystack":
                raise ValidationError("Complete the original payment before changing provider.")
            return existing
        services.refresh_order_reservations(order)
        attempt = MarketPaymentAttempt.objects.create(
            order=order, provider="paystack",
            reference=order.public_reference + "-" + secrets.token_hex(8).upper(),
            amount=order.total, currency="GHS", status="initializing",
            next_check_at=timezone.now() + timedelta(seconds=30),
            verification_summary={"flow": "mobile_money", "network": network,
                                  "payer_phone": phone, "expires_at": (timezone.now() + timedelta(seconds=180)).isoformat()},
        )
        order.payment_reference = attempt.reference
        order.payment_status = "pending"
        order.save(update_fields=["payment_reference", "payment_status", "updated_at"])
    payload = {
        "email": order.email, "amount": str(int(attempt.amount * 100)),
        "currency": "GHS", "reference": attempt.reference,
        "mobile_money": {"phone": "0" + phone[4:], "provider": network},
        "metadata": {"source": "kofad_market", "order_reference": order.public_reference},
    }
    state = "submission_unknown"
    message = "Your payment request is being checked. If debited, do not pay again."
    try:
        response = requests.post("https://api.paystack.co/charge",
            headers=services._paystack_headers(), json=payload,
            timeout=settings.PAYSTACK_TIMEOUT_SECONDS, allow_redirects=False)
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if (200 <= response.status_code < 300 and isinstance(body, dict) and body.get("status") is True
                and isinstance(data, dict) and data.get("reference") == attempt.reference):
            state = "pending"
            message = str(data.get("display_text") or "Approve the Mobile Money request on your phone.")[:240]
    except (requests.RequestException, ValueError):
        pass
    # Signed callbacks may confirm the payment before the HTTP response arrives.
    MarketPaymentAttempt.objects.filter(pk=attempt.pk, status="initializing").update(
        status=state, provider_message=message,
        next_check_at=timezone.now() + timedelta(seconds=10),
    )
    attempt.refresh_from_db()
    return attempt
