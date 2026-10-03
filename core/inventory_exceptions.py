"""Controlled supplier returns, damaged-stock quarantine and inventory loss evidence."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from . import services as s
from .returns import can_direct_return, is_owner
from .models import (
    Allocation, Document, Line, Payment, Product, QuarantineItem, Stock, SupplierReturn,
)


def _meaningful(value, minimum=10, maximum=2000, label="reason"):
    value = str(value or "").strip()
    if len(value) < minimum or len(value) > maximum:
        raise ValidationError(f"Provide a {label} between {minimum} and {maximum} characters.")
    return value


def _inventory_loss_document(user, branch, product, quantity, unit_cost, note, original=None):
    quantity = s.units(quantity)
    unit_cost = s.money(unit_cost)
    total = s.money(unit_cost * quantity)
    doc = Document.objects.create(
        branch=branch,
        kind="inventory_writeoff",
        party=None,
        original=original,
        reference=s.reference("loss", branch),
        total=total,
        paid=0,
        note=note,
        created_by=user,
    )
    Line.objects.create(
        document=doc,
        product=product,
        description=product.name,
        mode="writeoff",
        quantity=quantity,
        factor=1,
        list_price=unit_cost,
        unit_price=unit_cost,
        discount_percent=0,
        unit_cost=unit_cost,
        total=total,
    )
    s.audit(user, branch, "inventory.loss_posted", doc.reference, {
        "product": product.sku,
        "quantity": quantity,
        "unit_cost": str(unit_cost),
        "value": str(total),
        "note": note,
    })
    return doc


@transaction.atomic
def request_supplier_return(user, branch, source_line_id, quantity, reason, refund_method, direct=False):
    s.permit(user, branch, "operate_inventory")
    branch = s.lock_branch(branch)
    source = Line.objects.select_related("document", "product", "document__party").filter(
        pk=source_line_id,
        document__branch=branch,
        document__kind="purchase",
    ).first()
    if not source:
        raise ValidationError("Choose a line from a purchase at this location.")
    quantity = s.units(quantity)
    reason = _meaningful(reason, label="return reason")
    if refund_method not in dict(Payment.METHODS):
        raise ValidationError("Choose a valid supplier refund channel.")
    if not s.payment_method_enabled(refund_method):
        raise ValidationError(f"{dict(Payment.METHODS)[refund_method]} is disabled in Settings.")
    reserved = SupplierReturn.objects.filter(
        source_line=source,
        status__in=["requested", "approved"],
    ).aggregate(total=Sum("quantity"))["total"] or 0
    if reserved + quantity > source.quantity:
        raise ValidationError("Supplier return quantity exceeds the quantity purchased or already reserved for return.")
    item = SupplierReturn.objects.create(
        branch=branch,
        source_line=source,
        quantity=quantity,
        refund_method=refund_method,
        reason=reason,
        requested_by=user,
    )
    if direct and not can_direct_return(user, branch, "supplier"):
        raise PermissionDenied("Direct supplier-return authority is required.")
    s.audit(user, branch, "supplier_return.requested", item.pk, {
        "purchase": source.document.reference,
        "product": source.product.sku,
        "quantity": quantity,
        "refund_method": refund_method,
        "reason": reason,
        "direct_authority": direct,
    }, category="returns", severity="notice", entity_type="supplier_return_request")
    if direct:
        return review_supplier_return(user, branch, item.pk, True, direct=True)
    return item


@transaction.atomic
def review_supplier_return(user, branch, return_id, approve, direct=False):
    branch = s.lock_branch(branch)
    item = SupplierReturn.objects.select_for_update(of=("self",)).select_related(
        "source_line__document", "source_line__product", "source_line__document__party"
    ).get(pk=return_id, branch=branch)
    if item.status != "requested":
        raise ValidationError("This supplier return has already been reviewed.")
    if direct:
        if is_owner(user):
            pass
        elif item.requested_by_id != user.pk or not can_direct_return(user, branch, "supplier"):
            raise PermissionDenied("Direct supplier-return authority is required.")
    else:
        s.permit(user, branch, "approve_operations")
        s.permit(user, branch, "operate_finance")
        if item.requested_by_id == user.pk:
            raise ValidationError("A different authorized colleague must review this supplier return.")
    if not approve:
        item.status = "rejected"
        item.reviewed_by = user
        item.reviewed_at = timezone.now()
        item.save(update_fields=["status", "reviewed_by", "reviewed_at"])
        s.audit(user, branch, "supplier_return.rejected", item.pk, {"reason": item.reason})
        return item

    s.ensure_open(branch)
    source = item.source_line
    already_posted = SupplierReturn.objects.filter(
        source_line=source, status="approved"
    ).exclude(pk=item.pk).aggregate(total=Sum("quantity"))["total"] or 0
    if already_posted + item.quantity > source.quantity:
        raise ValidationError("The eligible purchase quantity changed while this return was awaiting approval.")
    if not s.payment_method_enabled(item.refund_method):
        raise ValidationError(
            f"{dict(Payment.METHODS)[item.refund_method]} is disabled. Reject this request and create one with an enabled channel."
        )

    amount = s.money(source.unit_price * item.quantity)
    purchase = source.document
    credit = min(amount, s.balance(purchase))
    refund = amount - credit
    doc = Document.objects.create(
        branch=branch,
        kind="supplier_return",
        party=purchase.party,
        original=purchase,
        reference=s.reference("supplier_return", branch),
        total=amount,
        paid=refund,
        note=item.reason,
        created_by=user,
    )
    Line.objects.create(
        document=doc,
        product=source.product,
        description=source.description,
        mode=source.mode,
        quantity=item.quantity,
        factor=source.factor,
        list_price=source.list_price or source.unit_price,
        unit_price=source.unit_price,
        discount_percent=source.discount_percent,
        unit_cost=source.unit_cost,
        total=amount,
        source_line=source,
    )
    s.stock_move(
        user,
        branch,
        source.product,
        -(item.quantity * source.factor),
        doc.reference,
        "Approved supplier return: " + item.reason,
    )
    if credit:
        Allocation.objects.create(payment_document=doc, invoice=purchase, amount=credit)
    if refund:
        s.payments(doc, [{
            "method": item.refund_method,
            "amount": refund,
            "reference": "",
        }], 1)

    item.status = "approved"
    item.reviewed_by = user
    item.reviewed_at = timezone.now()
    item.posted = doc
    item.save(update_fields=["status", "reviewed_by", "reviewed_at", "posted"])
    s.audit(user, branch, "supplier_return.approved", item.pk, {
        "purchase": purchase.reference,
        "document": doc.reference,
        "quantity": item.quantity,
        "credit": str(credit),
        "refund": str(refund),
        "refund_method": item.refund_method,
        "direct_authority": bool(direct),
    }, category="returns", severity="high" if refund else "notice", entity_type="supplier_return", entity_id=str(doc.pk))
    return item


@transaction.atomic
def request_quarantine(user, branch, product_id, quantity, reason, direct=False):
    s.permit(user, branch, "operate_inventory")
    branch = s.lock_branch(branch)
    product = Product.objects.filter(pk=product_id, active=True).first()
    if not product:
        raise ValidationError("Choose an active product.")
    quantity = s.units(quantity)
    reason = _meaningful(reason, label="quarantine reason")
    available = Stock.objects.filter(branch=branch, product=product).values_list("quantity", flat=True).first() or 0
    if quantity > available:
        raise ValidationError(f"Cannot quarantine {quantity} units. Only {available} sellable units are available.")
    item = QuarantineItem.objects.create(
        branch=branch,
        product=product,
        quantity=quantity,
        unit_cost=product.cost,
        reason=reason,
        requested_by=user,
    )
    if direct and not is_owner(user):
        raise PermissionDenied("Owner / company administrator authority is required.")
    s.audit(user, branch, "quarantine.requested", item.pk, {
        "product": product.sku,
        "quantity": quantity,
        "unit_cost": str(product.cost),
        "reason": reason,
        "direct_authority": direct,
    }, category="inventory", severity="high", entity_type="quarantine")
    if direct:
        return review_quarantine(user, branch, item.pk, True, direct=True)
    return item


@transaction.atomic
def review_quarantine(user, branch, item_id, approve, direct=False):
    if direct:
        if not is_owner(user):
            raise PermissionDenied("Owner / company administrator authority is required.")
    else:
        s.permit(user, branch, "approve_operations")
    branch = s.lock_branch(branch)
    item = QuarantineItem.objects.select_for_update().select_related("product").get(pk=item_id, branch=branch)
    if item.status != "requested":
        raise ValidationError("This quarantine request has already been reviewed.")
    if item.requested_by_id == user.pk and not (direct and is_owner(user)):
        raise ValidationError("A different authorized colleague must review this quarantine request.")
    if approve:
        s.ensure_open(branch)
        s.stock_move(
            user, branch, item.product, -item.quantity, str(item.pk),
            "Moved to damaged-stock quarantine: " + item.reason,
        )
        item.status = "held"
        action = "quarantine.held"
    else:
        item.status = "rejected"
        action = "quarantine.rejected"
    item.reviewed_by = user
    item.reviewed_at = timezone.now()
    item.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    s.audit(user, branch, action, item.pk, {
        "product": item.product.sku,
        "quantity": item.quantity,
        "reason": item.reason,
    })
    return item


@transaction.atomic
def resolve_quarantine(user, branch, item_id, action, note, owner_direct=False):
    if owner_direct:
        if not is_owner(user):
            raise PermissionDenied("Owner / company administrator authority is required.")
    else:
        s.permit(user, branch, "approve_operations")
    branch = s.lock_branch(branch)
    item = QuarantineItem.objects.select_for_update().select_related("product").get(pk=item_id, branch=branch)
    if item.status != "held":
        raise ValidationError("Only stock currently held in quarantine can be resolved.")
    if item.requested_by_id == user.pk and not owner_direct:
        raise ValidationError("A different authorized colleague must resolve this quarantine item.")
    note = _meaningful(note, label="resolution note")
    s.ensure_open(branch)
    if action == "release":
        s.stock_move(
            user, branch, item.product, item.quantity, str(item.pk),
            "Released from quarantine: " + note,
        )
        item.status = "released"
    elif action == "writeoff":
        item.loss_document = _inventory_loss_document(
            user, branch, item.product, item.quantity, item.unit_cost,
            "Damaged stock write-off: " + note,
        )
        item.status = "written_off"
    else:
        raise ValidationError("Choose release or write-off.")
    item.resolved_by = user
    item.resolved_at = timezone.now()
    item.resolution_note = note
    item.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_note", "loss_document"])
    s.audit(user, branch, "quarantine." + item.status, item.pk, {
        "product": item.product.sku,
        "quantity": item.quantity,
        "value": str(item.value),
        "note": note,
        "loss_document": str(item.loss_document_id or ""),
    })
    return item


def post_transfer_loss(user, branch, product, quantity, unit_cost, note, original=None):
    """Create accounting evidence for transfer stock confirmed lost after dispatch."""
    return _inventory_loss_document(user, branch, product, quantity, unit_cost, note, original=original)
