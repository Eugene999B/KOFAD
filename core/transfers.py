"""Partial transfer receipts preserve the unreceived quantity for independent review."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from . import services as s
from .models import Operation, TransferReceipt


@transaction.atomic
def receive_transfer(user, operation_id, quantity=None, note=""):
    op = Operation.objects.select_for_update(of=("self",)).select_related("branch", "destination", "product").get(pk=operation_id)
    if op.kind != "transfer" or not op.destination_id:
        raise ValidationError("Only a dispatched transfer can be received.")
    s.permit(user, op.destination, "operate_inventory")
    s.lock_branch(op.destination)
    raw = str(op.quantity if quantity is None else quantity).strip()
    if not raw.isascii() or not raw.isdigit() or len(raw) > 10 or int(raw) > op.quantity:
        raise ValidationError("Received units must be between zero and the dispatched quantity.")
    quantity = int(raw)
    note = str(note).strip()
    if len(note) > 2000 or (quantity < op.quantity and len(note) < 10):
        raise ValidationError("Explain missing or damaged units in 10 to 2,000 characters.")
    existing = TransferReceipt.objects.filter(operation=op).first()
    if existing:
        if existing.recorded_by_id == user.pk and existing.quantity == quantity and existing.note == note:
            return op
        raise ValidationError("This transfer already has a receipt. Review its recorded discrepancy.")
    if op.status != "dispatched":
        raise ValidationError("Only a dispatched transfer can be received.")
    s.ensure_open(op.destination)
    TransferReceipt.objects.create(operation=op, quantity=quantity, note=note, recorded_by=user)
    s.stock_move(user, op.destination, op.product, quantity, str(op.pk), "Transfer receipt: " + (note or op.reason))
    op.status = "received" if quantity == op.quantity else "discrepancy"
    op.save(update_fields=["status"])
    s.audit(user, op.destination, "transfer.received", op.pk,
        {"dispatched": op.quantity, "received": quantity, "unreceived": op.quantity - quantity, "note": note})
    return op


@transaction.atomic
def resolve_transfer(user, operation_id, resolution, note):
    op = Operation.objects.select_for_update(of=("self",)).select_related("branch", "destination", "product").get(pk=operation_id)
    if op.kind != "transfer" or not op.destination_id:
        raise ValidationError("Choose a transfer with an unresolved discrepancy.")
    s.permit(user, op.destination, "approve_operations")
    s.lock_branch(op.destination)
    receipt = TransferReceipt.objects.select_for_update().filter(operation=op).first()
    if not receipt or op.status != "discrepancy" or receipt.resolution:
        raise ValidationError("This transfer has no unresolved discrepancy.")
    if receipt.recorded_by_id == user.pk:
        raise ValidationError("A different authorized colleague must resolve this discrepancy.")
    note = str(note).strip()
    if resolution not in ("arrived", "loss") or len(note) < 10 or len(note) > 2000:
        raise ValidationError("Choose a resolution and explain it in 10 to 2,000 characters.")
    s.ensure_open(op.destination)
    if resolution == "arrived":
        s.stock_move(user, op.destination, op.product, receipt.missing, str(op.pk),
            "Transfer remainder received: " + note)
    # A confirmed loss never restores source stock or invents destination stock.
    receipt.resolution = resolution
    receipt.resolution_note = note
    receipt.resolved_by = user
    receipt.resolved_at = timezone.now()
    receipt.save(update_fields=["resolution", "resolution_note", "resolved_by", "resolved_at"])
    op.status = "received"
    op.save(update_fields=["status"])
    s.audit(user, op.destination, "transfer.discrepancy_resolved", op.pk,
        {"resolution": resolution, "quantity": receipt.missing, "note": note})
    return op
