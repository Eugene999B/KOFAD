"""Phone-control verification before a customer's first Paystack MoMo request.

An SMS OTP proves control of the selected phone, NOT the wallet owner's
registered name. Only Paystack/the mobile network handles the customer's PIN.
"""
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.identity import normalize_ghana_phone
from . import paystack_momo, services
from .models import OnlineOrder, VerifiedMomoPhone

SESSION_KEY = "market_momo_phone_pending"
LIFETIME = 600


def pending_for_order(request, customer, order):
    saved = request.session.get(SESSION_KEY)
    if not isinstance(saved, dict):
        return None
    try:
        valid = (
            saved.get("customer_id") == customer.pk
            and saved.get("order_id") == str(order.pk)
            and float(saved.get("expires_at", 0)) > timezone.now().timestamp()
            and saved.get("network") in paystack_momo.NETWORKS
            and normalize_ghana_phone(saved.get("phone", "")) == saved.get("phone")
        )
    except (TypeError, ValueError, ValidationError):
        valid = False
    return saved if valid else None


def begin(request, customer, order, phone, network):
    """Return True while waiting for first-time SMS, False if charge was started."""
    if order.customer_id != customer.pk or order.status != "awaiting_payment" or order.payment_status == "paid":
        raise ValidationError("This order cannot start a payment.")
    if not paystack_momo.ready():
        raise ValidationError("Mobile Money payment is not activated.")
    if network not in paystack_momo.NETWORKS:
        raise ValidationError("Choose a supported Mobile Money network.")
    phone = normalize_ghana_phone(phone)
    if VerifiedMomoPhone.objects.filter(customer=customer, phone=phone).exists():
        paystack_momo.initialize(order, phone, network)
        request.session.pop(SESSION_KEY, None)
        return False
    saved = pending_for_order(request, customer, order)
    if saved:
        if saved["phone"] != phone or saved["network"] != network:
            raise ValidationError("Finish or cancel the existing phone verification before changing payment details.")
        return True
    if not settings.CUSTOMER_OTP_ENABLED:
        raise ValidationError("SMS verification is not active yet. Your order is saved and no payment was requested.")
    if order.payment_attempts.filter(status__in=paystack_momo.ACTIVE_STATUSES).exists():
        raise ValidationError("Your previous payment is being checked. Do not start another charge.")
    # send_otp has per-phone and per-request quotas, a digest-only code, and attempt lockouts.
    services.send_otp(phone, "momo", request=request)
    request.session[SESSION_KEY] = {
        "customer_id": customer.pk, "order_id": str(order.pk),
        "phone": phone, "network": network,
        "expires_at": (timezone.now() + timedelta(seconds=LIFETIME)).timestamp(),
    }
    request.session.modified = True
    return True


def complete(request, customer, order, *, code=None, resend=False):
    saved = pending_for_order(request, customer, order)
    if not saved:
        raise ValidationError("Phone verification expired. Start the payment again.")
    if order.status != "awaiting_payment" or order.payment_status == "paid":
        raise ValidationError("This order cannot start a new payment.")
    if resend:
        services.send_otp(saved["phone"], "momo", request=request)
        saved["expires_at"] = (timezone.now() + timedelta(seconds=LIFETIME)).timestamp()
        request.session[SESSION_KEY] = saved
        request.session.modified = True
        return False
    services.verify_otp(saved["phone"], code, "momo")
    with transaction.atomic():
        VerifiedMomoPhone.objects.get_or_create(
            customer=customer, phone=saved["phone"],
        )
    request.session.pop(SESSION_KEY, None)
    # Even if this provider submission times out, initialize() persists one
    # reference and blocks duplicate charges while reconciliation continues.
    paystack_momo.initialize(order, saved["phone"], saved["network"])
    return True
