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
from .models import MarketPaymentAttempt, OnlineOrder, VerifiedMomoPhone

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
    if not VerifiedMomoPhone.objects.filter(customer=order.customer, phone=phone).exists():
        raise ValidationError("Verify control of this Mobile Money number by SMS first.")
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
    provider_status = ""
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
            provider_status = str(data.get("status", ""))
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
    if provider_status:
        remember_challenge(attempt, {"status": provider_status})
        attempt.refresh_from_db()
    return attempt


def remember_challenge(attempt, data):
    from core.paystack_challenges import challenge_message
    with transaction.atomic():
        current = MarketPaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
        if current.status not in ACTIVE_STATUSES or current.provider != "paystack":
            return
        summary = dict(current.verification_summary or {})
        summary["charge_status"] = str(data.get("status", ""))
        current.verification_summary = summary
        if summary["charge_status"] == "send_otp" or summary["charge_status"].startswith("send_"):
            current.provider_message = challenge_message(data)
        if summary["charge_status"].startswith("send_") and summary["charge_status"] != "send_otp":
            current.status = "attention"
        current.save(update_fields=["verification_summary", "provider_message", "status"])


def submit_otp(order, otp):
    from core.paystack_challenges import charge_step
    with transaction.atomic():
        current_order = OnlineOrder.objects.select_for_update().get(pk=order.pk)
        if current_order.payment_status in {"paid", "refunded"} or current_order.status != "awaiting_payment":
            raise ValidationError("This payment cannot accept another code.")
        attempt = current_order.payment_attempts.select_for_update().filter(
            provider="paystack", reference=current_order.payment_reference, status="pending").first()
        if not attempt:
            raise ValidationError("No pending Mobile Money verification was found.")
        summary = dict(attempt.verification_summary or {})
        if summary.get("flow") != "mobile_money" or summary.get("charge_status") != "send_otp":
            raise ValidationError("This payment is not requesting a one-time code.")
        now = timezone.now().timestamp()
        if int(summary.get("otp_attempts", 0)) >= 5 or float(summary.get("otp_next_at", 0)) > now:
            raise ValidationError("Please wait before trying another code, or contact KOFAD.")
        summary["otp_attempts"] = int(summary.get("otp_attempts", 0)) + 1
        summary["otp_next_at"] = now + 30
        attempt.verification_summary = summary
        attempt.save(update_fields=["verification_summary"])
    data = charge_step(attempt.reference, otp=otp)
    remember_challenge(attempt, data)
    MarketPaymentAttempt.objects.filter(pk=attempt.pk, status="pending").update(next_check_at=timezone.now())
