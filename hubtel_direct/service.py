"""Persist-before-send, two-person payout approval and independent settlement."""
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.identity import normalize_ghana_phone
from core import services as accounting
from marketplace import services as market_services
from marketplace.models import MarketPaymentAttempt, OnlineOrder, VerifiedMomoPhone
from . import client
from .models import DirectTransaction

ACTIVE = ("initializing", "submission_unknown", "pending")
BLOCKING_ATTEMPTS = ("initializing", "submission_unknown", "pending", "attention")
MIN_STATUS_WAIT = timedelta(minutes=5)


def _reference():
    return secrets.token_hex(16)


def _normalized_destination(value):
    return normalize_ghana_phone(value).lstrip("+")


def _persisted(kind, amount, dest, **attributes):
    return DirectTransaction.objects.create(
        kind=kind, client_reference=_reference(), amount=client.money(amount),
        destination_encrypted=client.seal(dest), destination_suffix=str(dest)[-4:],
        **attributes,
    )


def create_collection(customer, order_id, network):
    """Buyer cannot choose their phone or amount. Requires prior account-phone OTP."""
    client.require_enabled("receive")
    if network not in client.CHANNELS:
        raise ValidationError("Choose a supported Mobile Money network.")
    if not customer or not customer.active or not customer.verified_at or not customer.phone:
        raise PermissionDenied("Sign in with a verified Ghana phone number.")
    phone = _normalized_destination(customer.phone)
    if not VerifiedMomoPhone.objects.filter(
        customer=customer, phone__in=[customer.phone, "+" + phone, phone]
    ).exists():
        raise ValidationError("Verify your registered Mobile Money number with KOFAD before a payment prompt.")
    with transaction.atomic():
        order = OnlineOrder.objects.select_for_update().get(pk=order_id, customer=customer)
        if order.status != "awaiting_payment" or order.payment_status in ("paid", "refunded"):
            raise ValidationError("This order is not eligible for another payment.")
        old = order.payment_attempts.filter(status__in=BLOCKING_ATTEMPTS).order_by("-created_at").first()
        if old:
            if old.provider == "hubtel_direct":
                existing = DirectTransaction.objects.filter(order=order, client_reference=old.reference).first()
                if existing:
                    return existing  # Never send the original POST a second time.
            raise ValidationError("Resolve the existing payment before requesting another.")
        market_services.refresh_order_reservations(order)
        tx = _persisted(
            "receive", order.total, phone, customer=customer, order=order,
            channel=network, recipient_name=customer.full_name[:140], status="initializing",
            next_check_at=timezone.now() + MIN_STATUS_WAIT,
        )
        MarketPaymentAttempt.objects.create(
            order=order, provider="hubtel_direct", reference=tx.client_reference,
            amount=tx.amount, currency="GHS", status="initializing",
            provider_message="Waiting for Mobile Money approval.",
            next_check_at=tx.next_check_at,
            verification_summary={"flow": "hubtel_direct", "network": network},
        )
        order.payment_reference = tx.client_reference
        order.payment_status = "pending"
        order.save(update_fields=["payment_reference", "payment_status", "updated_at"])
    dispatch_once(tx)
    tx.refresh_from_db()
    return tx


def create_payout(user, kind, amount, destination, recipient_name, channel="", bank_code="", idempotency_key=None):
    if kind not in ("send_momo", "send_bank"):
        raise ValidationError("Choose a supported payout type.")
    client.require_enabled(kind)
    amount = client.money(amount)
    if amount > client.money(settings.HUBTEL_DIRECT_MAX_PAYOUT_GHS):
        raise ValidationError("Payout exceeds the configured single-transaction limit.")
    recipient_name = str(recipient_name or "").strip()
    if not 2 <= len(recipient_name) <= 140:
        raise ValidationError("Enter a recipient name for independent review.")
    if kind == "send_momo":
        if channel not in client.CHANNELS or bank_code:
            raise ValidationError("Choose the recipient's Mobile Money network.")
        destination = _normalized_destination(destination)
    else:
        if bank_code not in client.BANK_CODES or channel:
            raise ValidationError("Choose a supported Ghana bank code.")
        if not isinstance(destination, str) or not destination.isdigit() or not 6 <= len(destination) <= 24:
            raise ValidationError("Enter a valid bank account number.")
    try:
        request_key = uuid.UUID(str(idempotency_key))
        if request_key.version != 4:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("A fresh UUIDv4 Idempotency-Key is required for each payout intent.") from exc
    fingerprint_input = json.dumps({
        "actor": user.pk, "kind": kind, "amount": str(amount),
        "destination": destination, "recipient_name": recipient_name,
        "channel": channel, "bank_code": bank_code,
    }, sort_keys=True, separators=(",", ":")).encode()
    fingerprint = hmac.new(settings.SECRET_KEY.encode(), fingerprint_input, hashlib.sha256).hexdigest()
    existing = DirectTransaction.objects.filter(request_key=request_key).first()
    if existing:
        if existing.requested_by_id == user.pk and hmac.compare_digest(existing.request_fingerprint, fingerprint):
            return existing
        raise ValidationError("This payout request key was already used for different details.")
    try:
        tx = _persisted(
            kind, amount, destination, recipient_name=recipient_name,
            requested_by=user, channel=channel, bank_code=bank_code,
            status="approval_pending", request_key=request_key,
            request_fingerprint=fingerprint,
        )
    except IntegrityError:
        existing = DirectTransaction.objects.filter(request_key=request_key).first()
        if existing and existing.requested_by_id == user.pk and hmac.compare_digest(existing.request_fingerprint, fingerprint):
            return existing
        raise ValidationError("Duplicate payout request could not be reconciled.")
    accounting.audit(user, None, "hubtel.payout_requested", tx.client_reference,
                     {"kind": kind, "amount": str(amount), "destination_last4": tx.destination_suffix},
                     category="finance", severity="high")
    return tx


def approve_payout(tx_id, approver):
    """Approval is a separate actor and sends only once on the first approval."""
    with transaction.atomic():
        tx = DirectTransaction.objects.select_for_update().get(pk=tx_id)
        if tx.kind == "receive" or tx.status != "approval_pending" or tx.approved_by_id:
            raise ValidationError("This payout is not awaiting approval.")
        if tx.requested_by_id == approver.pk:
            raise PermissionDenied("The payout creator cannot approve their own transfer.")
        client.require_enabled(tx.kind)
        tx.approved_by = approver
        tx.status = "initializing"
        tx.next_check_at = timezone.now() + MIN_STATUS_WAIT
        tx.save(update_fields=["approved_by", "status", "next_check_at", "updated_at"])
        accounting.audit(approver, None, "hubtel.payout_approved", tx.client_reference,
                         {"kind": tx.kind, "amount": str(tx.amount)},
                         category="finance", severity="high")
    dispatch_once(tx)
    tx.refresh_from_db()
    return tx


def dispatch_once(tx):
    """Only the transaction creation or first approval calls this, NEVER a poll/retry."""
    if tx.status != "initializing":
        raise ValidationError("A previously submitted transfer cannot be sent again.")
    try:
        code, provider_id = client.submit(tx)
        status = "pending"
    except client.ProviderRejected as exc:
        code, provider_id, status = exc.code, "", "failed"
    except (client.SubmissionUnknown, ValidationError):
        code, provider_id, status = "", "", "submission_unknown"
    DirectTransaction.objects.filter(pk=tx.pk, status="initializing").update(
        status=status, provider_code=code, hubtel_transaction_id=provider_id,
        next_check_at=timezone.now() + MIN_STATUS_WAIT,
    )
    if tx.kind == "receive":
        MarketPaymentAttempt.objects.filter(
            reference=tx.client_reference, provider="hubtel_direct", status="initializing"
        ).update(
            status=status, provider_message=(
                "Hubtel request outcome uncertain: do not start another payment."
                if status == "submission_unknown" else "Waiting for Hubtel verification."
                if status == "pending" else "Hubtel rejected this request."
            ),
        )
        if status == "failed":
            OnlineOrder.objects.filter(
                pk=tx.order_id, payment_reference=tx.client_reference
            ).exclude(payment_status__in=["paid", "refunded"]).update(payment_status="failed")
    accounting.audit(tx.requested_by, None, "hubtel.submission_recorded", tx.client_reference,
                     {"kind": tx.kind, "state": status, "response_code": code},
                     category="finance", severity="warning" if status != "pending" else "info")


def note_callback(payload):
    """Untrusted callback can ONLY record receipt, never change money or order state."""
    if not isinstance(payload, dict):
        return
    data = client._field(payload, "Data")
    if not isinstance(data, dict):
        return
    ref = client._field(data, "ClientReference")
    if not isinstance(ref, str) or len(ref) > 36:
        return
    DirectTransaction.objects.filter(
        client_reference=ref, status__in=ACTIVE, callback_seen_at__isnull=True
    ).update(callback_seen_at=timezone.now())


def reconcile(reference):
    """Verify after >=5 minutes, using the separate whitelisted Status Check API."""
    now = timezone.now()
    tx = DirectTransaction.objects.filter(client_reference=reference).first()
    if not tx or tx.status not in ACTIVE:
        return tx
    if now < tx.created_at + MIN_STATUS_WAIT:
        return tx
    # Claim the next check with a durable lease for cross-worker concurrency.
    claimed = DirectTransaction.objects.filter(
        pk=tx.pk, status__in=ACTIVE, next_check_at__lte=now
    ).update(next_check_at=now + MIN_STATUS_WAIT, check_count=F("check_count") + 1)
    if not claimed:
        return tx
    try:
        final, provider_id = client.check(tx)
    except client.StatusUnconfirmed:
        return tx
    except ValidationError:
        final, provider_id = "attention", tx.hubtel_transaction_id

    with transaction.atomic():
        current = DirectTransaction.objects.select_for_update().get(pk=tx.pk)
        if current.status not in ACTIVE:
            return current
        if final == "pending":
            current.checked_at = timezone.now()
            current.save(update_fields=["checked_at", "updated_at"])
            return current
        if current.kind == "receive":
            attempt = MarketPaymentAttempt.objects.select_for_update().get(
                reference=current.client_reference, provider="hubtel_direct"
            )
            if final == "success":
                # Existing stock/ledger/receipt logic: exactly once per order.
                try:
                    market_services.finalize_payment(current.client_reference, {
                        "status": "success", "amount": int(current.amount * 100),
                        "currency": "GHS", "channel": "mobile_money",
                    }, expected_provider="hubtel_direct")
                except ValidationError:
                    final = "attention"
            if final != "success":
                attempt.status = "attention" if final == "attention" else "failed"
                attempt.next_check_at = None
                attempt.provider_message = "Hubtel Direct requires management reconciliation." if final == "attention" else "Hubtel confirmed payment failure."
                attempt.save(update_fields=["status", "next_check_at", "provider_message"])
                if final == "failed":
                    OnlineOrder.objects.filter(
                        pk=current.order_id, payment_reference=current.client_reference
                    ).exclude(payment_status__in=["paid", "refunded"]).update(payment_status="failed")
        current.status = final
        current.hubtel_transaction_id = provider_id
        current.checked_at = timezone.now()
        current.next_check_at = None
        current.save(update_fields=["status", "hubtel_transaction_id", "checked_at", "next_check_at", "updated_at"])
        accounting.audit(current.requested_by, None, "hubtel.status_verified", current.client_reference,
                         {"kind": current.kind, "verified_status": final},
                         category="finance", severity="high" if final == "attention" else "info")
        return current


def reconcile_due(limit=20):
    now = timezone.now()
    due = list(DirectTransaction.objects.filter(
        status__in=ACTIVE, next_check_at__lte=now, created_at__lte=now - MIN_STATUS_WAIT
    ).order_by("next_check_at").values_list("client_reference", flat=True)[:limit])
    for ref in due:
        reconcile(ref)
    return len(due)
