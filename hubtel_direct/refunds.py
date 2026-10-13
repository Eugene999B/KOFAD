"""Conservative Hubtel full-order refund review and one-shot release.

Hubtel's October 2026 Refund API uses its ORIGINAL orderId, not KOFAD's
clientReference, and does not document an independently queryable final refund
status. Callbacks are therefore advisory; they never mark a refund as paid.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core import services as core_services
from marketplace.models import MarketPaymentAttempt, MarketReturnRequest
from . import client, debit_api
from .models import HubtelRefundCase

HUBTEL_PROVIDERS = ("hubtel", "hubtel_direct")
ORDER_ID_PATTERN = re.compile(r"^[a-fA-F0-9]{24,64}$")


def _verified_original(item):
    """Resolve a successful, original payment from KOFAD's immutable ledger."""
    order = item.order
    # Hubtel's provided Refund API is for Mobile Money only. Hubtel card
    # checkout is a different rail and must never reach this endpoint.
    if order.payment_channel != "mobile_money":
        raise ValidationError("Hubtel's Mobile Money Refund API cannot refund a card or bank payment.")
    if order.payment_status != "paid" or not order.payment_reference or not order.paid_at:
        raise ValidationError("The original order must have a confirmed Hubtel payment.")
    if order.paid_at < timezone.now() - timedelta(days=45):
        raise ValidationError("Hubtel's automatic refund window is 45 days. Request manual Hubtel assistance.")
    payment = MarketPaymentAttempt.objects.filter(
        order=order, reference=order.payment_reference,
        provider__in=HUBTEL_PROVIDERS, status="success", verified_at__isnull=False,
        amount=order.total, currency="GHS",
    ).first()
    if not payment:
        raise ValidationError("KOFAD has no independently verified Hubtel payment for this order.")
    return payment


def _validate_return(item):
    if item.resolution != "refund" or not item.core_return_request_id:
        raise ValidationError("An approved, posted KOFAD return is required for a Hubtel refund.")
    if item.core_return_request.status != "approved" or item.status not in {
        "processing", "refund_attention",
    }:
        raise ValidationError("Physical return approval is required before preparing the payment refund.")
    # A return approved for a DIFFERENT original KOFAD sale cannot authorize
    # this online order's Hubtel money to be returned.
    if (not item.order.sale_document_id
            or item.core_return_request.sale_id != item.order.sale_document_id
            or not item.core_return_request.posted_id):
        raise ValidationError("The approved and posted physical return must match this exact original sale.")
    from marketplace.services import market_return_value
    amount = market_return_value(item)
    if amount < Decimal("1.00") or amount != item.order.total:
        raise ValidationError(
            "Hubtel refunds the original entire order, not selected products. "
            "For partial returns, use the replacement/manual finance workflow."
        )
    if item.order.return_requests.exclude(pk=item.pk).filter(
        hubtel_refund_case__status__in=[
            "awaiting_order_id", "review_pending", "submission_unknown",
            "provider_pending", "callback_received", "reconciled",
        ]
    ).exists():
        raise ValidationError("This order already has a Hubtel refund under review.")
    return amount


@transaction.atomic
def prepare_case(user, item, *, branch):
    """Queue human review. This operation does not call Hubtel or move funds."""
    core_services.permit(user, branch, "operate_finance")
    # Lock ONLY the return record; PostgreSQL does not allow FOR UPDATE
    # against the nullable side of the core_return_request relation.
    item = MarketReturnRequest.objects.select_for_update(of=("self",)).select_related(
        "order", "core_return_request",
    ).get(pk=item.pk, order__branch=branch)
    existing = HubtelRefundCase.objects.filter(return_request=item).first()
    if existing:
        return existing
    amount = _validate_return(item)
    payment = _verified_original(item)
    # Only a provider-verified original identifier from an already VERIFIED
    # payment attempt may become a refund API destination. A typed ID or a
    # customer screenshot is never enough.
    summary = payment.verification_summary or {}
    raw_order_id = str(summary.get("hubtel_order_id") or "").strip()
    provider_order_id = raw_order_id if ORDER_ID_PATTERN.fullmatch(raw_order_id) else None
    row = HubtelRefundCase.objects.create(
        return_request=item, original_hubtel_order_id=provider_order_id,
        original_transaction_reference=payment.reference, amount=amount,
        created_by=user, status="review_pending" if provider_order_id else "awaiting_order_id",
        review_note=(
            "The original Hubtel Order ID is authenticated and ready for a different manager to review."
            if provider_order_id else
            "The original Hubtel Order ID is missing from verified KOFAD payment evidence. "
            "Do not use the client reference or guess an ID. Finance must reconcile provider evidence."
        )[:240],
    )
    core_services.audit(
        user, branch, "hubtel_refund.case_prepared", str(row.pk),
        {"return": str(item.pk), "payment_ref": payment.reference,
         "amount": str(amount), "provider_id_verified": bool(provider_order_id)},
        category="finance", severity="high",
    )
    return row


def submit_approved_case(user, refund_id, *, branch):
    """Single POST only, with dual control and a durable pre-submission claim."""
    core_services.permit(user, branch, "manage_company")
    debit_api.gate("refund")  # OFF until real merchant-approved test and validation
    with transaction.atomic():
        core_services.lock_branch(branch)
        row = HubtelRefundCase.objects.select_for_update(of=("self",)).select_related(
            "return_request__order", "return_request__core_return_request",
        ).get(pk=refund_id, return_request__order__branch=branch)
        if row.status != "review_pending" or row.submitted_at or row.approved_by_id:
            raise ValidationError("This Hubtel refund is not awaiting its first approval.")
        if row.created_by_id == user.pk:
            raise ValidationError("A different manager must approve the refund request.")
        item = row.return_request
        amount = _validate_return(item)
        payment = _verified_original(item)
        valid_id = str((payment.verification_summary or {}).get("hubtel_order_id") or "")
        if not row.original_hubtel_order_id or not ORDER_ID_PATTERN.fullmatch(valid_id):
            raise ValidationError("A provider-verified original Hubtel Order ID is still missing.")
        if valid_id != row.original_hubtel_order_id or row.original_transaction_reference != payment.reference:
            raise ValidationError("The original Hubtel payment evidence has changed. Review again.")
        if amount != row.amount or row.amount != item.order.total:
            raise ValidationError("Refund amount does not match the original full payment.")
        # Record irreversible intent BEFORE the external call. A timeout is
        # never evidence of failure and cannot authorize a second POST.
        row.approved_by = user
        row.reviewed_at = timezone.now()
        row.submitted_at = timezone.now()
        row.status = "submission_unknown"
        row.save(update_fields=["approved_by", "reviewed_at", "submitted_at", "status"])
        core_services.audit(
            user, branch, "hubtel_refund.one_time_submission_claimed", str(row.pk),
            {"original_ref": row.original_transaction_reference,
             "amount": str(row.amount), "hubtel_order_id": row.original_hubtel_order_id},
            category="finance", severity="high",
        )
        order_id = row.original_hubtel_order_id
    try:
        debit_api.submit_refund(hubtel_order_id=order_id)
    except (client.StatusUnconfirmed, client.SubmissionUnknown, ValidationError):
        # Keep submission_unknown. Human support must check the provider; no retry.
        return HubtelRefundCase.objects.get(pk=refund_id)
    HubtelRefundCase.objects.filter(pk=refund_id, status="submission_unknown").update(
        status="provider_pending", last_provider_code="0001",
    )
    return HubtelRefundCase.objects.get(pk=refund_id)


def note_callback(payload):
    """Record advisory evidence only; unauthenticated callbacks never settle."""
    if not isinstance(payload, dict):
        return False
    data = client._field(payload, "data")
    if not isinstance(data, dict):
        return False
    order_id = client._field(data, "orderId")
    if not isinstance(order_id, str) or not ORDER_ID_PATTERN.fullmatch(order_id):
        return False
    with transaction.atomic():
        row = HubtelRefundCase.objects.select_for_update().filter(
            original_hubtel_order_id=order_id,
            status__in=["submission_unknown", "provider_pending", "callback_received"],
        ).first()
        if not row:
            return False
        # Do not trust provider-supplied amount/status for final settlement.
        row.callback_seen_at = timezone.now()
        row.status = "callback_received"
        row.last_provider_code = str(client._field(payload, "responseCode") or "")[:24]
        row.save(update_fields=["callback_seen_at", "status", "last_provider_code"])
    return True
