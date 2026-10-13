"""Net-pay-only Hubtel salary batches, with verified recipients and dual approval.

All outward money operations require the separate disbursement account,
the specific send scope, the static web IP, sufficient funds and opt-in
activation. Payroll payments are posted ONLY after verified Hubtel settlement.
"""
import uuid
from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core import payroll_engine
from core import services as core_services
from core.models import Company, PayrollEntry, PayrollPeriod, Worker
from . import client, service, verification
from .models import WorkerTransferProfile, PayrollTransferBatch, PayrollTransferLine, DirectTransaction


def save_recipient(user, worker, *, payment_type, destination, channel="", bank_code=""):
    """Manager verifies the actual registered beneficiary from the web IP."""
    if not user.is_superuser and not user.has_perm("core.manage_company"):
        raise PermissionDenied("Only company management may change salary destinations.")
    if payment_type == "momo":
        result = verification.inquiry(destination, channel)
        number = result["phone"]
        name = result["wallet_name"]
        code = channel
        bank = ""
    elif payment_type == "bank":
        result = verification.bank_inquiry(bank_code, destination)
        number = str(destination).strip()
        name = result["name"]
        code = ""
        bank = bank_code
    else:
        raise ValidationError("Choose Mobile Money or a verified Ghana bank.")
    with transaction.atomic():
        # Editing a recipient while a prepared/processing batch exists cannot
        # silently re-route payroll. Finish or explicitly review that batch
        # before accepting new beneficiary instructions.
        if PayrollTransferLine.objects.filter(
            entry__worker=worker, batch__status__in=["awaiting_approval", "processing"],
        ).exists():
            raise ValidationError(
                "This worker has an active Hubtel salary batch. Resolve the batch "
                "before changing their verified bank or wallet destination."
            )
        obj, _ = WorkerTransferProfile.objects.select_for_update().get_or_create(
            worker=worker,
            defaults={
                "payment_type": payment_type, "channel": code,
                "bank_code": bank, "destination_encrypted": client.seal(number),
                "destination_suffix": number[-4:], "provider_name": name,
                "verified_at": timezone.now(), "verified_by": user,
            },
        )
        obj.payment_type = payment_type
        obj.channel = code
        obj.bank_code = bank
        obj.destination_encrypted = client.seal(number)
        obj.destination_suffix = number[-4:]
        obj.provider_name = name
        obj.verified_at = timezone.now()
        obj.verified_by = user
        obj.save()
    core_services.audit(user, worker.branch, "payroll.hubtel_beneficiary_verified",
                     str(worker.pk), {
                         "employee_code": worker.employee_code,
                         "payment_type": payment_type, "destination_last4": number[-4:],
                         "provider_name": name,
                     }, category="finance", severity="high")
    return obj


@transaction.atomic
def prepare_batch(user, period):
    if not user.has_perm("core.operate_finance"):
        raise PermissionDenied("Finance permission is required to prepare salary payments.")
    if period.status != "locked":
        raise ValidationError("Payroll must be approved and locked before online disbursement.")
    existing = PayrollTransferBatch.objects.filter(period=period).first()
    if existing:
        return existing
    # Lock each entry so the net balance cannot change during preparation.
    entries = list(PayrollEntry.objects.select_for_update().filter(
        period=period, net_pay__gt=0
    ).select_related("worker"))
    outstanding = [entry for entry in entries if entry.balance > 0]
    if not outstanding:
        raise ValidationError("No salary balances remain to pay.")
    if any(entry.worker.branch_id != period.branch_id for entry in outstanding):
        raise ValidationError("A payroll worker belongs to another branch.")
    max_single = client.money(settings.HUBTEL_DIRECT_MAX_PAYOUT_GHS)
    max_daily = client.money(settings.HUBTEL_DIRECT_DAILY_PAYOUT_GHS)
    requested = sum((entry.balance for entry in outstanding), Decimal("0.00"))
    if requested > max_daily:
        raise ValidationError("Payroll exceeds today's approved total Hubtel disbursement limit.")
    for entry in outstanding:
        if entry.balance > max_single:
            raise ValidationError(
                f"{entry.worker.employee_code}: salary exceeds the approved single-payment limit."
            )
        profile = WorkerTransferProfile.objects.filter(worker=entry.worker).first()
        if not profile or profile.verified_at < timezone.now() - timedelta(days=30):
            raise ValidationError(
                f"{entry.worker.employee_code}: verify salary bank/MoMo destination within 30 days."
            )
        if profile.payment_type == "momo" and profile.channel not in client.CHANNELS:
            raise ValidationError("Invalid worker Mobile Money network.")
        if profile.payment_type == "bank" and profile.bank_code not in client.BANK_CODES:
            raise ValidationError("Invalid worker bank code.")
    batch = PayrollTransferBatch.objects.create(
        period=period, created_by=user, expected_amount=requested, status="awaiting_approval",
    )
    for entry in outstanding:
        profile = WorkerTransferProfile.objects.get(worker=entry.worker)
        PayrollTransferLine.objects.create(
            batch=batch, entry=entry, destination=profile, amount=entry.balance,
            encrypted_account_snapshot=profile.destination_encrypted,
            recipient_name_snapshot=profile.provider_name,
            payment_type_snapshot=profile.payment_type,
            channel_snapshot=profile.channel,
            bank_code_snapshot=profile.bank_code,
        )
    core_services.audit(user, period.branch, "payroll.hubtel_batch_prepared",
                     str(batch.pk), {"period": period.label, "total": str(requested),
                                     "count": len(outstanding)}, category="finance", severity="high")
    return batch


def approve_and_send(user, batch_id):
    """Explicit manager action. Money may be partially sent; never re-click/replay."""
    if not user.is_superuser and not user.has_perm("core.manage_company"):
        raise PermissionDenied("Owner/management approval is required.")
    if not getattr(settings, "HUBTEL_PAYROLL_DISBURSEMENTS_ENABLED", False):
        raise ValidationError("Hubtel payroll online disbursement has not been activated.")
    with transaction.atomic():
        b = PayrollTransferBatch.objects.select_for_update().select_related(
            "period", "created_by",
        ).get(pk=batch_id)
        if b.status != "awaiting_approval" or b.approved_by_id:
            raise ValidationError("This salary batch is not awaiting approval.")
        if b.created_by_id == user.pk:
            raise PermissionDenied("The payroll payment batch creator cannot release their own batch.")
        if b.period.status != "locked":
            raise ValidationError("Payroll is no longer locked for payment.")
        if PayrollTransferLine.objects.filter(batch=b).count() == 0:
            raise ValidationError("Empty payroll transfer batch.")
        if sum((line.amount for line in b.lines.all()), Decimal("0.00")) != b.expected_amount:
            raise ValidationError("The payroll totals no longer reconcile.")
        # Freeze authorization before any provider POST.
        b.approved_by = user
        b.approved_at = timezone.now()
        b.status = "processing"
        b.save(update_fields=["approved_by", "approved_at", "status"])
    for line_id in PayrollTransferLine.objects.filter(
        batch_id=batch_id, status="draft",
    ).values_list("pk", flat=True):
        line = PayrollTransferLine.objects.select_related(
            "entry", "destination", "batch__created_by", "entry__worker",
        ).get(pk=line_id)
        if line.amount != line.entry.balance:
            PayrollTransferLine.objects.filter(pk=line_id, status="draft").update(status="attention")
            continue
        try:
            destination = client.unseal(line.encrypted_account_snapshot)
            # No mutable live beneficiary field may select the payout rail.
            if line.payment_type_snapshot == "momo":
                if line.channel_snapshot not in client.CHANNELS or line.bank_code_snapshot:
                    raise ValidationError("Saved payroll MoMo instructions are inconsistent.")
                payment_kind = "send_momo"
            elif line.payment_type_snapshot == "bank":
                if line.bank_code_snapshot not in client.BANK_CODES or line.channel_snapshot:
                    raise ValidationError("Saved payroll bank instructions are inconsistent.")
                payment_kind = "send_bank"
            else:
                raise ValidationError("Saved payroll payment type is not supported.")
            tx = service.create_payout(
                line.batch.created_by, payment_kind, line.amount,
                destination, line.recipient_name_snapshot,
                channel=line.channel_snapshot, bank_code=line.bank_code_snapshot,
                idempotency_key=str(line.request_key),
                purpose="KOFAD approved monthly payroll " + line.entry.period.label,
            )
        except (ValidationError, PermissionDenied) as exc:
            # A failed request never means a payment was sent. Still
            # distinguish this from ambiguous provider outcomes.
            PayrollTransferLine.objects.filter(pk=line_id, status="draft").update(status="attention")
            continue
        PayrollTransferLine.objects.filter(pk=line_id, status="draft").update(
            transaction=tx, status="submitted",
        )
        try:
            service.approve_payout(tx.pk, user)
        except (ValidationError, PermissionDenied):
            PayrollTransferLine.objects.filter(pk=line_id, status="submitted").update(status="attention")
            # The transaction record still exists and is independently
            # reconciled; no new request with a different reference.
    return PayrollTransferBatch.objects.get(pk=batch_id)


def settle_confirmed(limit=50):
    """Post the real salary receipt only after independent payout status = success."""
    if not getattr(settings, "HUBTEL_PAYROLL_DISBURSEMENTS_ENABLED", False):
        return 0
    completed = 0
    candidates = PayrollTransferLine.objects.filter(
        payroll_payment__isnull=True, transaction__status="success",
    ).select_related("batch", "entry", "transaction").order_by("pk")[:limit]
    for candidate in candidates:
        try:
            with transaction.atomic():
                line = PayrollTransferLine.objects.select_for_update().select_related(
                    "batch__approved_by", "entry", "transaction",
                ).get(pk=candidate.pk)
                if line.payroll_payment_id:
                    continue
                if not line.transaction or line.transaction.status != "success":
                    continue
                row = payroll_engine.record_payment(
                    line.batch.approved_by, line.entry, line.amount,
                    "momo" if line.transaction.kind == "send_momo" else "bank",
                    reference=line.transaction.client_reference,
                    note="Hubtel online salary transfer verified independently",
                )
                line.payroll_payment = row
                line.status = "paid"
                line.save(update_fields=["payroll_payment", "status"])
                completed += 1
        except ValidationError:
            PayrollTransferLine.objects.filter(pk=candidate.pk).update(status="finance_review")
    for batch in PayrollTransferBatch.objects.filter(status="processing"):
        if not batch.lines.exclude(status="paid").exists():
            batch.status = "completed"
            batch.save(update_fields=["status"])
    return completed
