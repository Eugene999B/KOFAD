"""Recover checkout payments when a customer closes the provider tab."""
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from .models import MarketPaymentAttempt, OnlineOrder
from . import services


def reconcile_due(limit=5):
    if not settings.PAYSTACK_SECRET_KEY:
        return 0
    now = timezone.now()
    # Older workers treated an unopened hosted checkout as a final failure.
    # Recheck only the current, recent checkout; never resurrect a replaced intent.
    recoverable = Q(
        status="failed", order__status="awaiting_payment",
        order__payment_reference=F("reference"),
        provider_message="Provider confirmed unsuccessful payment.",
        created_at__gte=now - timedelta(hours=24),
    ) & ~Q(authorization_url="")
    rows = list(MarketPaymentAttempt.objects.filter(provider="paystack").filter(
        Q(status__in=["initializing", "submission_unknown", "pending"]) | recoverable,
    ).filter(Q(next_check_at__lte=now) | Q(next_check_at__isnull=True)).order_by("created_at")[:limit])
    for attempt in rows:
        claimed = MarketPaymentAttempt.objects.filter(pk=attempt.pk).filter(
            Q(next_check_at__lte=now) | Q(next_check_at__isnull=True),
        ).update(next_check_at=now + timedelta(minutes=1), check_count=F("check_count") + 1)
        if not claimed:
            continue
        charge_status = None
        direct_momo = (attempt.verification_summary or {}).get("flow") == "mobile_money"
        try:
            if (attempt.verification_summary or {}).get("flow") == "mobile_money":
                from core.paystack_challenges import charge_step
                from .paystack_momo import remember_challenge
                try:
                    charge = charge_step(attempt.reference)
                    charge_status = str(charge.get("status", ""))
                    remember_challenge(attempt, charge)
                except ValidationError:
                    pass
            verified = services.verify_paystack(attempt.reference)
            state = verified.get("status")
            if state == "success":
                services.finalize_payment(attempt.reference, verified)
            elif state == "abandoned" and not direct_momo:
                # Paystack reports abandoned before the buyer completes its hosted
                # page. Retain the original checkout and block a second charge.
                with transaction.atomic():
                    current = MarketPaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
                    order = OnlineOrder.objects.select_for_update().get(pk=attempt.order_id)
                    if (current.status == "failed" and order.status == "awaiting_payment"
                            and order.payment_reference == current.reference
                            and order.payment_status not in {"paid", "refunded"}):
                        current.status = "pending"
                        current.provider_message = "Complete payment on the original Paystack checkout."
                        current.save(update_fields=["status", "provider_message"])
                        order.payment_status = "pending"
                        order.save(update_fields=["payment_status", "updated_at"])
            elif (state in {"failed", "reversed"} or (direct_momo and state == "abandoned")) and (
                not direct_momo or charge_status in {"failed", "abandoned", "reversed"}
            ):
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
