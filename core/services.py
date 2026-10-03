"""Transactional application services. Views never write financial ledgers directly."""
import hashlib
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from .models import (
    Allocation, Audit, Branch, Closing, Company, Correction, Document, Idempotency, Line,
    Movement, Operation, Party, Payment, Product, Stock,
)

ZERO = Decimal("0.00")


def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal("999999999999.99"):
            raise ValueError
        if amount != amount.quantize(Decimal(".01")):
            raise ValueError
        return amount.quantize(Decimal(".01"))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Enter a nonnegative amount with at most two decimal places.")


def percent(value):
    try:
        amount = Decimal(str(value or 0))
        if not amount.is_finite() or amount < 0 or amount > 100:
            raise ValueError
        if amount != amount.quantize(Decimal(".01")):
            raise ValueError
        return amount.quantize(Decimal(".01"))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Enter a percentage from 0 to 100 with at most two decimal places.")


def company_policy():
    return Company.objects.first() or Company()


PAYMENT_FLAGS = {
    "cash": "payment_cash",
    "momo": "payment_momo",
    "bank": "payment_bank",
    "card": "payment_card",
}


def active_payment_methods(company=None):
    company = company or company_policy()
    return [(code, label) for code, label in Payment.METHODS if getattr(company, PAYMENT_FLAGS[code], True)]


def payment_method_enabled(method, company=None):
    return method in dict(active_payment_methods(company))


def units(value, signed=False):
    if isinstance(value, bool) or not str(value).lstrip("-").isdigit():
        raise ValidationError("Quantity must be a whole number.")
    value = int(value)
    if abs(value) > 1000000 or value == 0 or (value < 0 and not signed):
        raise ValidationError("Quantity must be between 1 and 1,000,000.")
    return value


def permit(user, branch, permission):
    if not user.is_active or not user.has_perm("core." + permission):
        raise PermissionDenied("You do not have permission for this action.")
    if not branch.active:
        raise PermissionDenied("This location is inactive.")
    if not user.is_superuser and not user.access.branches.filter(pk=branch.pk).exists():
        raise PermissionDenied("This location is outside your access.")


def audit(user, branch, action, reference, detail=None):
    Audit.objects.create(actor=user, branch=branch, action=action, reference=str(reference), detail=detail or {})


def lock_branch(branch):
    # Every financial write takes the same branch lock, preventing overselling,
    # double allocation and a concurrent posting from racing a daily closing.
    return Branch.objects.select_for_update().get(pk=branch.pk)


def ensure_open(branch):
    if Closing.objects.filter(branch=branch, date=timezone.localdate()).exists():
        raise ValidationError("Today is closed. No further financial postings are allowed.")


def begin_request(user, branch, key, payload):
    try:
        key = uuid.UUID(str(key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("A valid request key is required.")
    raw = json.dumps({"user": user.pk, "payload": payload}, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(raw.encode()).hexdigest()
    record, _ = Idempotency.objects.get_or_create(branch=branch, key=key, defaults={"fingerprint": fingerprint})
    if record.fingerprint != fingerprint:
        raise ValidationError("This request key was already used for different information.")
    return record


def stock_move(user, branch, product, delta, reference, reason):
    if not delta:
        return
    stock, _ = Stock.objects.select_for_update().get_or_create(branch=branch, product=product)
    quantity = stock.quantity + delta
    if quantity > 2000000000:
        raise ValidationError("Stock balance exceeds the supported limit.")
    if quantity < 0:
        raise ValidationError(f"Insufficient stock for {product.name}. Available: {stock.quantity}.")
    stock.quantity = quantity
    stock.save(update_fields=["quantity"])
    Movement.objects.create(branch=branch, product=product, delta=delta, balance=quantity,
                            reference=reference, reason=reason, actor=user)


@transaction.atomic
def restock_inventory(user, branch, product_id, packs=0, loose=0, note="", external_reference="", unit_cost=None):
    """Receive sellable stock without creating a supplier payable.

    Financial supplier purchases belong in Purchasing. This quick restock exists for
    stock-only receipts, opening top-ups and deliveries whose accounting is handled
    outside KOFAD.
    """
    permit(user, branch, "operate_inventory")
    branch = lock_branch(branch)
    ensure_open(branch)
    product = Product.objects.select_for_update().filter(pk=product_id, active=True).first()
    if not product:
        raise ValidationError("Choose an active product.")

    def nonnegative(value, label):
        raw = str(value or "0").strip()
        if not raw.isdigit():
            raise ValidationError(f"{label} must be a whole number.")
        number = int(raw)
        if number > 100000000:
            raise ValidationError(f"{label} is too large.")
        return number

    pack_count = nonnegative(packs, "Pack quantity")
    loose_count = nonnegative(loose, "Loose quantity")
    if product.pack_size > 1 and loose_count >= product.pack_size:
        raise ValidationError(
            f"Loose quantity must be less than one full {product.pack_name} ({product.pack_size} {product.base_unit})."
        )
    if product.pack_size <= 1 and pack_count:
        loose_count += pack_count
        pack_count = 0

    quantity = pack_count * product.pack_size + loose_count
    if quantity <= 0:
        raise ValidationError("Enter at least one pack or loose unit to restock.")

    cleaned_note = str(note or "").strip()
    if len(cleaned_note) < 5:
        raise ValidationError("Add a short restock note of at least five characters.")

    current = Stock.objects.select_for_update().filter(branch=branch, product=product).first()
    before_quantity = current.quantity if current else 0
    before_cost = product.cost

    if unit_cost not in (None, ""):
        new_cost = money(unit_cost)
        if new_cost <= 0:
            raise ValidationError("Unit cost must be greater than zero when supplied.")
        if new_cost != product.cost:
            product.cost = new_cost
            product.save(update_fields=["cost"])

    supplied_reference = str(external_reference or "").strip()[:100]
    ref = supplied_reference or reference("restock", branch)
    stock_move(user, branch, product, quantity, ref, f"restock · {cleaned_note}")

    after_quantity = before_quantity + quantity
    audit(user, branch, "inventory.restocked", product.sku, {
        "product": product.pk,
        "reference": ref,
        "packs": pack_count,
        "loose_units": loose_count,
        "base_units_added": quantity,
        "stock_before": before_quantity,
        "stock_after": after_quantity,
        "unit_cost_before": str(before_cost),
        "unit_cost_after": str(product.cost),
        "note": cleaned_note,
        "accounting_effect": "inventory_only",
    })
    return {
        "product": product,
        "reference": ref,
        "packs": pack_count,
        "loose": loose_count,
        "added": quantity,
        "before": before_quantity,
        "after": after_quantity,
    }


def reference(kind, branch):
    core = f"{kind[:3].upper()}-{branch.code.upper()}-{uuid.uuid4().hex[:12].upper()}"
    prefix = company_policy().reference_prefix.strip().upper()
    return f"{prefix}-{core}" if prefix else core


def balance(invoice):
    allocated = invoice.settlements.exclude(payment_document__correction__status="approved").aggregate(total=Sum("amount"))["total"] or ZERO
    return invoice.total - invoice.paid - allocated


def party_debt(party):
    kind = "sale" if party.kind == "customer" else "purchase"
    return sum((balance(d) for d in Document.objects.filter(party=party, kind=kind)), ZERO)


def payments(document, rows, direction, enforce_enabled=True):
    total = ZERO
    company = company_policy()
    if not isinstance(rows, list) or len(rows) > 8:
        raise ValidationError("Provide up to eight payment entries.")
    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError("Invalid payment entry.")
        amount = money(row.get("amount", 0))
        if amount == 0:
            continue
        method = row.get("method")
        if method not in dict(Payment.METHODS):
            raise ValidationError("Unknown payment method.")
        if enforce_enabled and not payment_method_enabled(method, company):
            raise ValidationError(f"{dict(Payment.METHODS)[method]} is disabled in Settings.")
        ref = str(row.get("reference", ""))[:100]
        Payment.objects.create(document=document, method=method, amount=amount, reference=ref, direction=direction)
        total += amount
    return total


@transaction.atomic
def post_trade(user, branch, payload, key, kind="sale"):
    if kind not in ("sale", "purchase"):
        raise ValidationError("Invalid transaction kind.")
    permit(user, branch, "operate_sales" if kind == "sale" else "operate_inventory")
    branch = lock_branch(branch)
    request = begin_request(user, branch, key, {"kind": kind, **payload})
    if request.document_id:
        return request.document
    ensure_open(branch)
    company = company_policy()
    rows = payload.get("items", [])
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValidationError("Add between one and 100 items.")
    party = None
    if kind == "sale":
        from .identity import resolve_sale_customer
        party = resolve_sale_customer(user, branch, payload, audit)
    elif payload.get("party"):
        party = Party.objects.filter(pk=payload["party"], branch=branch, kind="supplier").first()
        if not party:
            raise ValidationError("Choose a valid supplier at this location.")
    if kind == "purchase" and not party:
        raise ValidationError("A supplier is required.")

    override_reason = str(payload.get("override_reason", "")).strip()
    elevated = []
    overrides = []
    doc = Document.objects.create(branch=branch, kind=kind, party=party, total=0, paid=0, finalized=False,
        created_by=user, reference=reference(kind, branch), note=str(payload.get("note", ""))[:2000])
    total = ZERO

    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError("Invalid line item.")
        product = Product.objects.filter(pk=row.get("product"), active=True).first()
        if not product:
            raise ValidationError("A product is missing or archived.")
        qty = units(row.get("quantity"))
        mode = row.get("mode", "retail_unit")
        if mode not in ("retail_unit", "retail_pack", "wholesale_unit", "wholesale_pack"):
            raise ValidationError("Invalid selling mode.")
        factor = product.pack_size if mode.endswith("pack") else 1
        discount = ZERO
        list_price = None

        if kind == "sale":
            configured = getattr(product, mode)
            if configured is None:
                raise ValidationError(f"{mode.replace('_', ' ')} is disabled for {product.name}.")
            list_price = money(configured)
            price = list_price
            requested = row.get("price")
            if requested not in (None, ""):
                requested_price = money(requested)
                if requested_price != list_price:
                    if not company.allow_price_overrides:
                        raise ValidationError("Price overrides are disabled in Settings.")
                    price = requested_price
                    overrides.append({
                        "type": "price", "product": product.sku,
                        "from": str(list_price), "to": str(price),
                    })
                    if list_price > 0 and price < list_price:
                        reduction = ((list_price - price) * Decimal("100") / list_price).quantize(
                            Decimal(".01"), rounding=ROUND_HALF_UP)
                        if reduction > company.max_price_reduction_percent:
                            raise ValidationError(
                                f"Price reduction for {product.name} exceeds the configured maximum of "
                                f"{company.max_price_reduction_percent}%."
                            )
                        if reduction > company.staff_price_reduction_limit:
                            if not user.has_perm("core.approve_operations"):
                                raise ValidationError(
                                    f"A manager with approval authority must complete price reductions above "
                                    f"{company.staff_price_reduction_limit}%."
                                )
                            elevated.append(f"price reduction {product.sku} {reduction}%")

            discount = percent(row.get("discount", 0))
            if discount:
                if not company.allow_discounts:
                    raise ValidationError("Discounts are disabled in Settings.")
                if price != list_price:
                    raise ValidationError("Use either a price override or a discount on a line, not both.")
                if discount > company.max_discount_percent:
                    raise ValidationError(
                        f"Discount for {product.name} exceeds the configured maximum of "
                        f"{company.max_discount_percent}%."
                    )
                if discount > company.staff_discount_limit:
                    if not user.has_perm("core.approve_operations"):
                        raise ValidationError(
                            f"A manager with approval authority must complete discounts above "
                            f"{company.staff_discount_limit}%."
                        )
                    elevated.append(f"discount {product.sku} {discount}%")
                overrides.append({"type": "discount", "product": product.sku, "percent": str(discount)})
                price = (price * (Decimal("100") - discount) / Decimal("100")).quantize(
                    Decimal(".01"), rounding=ROUND_HALF_UP)
        else:
            price = money(row.get("price"))
            list_price = price

        price = money(price)
        if qty * factor > 1000000000:
            raise ValidationError("Base-unit quantity exceeds the supported posting limit.")
        line_total = money(price * qty)
        Line.objects.create(document=doc, product=product, description=product.name,
            mode=mode, quantity=qty, factor=factor, list_price=list_price, unit_price=price,
            discount_percent=discount, unit_cost=product.cost, total=line_total)
        stock_move(user, branch, product, qty * factor * (-1 if kind == "sale" else 1), doc.reference, kind)
        total += line_total

    total = money(total)
    if kind == "sale":
        if company.customer_required_above and total >= company.customer_required_above and not party:
            raise ValidationError(
                f"A named customer is required for sales of {company.currency} "
                f"{company.customer_required_above} or more."
            )
        if company.sale_manager_threshold and total > company.sale_manager_threshold:
            if not user.has_perm("core.approve_operations"):
                raise ValidationError(
                    f"A manager with approval authority must complete sales above "
                    f"{company.currency} {company.sale_manager_threshold}."
                )
            elevated.append(f"sale total {total}")

    if overrides and len(override_reason) < 10:
        raise ValidationError("Explain the discount or price override in at least ten characters.")

    paid = payments(doc, payload.get("payments", []), 1 if kind == "sale" else -1)
    if paid > total:
        raise ValidationError("Payments exceed the total. Enter the amount retained, excluding change.")

    due = None
    credit_override = ZERO
    if paid < total:
        if not party:
            raise ValidationError("A customer is required for credit.")
        if kind == "sale" and not company.allow_credit_sales:
            raise ValidationError("Credit sales are disabled in Settings.")
        try:
            due = date.fromisoformat(str(payload.get("due_date", "")))
        except ValueError:
            raise ValidationError("A due date is required for an unpaid balance.")
        if due < timezone.localdate():
            raise ValidationError("The due date cannot be in the past.")
        if kind == "sale" and due > timezone.localdate() + timedelta(days=company.max_credit_days):
            raise ValidationError(f"Credit terms cannot exceed {company.max_credit_days} days.")
        if kind == "sale":
            projected = party_debt(party) + total - paid
            if party.credit_limit > 0 and projected > party.credit_limit:
                credit_override = projected - party.credit_limit
                if (not user.has_perm("core.approve_operations") or company.max_credit_override <= 0
                        or credit_override > company.max_credit_override):
                    raise ValidationError(
                        "This sale exceeds the customer's credit limit. A permitted manager override is required."
                    )
                if len(override_reason) < 10:
                    raise ValidationError("Explain the credit-limit override in at least ten characters.")
                elevated.append(f"credit override {credit_override}")

    doc.total, doc.paid, doc.due_date = total, paid, due
    doc.finalized = True
    doc.save(update_fields=["total", "paid", "due_date", "finalized"])
    request.document = doc
    request.save(update_fields=["document"])
    audit(user, branch, kind + ".posted", doc.reference, {
        "total": str(total), "paid": str(paid), "overrides": overrides,
        "elevated_authority": elevated, "credit_override": str(credit_override),
        "override_reason": override_reason if overrides or credit_override else "",
    })
    if kind == "sale":
        from . import automations
        transaction.on_commit(
            lambda document_id=doc.pk, actor_id=user.pk:
                automations.safe_prepare_sale_receipt(document_id, actor_id)
        )
    return doc

@transaction.atomic
def post_payment(user, branch, payload, key, supplier=False):
    permit(user, branch, "operate_finance")
    branch = lock_branch(branch)
    request = begin_request(user, branch, key, {"supplier": supplier, **payload})
    if request.document_id:
        return request.document
    ensure_open(branch)
    invoice = Document.objects.filter(pk=payload.get("invoice"), branch=branch,
        kind="purchase" if supplier else "sale").first()
    if not invoice:
        raise ValidationError("Choose a valid outstanding invoice.")
    amount = money(payload.get("amount"))
    if amount <= 0 or amount > balance(invoice):
        raise ValidationError("Payment must be positive and cannot exceed the outstanding balance.")
    kind = "supplier_payment" if supplier else "collection"
    doc = Document.objects.create(branch=branch, kind=kind, party=invoice.party, original=invoice,
        reference=reference(kind, branch), total=amount, paid=amount, created_by=user)
    payments(doc, [{"method": payload.get("method"), "amount": amount, "reference": payload.get("reference", "")}],
             -1 if supplier else 1)
    Allocation.objects.create(payment_document=doc, invoice=invoice, amount=amount)
    request.document = doc
    request.save(update_fields=["document"])
    audit(user, branch, kind + ".posted", doc.reference, {"invoice": invoice.reference, "amount": str(amount)})
    if not supplier:
        from . import automations
        transaction.on_commit(
            lambda document_id=doc.pk, actor_id=user.pk:
                automations.safe_prepare_payment_confirmation(document_id, actor_id)
        )
    return doc


@transaction.atomic
def post_return(user, branch, payload, key):
    permit(user, branch, "approve_operations")
    branch = lock_branch(branch)
    request = begin_request(user, branch, key, payload)
    if request.document_id:
        return request.document
    ensure_open(branch)
    source = Line.objects.select_related("document").filter(pk=payload.get("line"),
        document__branch=branch, document__kind="sale").first()
    if not source:
        raise ValidationError("Choose a sale line from this location.")
    qty = units(payload.get("quantity"))
    returned = Line.objects.filter(source_line=source).aggregate(q=Sum("quantity"))["q"] or 0
    if returned + qty > source.quantity:
        raise ValidationError("Return quantity exceeds the eligible quantity.")
    reason = str(payload.get("reason", "")).strip()
    if len(reason) < 5:
        raise ValidationError("Provide a meaningful reason for the return.")
    amount = money(source.unit_price * qty)
    credit = min(amount, balance(source.document))
    refund = amount - credit
    doc = Document.objects.create(branch=branch, kind="return", party=source.document.party,
        original=source.document, reference=reference("return", branch), total=amount, paid=refund,
        note=reason, created_by=user)
    Line.objects.create(document=doc, product=source.product, description=source.description,
        mode=source.mode, quantity=qty, factor=source.factor,
        list_price=source.list_price or source.unit_price, unit_price=source.unit_price,
        discount_percent=source.discount_percent, unit_cost=source.unit_cost,
        total=amount, source_line=source)
    stock_move(user, branch, source.product, qty * source.factor, doc.reference, reason)
    if credit:
        Allocation.objects.create(payment_document=doc, invoice=source.document, amount=credit)
    if refund:
        payments(doc, [{"method": payload.get("method"), "amount": refund}], -1)
    request.document = doc
    request.save(update_fields=["document"])
    audit(user, branch, "return.posted", doc.reference, {"original": source.document.reference, "reason": reason})
    return doc


@transaction.atomic
def post_expense(user, branch, payload, key):
    permit(user, branch, "operate_finance")
    branch = lock_branch(branch)
    request = begin_request(user, branch, key, payload)
    if request.document_id:
        return request.document
    ensure_open(branch)
    amount = money(payload.get("amount"))
    note = str(payload.get("note", "")).strip()
    if amount <= 0 or len(note) < 5:
        raise ValidationError("Provide a positive amount and a meaningful expense description.")
    company = company_policy()
    elevated = bool(company.expense_manager_threshold and amount > company.expense_manager_threshold)
    if elevated and not user.has_perm("core.approve_operations"):
        raise ValidationError(
            f"A manager with approval authority must post expenses above "
            f"{company.currency} {company.expense_manager_threshold}."
        )
    doc = Document.objects.create(branch=branch, kind="expense", reference=reference("expense", branch),
        total=amount, paid=amount, note=note, created_by=user)
    payments(doc, [{"method": payload.get("method"), "amount": amount}], -1)
    request.document = doc
    request.save(update_fields=["document"])
    audit(user, branch, "expense.posted", doc.reference, {
        "amount": str(amount), "note": note, "manager_threshold": elevated,
    })
    return doc


@transaction.atomic
def request_operation(user, branch, payload):
    permit(user, branch, "operate_inventory")
    qty = units(payload.get("quantity"), signed=True)
    kind = payload.get("kind")
    destination = None
    if kind == "transfer":
        if qty < 1:
            raise ValidationError("Transfer quantity must be positive.")
        destination = Branch.objects.filter(pk=payload.get("destination"), active=True).first()
        if not destination or destination == branch:
            raise ValidationError("Choose a different active destination.")
    elif kind != "adjustment":
        raise ValidationError("Invalid stock operation.")
    reason = str(payload.get("reason", "")).strip()
    if len(reason) < 5:
        raise ValidationError("A meaningful reason is required.")
    product = Product.objects.filter(pk=payload.get("product"), active=True).first()
    if not product:
        raise ValidationError("Choose an active product.")
    op = Operation.objects.create(branch=branch, destination=destination, product=product, kind=kind,
        quantity=qty, reason=reason, requested_by=user)
    audit(user, branch, "stock.requested", op.pk, {"quantity": qty, "reason": reason})
    return op


@transaction.atomic
def advance_operation(user, operation_id, action, quantity=None, note=""):
    if action == "receive":
        from .transfers import receive_transfer
        return receive_transfer(user, operation_id, quantity, note)
    op = Operation.objects.select_for_update(of=("self",)).select_related("branch", "destination", "product").get(pk=operation_id)
    branch = op.destination if action == "receive" else op.branch
    permit(user, branch, "approve_operations" if action in ("approve", "reject") else "operate_inventory")
    lock_branch(branch)
    if action in ("approve", "reject") and op.status == "requested":
        if op.requested_by_id == user.pk:
            raise ValidationError("A different authorized colleague must review this request.")
        op.approved_by = user
        op.status = "rejected" if action == "reject" else "approved"
        if action == "approve" and op.kind == "adjustment":
            ensure_open(branch)
            stock_move(user, branch, op.product, op.quantity, str(op.pk), op.reason)
            op.status = "received"
    elif action == "dispatch" and op.status == "approved" and op.kind == "transfer":
        ensure_open(branch)
        stock_move(user, branch, op.product, -op.quantity, str(op.pk), "Transfer dispatched: " + op.reason)
        op.status = "dispatched"
    else:
        raise ValidationError("This operation has already advanced or the transition is invalid.")
    op.save(update_fields=["approved_by", "status"])
    audit(user, branch, "stock." + action, op.pk, {"status": op.status})
    return op


def channel_totals(branch, day):
    rows = Payment.objects.filter(document__branch=branch, document__created_at__date=day)
    totals = {method: ZERO for method, _ in Payment.METHODS}
    for row in rows:
        totals[row.method] += row.amount * row.direction
    return totals


def closing_summary(branch, day):
    """Explain the day's commercial activity separately from the channel reconciliation."""
    docs = Document.objects.filter(branch=branch, created_at__date=day)
    sales = docs.filter(kind="sale")
    returns = docs.filter(kind="return")
    purchases = docs.filter(kind="purchase")
    supplier_returns = docs.filter(kind="supplier_return")
    collections = docs.filter(kind="collection").exclude(correction__status="approved")
    supplier_payments = docs.filter(kind="supplier_payment").exclude(correction__status="approved")
    expenses = docs.filter(kind="expense").exclude(correction__status="approved")
    losses = docs.filter(kind="inventory_writeoff")

    def total(queryset):
        return queryset.aggregate(value=Sum("total"))["value"] or ZERO

    sale_total = total(sales)
    sale_paid = sales.aggregate(value=Sum("paid"))["value"] or ZERO
    return_total = total(returns)
    source_codes = {
        "sale": "Sales received",
        "collection": "Debt collections",
        "purchase": "Purchases paid",
        "supplier_payment": "Supplier debt payments",
        "expense": "Expenses",
        "return": "Customer refunds",
        "supplier_return": "Supplier refunds",
        "reversal": "Corrections",
    }
    breakdown = {
        label: {method: ZERO for method, _ in Payment.METHODS}
        for label in source_codes.values()
    }
    payment_rows = Payment.objects.filter(
        document__branch=branch, document__created_at__date=day
    ).select_related("document")
    for payment in payment_rows:
        label = source_codes.get(payment.document.kind)
        if label:
            breakdown[label][payment.method] += payment.amount * payment.direction

    return {
        "sales_total": sale_total,
        "sales_received_at_checkout": sale_paid,
        "credit_created": sale_total - sale_paid,
        "returns_total": return_total,
        "net_sales": sale_total - return_total,
        "debt_collections": total(collections),
        "purchases_total": total(purchases),
        "supplier_returns_total": total(supplier_returns),
        "supplier_debt_payments": total(supplier_payments),
        "expenses_total": total(expenses),
        "inventory_losses": total(losses),
        "sale_count": sales.count(),
        "credit_sale_count": sales.filter(total__gt=F("paid")).count(),
        "return_count": returns.count(),
        "collection_count": collections.count(),
        "expense_count": expenses.count(),
        "channel_net": channel_totals(branch, day),
        "channel_breakdown": breakdown,
    }


def _closing_json(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _closing_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_closing_json(item) for item in value]
    return value


@transaction.atomic
def submit_closing(user, branch, day, counted, note, opening_cash=0, cash_in=0, cash_out=0):
    permit(user, branch, "operate_finance")
    branch = lock_branch(branch)
    if day > timezone.localdate():
        raise ValidationError("Cannot close a future day.")
    if Closing.objects.filter(branch=branch, date=day).exists():
        raise ValidationError("This day is already closed.")

    opening_cash = money(opening_cash or 0)
    cash_in = money(cash_in or 0)
    cash_out = money(cash_out or 0)
    summary = closing_summary(branch, day)
    expected = dict(summary["channel_net"])
    expected["cash"] = expected["cash"] + opening_cash + cash_in - cash_out

    actual = {}
    for method, _ in Payment.METHODS:
        raw = str(counted.get(method, 0))
        actual[method] = -money(raw[1:]) if raw.startswith("-") else money(raw)

    tolerance = company_policy().closing_tolerance
    variances = {method: actual[method] - expected[method] for method in actual}
    if any(abs(value) > tolerance for value in variances.values()) and len(note.strip()) < 5:
        raise ValidationError("Explain the closing variance in at least five characters.")

    summary["cash_control"] = {
        "opening_cash": opening_cash,
        "other_cash_in": cash_in,
        "other_cash_out": cash_out,
        "expected_cash": expected["cash"],
    }
    summary["variance"] = variances
    closing = Closing.objects.create(
        branch=branch,
        date=day,
        expected={key: str(value) for key, value in expected.items()},
        counted={key: str(value) for key, value in actual.items()},
        opening_cash=opening_cash,
        cash_in=cash_in,
        cash_out=cash_out,
        summary=_closing_json(summary),
        note=str(note or "").strip()[:2000],
        submitted_by=user,
    )
    audit(user, branch, "closing.submitted", closing.pk, {
        "date": str(day),
        "expected": closing.expected,
        "counted": closing.counted,
        "variance": {key: str(value) for key, value in variances.items()},
        "opening_cash": str(opening_cash),
        "cash_in": str(cash_in),
        "cash_out": str(cash_out),
    })
    from . import automations
    transaction.on_commit(
        lambda closing_id=closing.pk, actor_id=user.pk:
            automations.safe_prepare_closing(closing_id, actor_id)
    )
    return closing


@transaction.atomic
def verify_closing(user, closing):
    permit(user, closing.branch, "approve_operations")
    closing = Closing.objects.select_for_update().get(pk=closing.pk)
    if closing.submitted_by_id == user.pk or closing.verified_by_id:
        raise ValidationError("A different authorized colleague must verify an unverified closing.")
    closing.verified_by = user
    closing.save(update_fields=["verified_by"])
    audit(user, closing.branch, "closing.verified", closing.pk)


@transaction.atomic
def request_correction(user, branch, original_id, reason, refund_method="cash"):
    permit(user, branch, "operate_finance")
    lock_branch(branch)
    original = Document.objects.filter(pk=original_id, branch=branch, kind__in=[
        "sale", "expense", "collection", "supplier_payment"]).first()
    if not original:
        raise ValidationError("This record cannot be reversed through this workflow.")
    if len(reason.strip()) < 10:
        raise ValidationError("Explain the correction in at least ten characters.")
    if Correction.objects.filter(original=original).exists():
        raise ValidationError("This document already has a correction request.")
    if refund_method not in dict(Payment.METHODS):
        raise ValidationError("Choose a valid refund channel.")
    if original.kind == "sale" and not payment_method_enabled(refund_method):
        raise ValidationError(f"{dict(Payment.METHODS)[refund_method]} is disabled in Settings.")
    item = Correction.objects.create(original=original, requested_by=user, reason=reason, refund_method=refund_method)
    audit(user, branch, "correction.requested", original.reference, {"reason":reason})
    return item


@transaction.atomic
def review_correction(user, branch, correction_id, approve):
    permit(user, branch, "approve_operations")
    branch = lock_branch(branch)
    item = Correction.objects.select_for_update(of=("self",)).select_related("original").get(pk=correction_id, original__branch=branch)
    if item.status != "requested":
        raise ValidationError("This request has already been reviewed.")
    if item.requested_by_id == user.pk:
        raise ValidationError("A different authorized colleague must review the correction.")
    original = item.original
    if approve:
        ensure_open(branch)
        if original.kind == "sale":
            posted = []
            for line in original.lines.all():
                previous = Line.objects.filter(source_line=line).aggregate(q=Sum("quantity"))["q"] or 0
                remaining = line.quantity - previous
                if remaining:
                    doc = post_return(user,branch,{"line":line.pk,"quantity":remaining,
                        "reason":"Approved sale void: " + item.reason,"method":item.refund_method},
                        uuid.uuid5(original.pk, f"void:{item.pk}:{line.pk}"))
                    posted.append(doc)
            if not posted:
                raise ValidationError("All sale quantities have already been returned.")
            item.posted = posted[0]
        else:
            doc = Document.objects.create(branch=branch, kind="reversal", party=original.party,
                original=original, total=original.total, paid=original.paid, note=item.reason,
                reference=reference("reversal",branch),created_by=user)
            for payment in original.payments.all():
                payments(doc,[{"method":payment.method,"amount":payment.amount,"reference":payment.reference}],
                    -payment.direction, enforce_enabled=False)
            item.posted = doc
        item.status = "approved"
    else:
        item.status = "rejected"
    item.reviewed_by = user
    item.save(update_fields=["status","reviewed_by","posted"])
    audit(user,branch,"correction."+item.status,original.reference,{"reason":item.reason,"posted":str(item.posted_id or "")})
    return item
