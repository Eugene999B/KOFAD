"""Debt Desk Mobile Money collection. An actual verified PAID provider status
is the only point at which the debt ledger can be credited.
"""
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core import debts, services
from core.identity import normalize_ghana_phone
from core.models import Party
from . import client, verification
from .models import DebtMomoCollection, DebtDebitAttempt

IN_FLIGHT = ("initializing", "pending", "submission_unknown")
# "attention" stays blocked from new collections but can be independently
# rechecked by authorized finance staff without creating another debit.
RECHECKABLE = IN_FLIGHT + ("attention",)
CHECK_WAIT = timedelta(seconds=20)
RECHECK_WAIT = timedelta(seconds=30)


def ready_for_check(row, now=None):
    """Read-only status inquiry is due; never starts another payment request."""
    now = now or timezone.now()
    return (
        row.status in RECHECKABLE
        and now >= row.created_at + CHECK_WAIT
        and (row.checked_at is None or now >= row.checked_at + RECHECK_WAIT)
    )


def _gateway_transaction(row):
    return SimpleNamespace(
        kind="receive", amount=row.amount, channel=row.channel,
        client_reference=row.reference, destination_encrypted=row.phone_encrypted,
        hubtel_transaction_id=row.provider_transaction_id, recipient_name=row.wallet_name,
    )


def start(user, branch, *, party_id, amount, phone, channel, wallet_name, request_key, consent):
    """Create one immutable network intent; never interpret the request as a receipt."""
    services.permit(user, branch, "operate_finance")
    client.require_enabled("receive")  # Only the web service with whitelisted egress.
    if not services.payment_method_enabled("momo"):
        raise ValidationError("Mobile Money debt collection is disabled in company payment settings.")
    if not consent:
        raise ValidationError("Confirm that the customer requested this exact payment.")
    if channel not in client.CHANNELS:
        raise ValidationError("Choose a valid Mobile Money network.")
    phone = normalize_ghana_phone(phone).lstrip("+")
    amount = services.money(amount)
    if amount <= Decimal("0.00"):
        raise ValidationError("Enter a positive collection amount.")
    if not str(party_id or "").isdigit():
        raise ValidationError("Select a customer debt account.")
    party_id = int(party_id)
    try:
        key = uuid.UUID(str(request_key))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("The form has expired. Reload and try again.") from exc
    previous = DebtMomoCollection.objects.filter(request_key=key).first()
    if previous:
        if previous.branch_id != branch.pk or previous.party_id != party_id or previous.requested_by_id != user.pk or previous.amount != amount or previous.channel != channel or client.unseal(previous.phone_encrypted) != phone:
            raise ValidationError("This payment request key belongs to another transaction.")
        return previous
    # Verification is distinct from wallet authorization. Never forge identity
    # if the merchant's name-inquiry scope returns HTTP 401.
    found = verification.inquiry(phone, channel)
    expected = " ".join(str(wallet_name or "").split()).casefold()
    actual = " ".join(found["wallet_name"].split()).casefold()
    if not expected or expected != actual:
        raise ValidationError("Confirmed wallet name does not match the verified wallet owner.")
    with transaction.atomic():
        services.lock_branch(branch)
        party = Party.objects.select_for_update().filter(pk=party_id, branch=branch, kind="customer").first()
        if not party:
            raise ValidationError("Choose an existing customer debt account.")
        if DebtMomoCollection.objects.filter(party=party, status__in=IN_FLIGHT + ("attention",)).exists():
            raise ValidationError("An earlier Mobile Money collection remains unresolved. Check the original request first.")
        if DebtDebitAttempt.objects.filter(
            mandate__party=party,
            status__in=("planned", "sending", "pending", "unknown", "finance_review"),
        ).exists():
            raise ValidationError("An automatic debit for this customer is planned or unresolved. Reconcile or cancel the original attempt before collecting manually.")
        outstanding = sum((due for _, due in debts.open_customer_invoices(party, for_update=True)), Decimal("0.00"))
        if amount > outstanding:
            raise ValidationError("Payment exceeds the current unpaid balance.")
        previous = DebtMomoCollection.objects.filter(request_key=key).first()
        if previous:
            return previous
        row = DebtMomoCollection.objects.create(
            request_key=key, branch=branch, party=party,
            requested_by=user, amount=amount, channel=channel,
            phone_encrypted=client.seal(phone), phone_suffix=phone[-4:],
            wallet_name=found["wallet_name"][:140],
            reference=secrets.token_hex(16),
            status="initializing", next_check_at=timezone.now() + CHECK_WAIT,
        )
    # One and only one POST for this stored intent.
    try:
        code, provider_id = client.submit(_gateway_transaction(row))
        status = "pending"
    except client.ProviderRejected as exc:
        code, provider_id, status = exc.code, "", "failed"
    except (client.SubmissionUnknown, ValidationError):
        code, provider_id, status = "", "", "submission_unknown"
    DebtMomoCollection.objects.filter(pk=row.pk, status="initializing").update(
        status=status, provider_code=code, provider_transaction_id=provider_id,
        next_check_at=(timezone.now() + CHECK_WAIT if status != "failed" else None),
    )
    row.refresh_from_db()
    try:
        services.audit(user, branch, "customer_debt.momo_request", row.reference, {
            "party": party.pk, "amount": str(amount), "state": row.status,
            "network": channel, "phone_suffix": phone[-4:],
        }, category="finance")
    except Exception:
        # An audit subsystem failure must not cause a second network send.
        import logging
        logging.getLogger(__name__).exception("Debt MoMo audit unavailable; reference remains persisted")
    return row


def reconcile(row_id):
    """Only a provider-confirmed PAID reference and amount can post a receipt."""
    row = DebtMomoCollection.objects.select_related("requested_by", "party", "branch").filter(pk=row_id).first()
    if not row or row.status not in RECHECKABLE:
        return row
    now = timezone.now()
    if not ready_for_check(row, now):
        return row
    if row.status in IN_FLIGHT and now - row.created_at > timedelta(hours=72):
        DebtMomoCollection.objects.filter(pk=row.pk, status__in=IN_FLIGHT).update(status="attention", next_check_at=None)
        row.refresh_from_db()
        return row
    from . import client as gateway
    try:
        final, provider_id = gateway.check(_gateway_transaction(row))
    except gateway.StatusUnconfirmed:
        DebtMomoCollection.objects.filter(pk=row.pk, status__in=RECHECKABLE).update(
            checked_at=now, next_check_at=now + RECHECK_WAIT,
        )
        row.refresh_from_db()
        return row
    except ValidationError:
        # A provider identity/amount/currency mismatch is never a success.
        DebtMomoCollection.objects.filter(pk=row.pk, status__in=RECHECKABLE).update(
            status="attention", checked_at=now, next_check_at=None,
        )
        row.refresh_from_db()
        return row

    if final == "pending":
        DebtMomoCollection.objects.filter(pk=row.pk, status__in=RECHECKABLE).update(
            checked_at=now, next_check_at=now + RECHECK_WAIT,
        )
    elif final == "failed":
        # An already escalated finance review could include evidence of an
        # earlier paid response. Conflicting later statuses need human review,
        # never erase that evidence by declaring the account unpaid.
        if row.status == "attention":
            DebtMomoCollection.objects.filter(pk=row.pk, status="attention").update(
                checked_at=now, next_check_at=None,
            )
        else:
            DebtMomoCollection.objects.filter(pk=row.pk, status__in=IN_FLIGHT).update(
                status="failed", provider_transaction_id=provider_id,
                checked_at=now, next_check_at=None,
            )
    elif final == "success":
        try:
            with transaction.atomic():
                services.lock_branch(row.branch)
                locked = DebtMomoCollection.objects.select_for_update().get(pk=row.pk)
                if locked.status not in RECHECKABLE:
                    return locked
                # This method verifies nothing itself; it is invoked only
                # after client.check() confirms the exact provider reference,
                # amount, network transaction ID and final paid status.
                receipt = debts.post_customer_payment(
                    locked.requested_by, locked.branch,
                    {"party": str(locked.party_id), "amount": str(locked.amount),
                     "method": "momo", "reference": str(provider_id),
                     "note": "Verified Mobile Money debt collection " + locked.reference},
                    str(locked.id), provider_verified_receipt=True,
                )
                locked.receipt = receipt
                locked.status = "success"
                locked.provider_transaction_id = provider_id
                locked.checked_at = now
                locked.next_check_at = None
                locked.save(update_fields=[
                    "receipt", "status", "provider_transaction_id",
                    "checked_at", "next_check_at", "updated_at",
                ])
                return locked
        except Exception:
            # Paid at provider but ledger not posted: preserve the finance hold.
            # A staff-initiated *read-only* provider recheck may recover the
            # exact receipt on a later open business day. Never POST again.
            DebtMomoCollection.objects.filter(pk=row.pk, status__in=RECHECKABLE).update(
                status="attention", provider_transaction_id=provider_id,
                checked_at=now, next_check_at=None,
            )
    else:
        DebtMomoCollection.objects.filter(pk=row.pk, status__in=RECHECKABLE).update(
            status="attention", provider_transaction_id=provider_id,
            checked_at=now, next_check_at=None,
        )
    row.refresh_from_db()
    return row


def reconcile_due(limit=8):
    now = timezone.now()
    due = list(DebtMomoCollection.objects.filter(
        status__in=IN_FLIGHT, next_check_at__lte=now,
        created_at__lte=now - CHECK_WAIT,
    ).order_by("next_check_at").values_list("pk", flat=True)[:limit])
    for row_id in due:
        reconcile(row_id)
    return len(due)
