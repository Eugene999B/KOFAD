"""Customer return controls with privilege-aware direct posting and approval reservation."""
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from . import services as s
from .models import (
    Allocation, CustomerReturnRequest, CustomerReturnRequestLine, Document, Line,
    Payment, QuarantineItem, ReturnPrivilege,
)

ZERO = Decimal("0.00")


def is_owner(user):
    return bool(user.is_superuser or user.has_perm("core.manage_company"))


def can_request_customer_return(user):
    return bool(
        user.is_superuser
        or user.has_perm("core.manage_company")
        or user.has_perm("core.operate_sales")
        or user.has_perm("core.approve_operations")
    )


def can_direct_return(user, branch, kind="customer"):
    if is_owner(user):
        return True
    privilege = ReturnPrivilege.objects.filter(branch=branch, user=user).first()
    if not privilege:
        return False
    return privilege.customer_returns if kind == "customer" else privilege.supplier_returns


def eligible_quantity(source_line):
    posted = Line.objects.filter(
        source_line=source_line, document__kind="return"
    ).aggregate(total=Sum("quantity"))["total"] or 0
    pending = CustomerReturnRequestLine.objects.filter(
        source_line=source_line, request__status="requested"
    ).aggregate(total=Sum("quantity"))["total"] or 0
    return max(int(source_line.quantity) - int(posted) - int(pending), 0)


def sale_return_rows(sale):
    rows = []
    for line in sale.lines.select_related("product").all():
        eligible = eligible_quantity(line)
        posted = Line.objects.filter(
            source_line=line, document__kind="return"
        ).aggregate(total=Sum("quantity"))["total"] or 0
        pending = CustomerReturnRequestLine.objects.filter(
            source_line=line, request__status="requested"
        ).aggregate(total=Sum("quantity"))["total"] or 0
        rows.append({
            "line": line,
            "eligible": eligible,
            "posted": posted,
            "pending": pending,
            "line_value": s.money(line.unit_price * eligible) if eligible else ZERO,
        })
    return rows


def _validate_request_payload(branch, sale, lines, reason, refund_method):
    if sale.branch_id != branch.pk or sale.kind != "sale":
        raise ValidationError("Choose a valid sale from this store.")
    reason = str(reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Explain why the customer is returning the item in at least five characters.")
    if refund_method not in dict(Payment.METHODS):
        raise ValidationError("Choose a valid refund channel.")
    if not isinstance(lines, list) or not lines:
        raise ValidationError("Select at least one item to return.")
    if len(lines) > 50:
        raise ValidationError("A single return can contain at most 50 sale lines.")

    source_ids = [str(row.get("line") or "") for row in lines]
    if len(source_ids) != len(set(source_ids)):
        raise ValidationError("Each sale line can appear only once in a return request.")

    validated = []
    for row in lines:
        source = Line.objects.select_related("document", "product").filter(
            pk=row.get("line"), document=sale
        ).first()
        if not source:
            raise ValidationError("One selected item does not belong to this sale.")
        qty = s.units(row.get("quantity"))
        available = eligible_quantity(source)
        if qty > available:
            raise ValidationError(
                f"{source.product.name}: only {available} unit(s) are currently eligible for return."
            )
        disposition = str(row.get("disposition") or "sellable")
        if disposition not in {"sellable", "quarantine"}:
            raise ValidationError("Choose whether each returned item is sellable or damaged / held.")
        validated.append((source, qty, disposition))
    return reason, refund_method, validated


@transaction.atomic
def create_customer_return(user, branch, sale, lines, reason, refund_method):
    if not can_request_customer_return(user):
        raise PermissionDenied("You do not have permission to request customer returns.")
    branch = s.lock_branch(branch)
    sale = Document.objects.select_for_update(of=("self",)).select_related("party").get(
        pk=sale.pk, branch=branch, kind="sale"
    )
    reason, refund_method, validated = _validate_request_payload(
        branch, sale, lines, reason, refund_method
    )
    direct = can_direct_return(user, branch, "customer")
    item = CustomerReturnRequest.objects.create(
        branch=branch,
        sale=sale,
        refund_method=refund_method,
        reason=reason,
        status="requested",
        direct=direct,
        requested_by=user,
    )
    CustomerReturnRequestLine.objects.bulk_create([
        CustomerReturnRequestLine(
            request=item, source_line=source, quantity=qty, disposition=disposition
        )
        for source, qty, disposition in validated
    ])
    s.audit(user, branch, "customer_return.requested", item.pk, {
        "sale": sale.reference,
        "direct_authority": direct,
        "lines": [
            {"line": source.pk, "product": source.product.sku, "quantity": qty, "disposition": disposition}
            for source, qty, disposition in validated
        ],
        "reason": reason,
    }, category="returns", severity="notice", entity_type="customer_return_request")

    if direct:
        return execute_customer_return(user, branch, item.pk, direct=True), True
    return item, False


@transaction.atomic
def execute_customer_return(user, branch, request_id, direct=False):
    branch = s.lock_branch(branch)
    item = CustomerReturnRequest.objects.select_for_update(of=("self",)).select_related(
        "sale", "sale__party", "requested_by"
    ).get(pk=request_id, branch=branch)
    if item.status != "requested":
        raise ValidationError("This customer return request has already been reviewed.")

    if direct:
        if item.requested_by_id != user.pk or not can_direct_return(user, branch, "customer"):
            raise PermissionDenied("Direct return authority is required.")
    else:
        if not (is_owner(user) or user.has_perm("core.approve_operations")):
            raise PermissionDenied("Approval permission is required.")
        if item.requested_by_id == user.pk and not is_owner(user):
            raise ValidationError("A different authorized colleague must approve this return.")

    s.ensure_open(branch)
    request_lines = list(item.lines.select_for_update().select_related(
        "source_line", "source_line__product", "source_line__document"
    ))
    if not request_lines:
        raise ValidationError("This return request has no items.")

    total = ZERO
    for requested in request_lines:
        source = requested.source_line
        posted = Line.objects.filter(
            source_line=source, document__kind="return"
        ).aggregate(total=Sum("quantity"))["total"] or 0
        other_pending = CustomerReturnRequestLine.objects.filter(
            source_line=source,
            request__status="requested",
        ).exclude(request=item).aggregate(total=Sum("quantity"))["total"] or 0
        available = int(source.quantity) - int(posted) - int(other_pending)
        if requested.quantity > available:
            raise ValidationError(
                f"{source.product.name}: return eligibility changed while approval was pending. "
                f"Only {max(available, 0)} unit(s) remain."
            )
        total += s.money(source.unit_price * requested.quantity)

    sale = item.sale
    credit = min(total, s.balance(sale))
    refund = total - credit
    if refund and not s.payment_method_enabled(item.refund_method):
        raise ValidationError(
            f"{dict(Payment.METHODS)[item.refund_method]} is disabled. Reject this request and create a new one using an enabled refund channel."
        )

    doc = Document.objects.create(
        branch=branch,
        kind="return",
        party=sale.party,
        original=sale,
        reference=s.reference("return", branch),
        total=total,
        paid=refund,
        note=item.reason,
        created_by=user,
    )

    quarantine_total = 0
    sellable_total = 0
    for requested in request_lines:
        source = requested.source_line
        amount = s.money(source.unit_price * requested.quantity)
        Line.objects.create(
            document=doc,
            product=source.product,
            description=source.description,
            mode=source.mode,
            quantity=requested.quantity,
            factor=source.factor,
            list_price=source.list_price or source.unit_price,
            unit_price=source.unit_price,
            discount_percent=source.discount_percent,
            unit_cost=source.unit_cost,
            total=amount,
            source_line=source,
        )
        base_units = requested.quantity * source.factor
        if requested.disposition == "sellable":
            s.stock_move(
                user, branch, source.product, base_units, doc.reference,
                "Customer return restored to sellable stock: " + item.reason,
            )
            sellable_total += base_units
        else:
            QuarantineItem.objects.create(
                branch=branch,
                product=source.product,
                quantity=base_units,
                unit_cost=source.unit_cost,
                reason=f"Customer return {doc.reference}: {item.reason}",
                status="held",
                requested_by=item.requested_by,
                reviewed_by=user,
                reviewed_at=timezone.now(),
            )
            quarantine_total += base_units

    if credit:
        Allocation.objects.create(payment_document=doc, invoice=sale, amount=credit)
    if refund:
        s.payments(doc, [{"method": item.refund_method, "amount": refund, "reference": doc.reference}], -1)

    item.status = "approved"
    item.reviewed_by = user
    item.reviewed_at = timezone.now()
    item.posted = doc
    item.direct = bool(direct)
    item.save(update_fields=["status", "reviewed_by", "reviewed_at", "posted", "direct"])
    s.audit(user, branch, "customer_return.posted", doc.reference, {
        "request": str(item.pk),
        "sale": sale.reference,
        "amount": str(total),
        "debt_credit": str(credit),
        "refund": str(refund),
        "refund_method": item.refund_method,
        "sellable_base_units": sellable_total,
        "quarantine_base_units": quarantine_total,
        "direct_authority": bool(direct),
    }, category="returns", severity="high" if refund else "notice", entity_type="return", entity_id=str(doc.pk))
    return item


@transaction.atomic
def reject_customer_return(user, branch, request_id, note=""):
    branch = s.lock_branch(branch)
    item = CustomerReturnRequest.objects.select_for_update().get(pk=request_id, branch=branch)
    if item.status != "requested":
        raise ValidationError("This customer return request has already been reviewed.")
    if not (is_owner(user) or user.has_perm("core.approve_operations")):
        raise PermissionDenied("Approval permission is required.")
    if item.requested_by_id == user.pk and not is_owner(user):
        raise ValidationError("A different authorized colleague must reject this return.")
    item.status = "rejected"
    item.reviewed_by = user
    item.reviewed_at = timezone.now()
    item.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    s.audit(user, branch, "customer_return.rejected", item.pk, {
        "sale": item.sale.reference,
        "review_note": str(note or "").strip()[:500],
    }, category="returns", severity="notice", entity_type="customer_return_request")
    return item
