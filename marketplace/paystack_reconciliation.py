"""Recover checkout payments when a customer closes the provider tab."""
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.utils import timezone
from .models import MarketPaymentAttempt, OnlineOrder
from . import services


def reconcile_due(limit=5):
    if not settings.PAYSTACK_SECRET_KEY:
        return 0
    now = timezone.now()
    rows = list(MarketPaymentAttempt.objects.filter(
        provider="paystack", status__in=["initializing", "submission_unknown", "pending"],
    ).filter(Q(next_check_at__lte=now) | Q(next_check_at__isnull=True)).order_by("created_at")[:limit])
    for attempt in rows:
        claimed = MarketPaymentAttempt.objects.filter(pk=attempt.pk).filter(
            Q(next_check_at__lte=now) | Q(next_check_at__isnull=True),
        ).update(next_check_at=now + timedelta(minutes=1), check_count=F("check_count") + 1)
        if not claimed:
            continue
        try:
            if (attempt.verification_summary or {}).get("flow") == "mobile_money":
                from core.paystack_challenges import charge_step
                from .paystack_momo import remember_challenge
                try:
                    remember_challenge(attempt, charge_step(attempt.reference))
                except ValidationError:
                    pass
            verified = services.verify_paystack(attempt.reference)
            state = verified.get("status")
            if state == "success":
                services.finalize_payment(attempt.reference, verified)
            elif state in {"failed", "abandoned", "reversed"}:
                MarketPaymentAttempt.objects.filter(pk=attempt.pk, status__in=["initializing", "submission_unknown", "pending"]).update(
                    status="failed", next_check_at=None, provider_message="Provider confirmed unsuccessful payment.",
                )
                OnlineOrder.objects.filter(pk=attempt.order_id, payment_reference=attempt.reference).exclude(
                    payment_status__in=["paid", "refunded"],
                ).update(payment_status="failed", updated_at=timezone.now())
        except services.PaymentVerificationUnavailable:
            pass
        except ValidationError:
            MarketPaymentAttempt.objects.filter(pk=attempt.pk).exclude(status="success").update(
                status="attention", next_check_at=None, provider_message="Verified details require staff review.",
            )
        if attempt.created_at < now - timedelta(hours=24):
            MarketPaymentAttempt.objects.filter(pk=attempt.pk, status__in=[
                "initializing", "submission_unknown", "pending",
            ]).update(status="attention", next_check_at=None, provider_message="Confirmation requires staff review.")
    return len(rows)
