"""Transactional application services. Views never write financial ledgers directly."""
import hashlib
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
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


def _audit_category(action):
    prefix = str(action or "").split(".", 1)[0]
    return {
        "sale": "sales", "return": "returns", "customer_return": "returns", "supplier_return": "returns",
        "purchase": "purchasing", "creditor": "finance", "expense": "finance", "collection": "finance",
        "supplier_payment": "finance", "payroll": "payroll", "closing": "finance", "correction": "controls",
        "stock": "inventory", "inventory": "inventory", "quarantine": "inventory", "count": "inventory",
        "staff": "administration", "role": "administration", "auth": "security", "journal": "accounting",
    }.get(prefix, "system")


def _audit_digest(event_id, branch_id, actor_id, action, reference, category, severity, entity_type, entity_id, detail, previous_hash):
    canonical = json.dumps({
        "event_id": str(event_id),
        "branch": branch_id,
        "actor": actor_id,
        "action": str(action),
        "reference": str(reference),
        "category": str(category),
        "severity": str(severity),
        "entity_type": str(entity_type or ""),
        "entity_id": str(entity_id or reference or ""),
        "detail": detail or {},
        "previous_hash": str(previous_hash or ""),
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def audit_hash_for(row):
    return _audit_digest(
        row.event_id, row.branch_id, row.actor_id, row.action, row.reference,
        row.category, row.severity, row.entity_type, row.entity_id, row.detail, row.previous_hash,
    )


def audit(user, branch, action, reference, detail=None, *, category=None, severity="info", entity_type="", entity_id=""):
    """Append a serialized, hash-linked and database-immutable business event."""
    detail = detail or {}
    category = str(category or _audit_category(action))[:32]
    severity = str(severity or "info")[:12]
    entity_type = str(entity_type or "")[:60]
    entity_id = str(entity_id or reference or "")[:100]
    with transaction.atomic():
        if branch is not None:
            Branch.objects.select_for_update().get(pk=branch.pk)
        elif connection.vendor == "postgresql":
            # Serialize the global chain too; branch rows already provide this lock for branch-scoped events.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    ["kofad_global_audit_chain_v1"],
                )
        previous = Audit.objects.filter(branch=branch).exclude(event_hash="").order_by("-created_at", "-pk").first()
        previous_hash = previous.event_hash if previous else ""
        event_id = uuid.uuid4()
        event_hash = _audit_digest(
            event_id, getattr(branch, "pk", None), getattr(user, "pk", None),
            action, reference, category, severity, entity_type, entity_id, detail, previous_hash,
        )
        return Audit.objects.create(
            event_id=event_id,
            actor=user,
            branch=branch,
            action=str(action)[:80],
            reference=str(reference)[:100],
            category=category,
            severity=severity,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )


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
    if invoice.kind == "creditor_charge" and hasattr(invoice, "correction") and invoice.correction.status == "approved":
        return ZERO
    allocated = invoice.settlements.exclude(payment_document__correction__status="approved").aggregate(total=Sum("amount"))["total"] or ZERO
    return invoice.total - invoice.paid - allocated


def party_debt(party):
    kinds = ["sale"] if party.kind == "customer" else ["purchase", "creditor_charge"]
    return sum((balance(d) for d in Document.objects.filter(party=party, kind__in=kinds)), ZERO)


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


def _new_purchase_product(data):
    if not isinstance(data, dict):
        raise ValidationError("New product details are invalid.")
    name = str(data.get("name", "")).strip()
    sku = str(data.get("sku", "")).strip().upper()
    if len(name) < 2 or len(name) > 150:
        raise ValidationError("Enter a product name between 2 and 150 characters.")
    if not (2 <= len(sku) <= 40) or not all(ch.isalnum() or ch in "._/-" for ch in sku):
        raise ValidationError("Enter a valid SKU using letters, numbers, dot, dash, slash or underscore.")
    if Product.objects.filter(sku__iexact=sku).exists():
        raise ValidationError(f"SKU {sku} already exists. Search and choose the existing product instead.")
    try:
        pack_size = int(data.get("pack_size", 1))
        reorder_level = int(data.get("reorder_level", 10))
    except (TypeError, ValueError):
        raise ValidationError("Pack size and reorder level must be whole numbers.")
    if not 1 <= pack_size <= 1000000:
        raise ValidationError("Units per pack must be between 1 and 1,000,000.")
    if not 0 <= reorder_level <= 1000000000:
        raise ValidationError("Reorder level is outside the supported range.")

    def optional_price(key):
        value = data.get(key)
        return None if value in (None, "") else money(value)

    return Product.objects.create(
        name=name,
        sku=sku,
        barcode=str(data.get("barcode", "")).strip()[:80],
        category=str(data.get("category", "")).strip()[:80],
        base_unit=str(data.get("base_unit", "piece")).strip()[:24] or "piece",
        pack_name=str(data.get("pack_name", "carton")).strip()[:24] or "carton",
        pack_size=pack_size,
        cost=ZERO,
        retail_unit=optional_price("retail_unit"),
        retail_pack=optional_price("retail_pack"),
        wholesale_unit=optional_price("wholesale_unit"),
        wholesale_pack=optional_price("wholesale_pack"),
        reorder_level=reorder_level,
        active=True,
    )


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

    external_reference = str(payload.get("external_reference", "")).strip()[:120] if kind == "purchase" else ""
    document_date = timezone.localdate()
    if kind == "purchase":
        raw_document_date = str(payload.get("document_date", "")).strip()
        if raw_document_date:
            try:
                document_date = date.fromisoformat(raw_document_date)
            except ValueError:
                raise ValidationError("Supplier invoice date must be a valid date.")
        if document_date > timezone.localdate():
            raise ValidationError("Supplier invoice date cannot be in the future.")
        if external_reference and Document.objects.filter(
            branch=branch, party=party, kind__in=["purchase", "creditor_charge"],
            external_reference__iexact=external_reference,
        ).exists():
            raise ValidationError("This supplier invoice/reference is already recorded for this supplier.")

    override_reason = str(payload.get("override_reason", "")).strip()
    elevated = []
    overrides = []
    doc = Document.objects.create(
        branch=branch, kind=kind, party=party, total=0, paid=0, finalized=False,
        created_by=user, reference=reference(kind, branch), note=str(payload.get("note", ""))[:2000],
        document_date=document_date if kind == "purchase" else None,
        external_reference=external_reference,
        payable_category="inventory" if kind == "purchase" else "",
    )
    total = ZERO

    created_products = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError("Invalid line item.")
        new_product_data = row.get("new_product")
        if new_product_data and kind != "purchase":
            raise ValidationError("New products can only be created through purchasing.")
        product = _new_purchase_product(new_product_data) if new_product_data else Product.objects.filter(
            pk=row.get("product"), active=True
        ).first()
        if not product:
            raise ValidationError("A product is missing or archived.")
        if new_product_data:
            created_products.append(product.sku)
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
        purchase_base_cost = product.cost
        stock_before = 0
        if kind == "purchase":
            purchase_base_cost = (price / Decimal(factor)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            stock_before = Stock.objects.filter(branch=branch, product=product).values_list("quantity", flat=True).first() or 0
        Line.objects.create(document=doc, product=product, description=product.name,
            mode=mode, quantity=qty, factor=factor, list_price=list_price, unit_price=price,
            discount_percent=discount, unit_cost=purchase_base_cost, total=line_total)
        base_units = qty * factor
        stock_move(user, branch, product, base_units * (-1 if kind == "sale" else 1), doc.reference, kind)
        if kind == "purchase":
            denominator = stock_before + base_units
            weighted = (
                (product.cost * stock_before) + (purchase_base_cost * base_units)
            ) / Decimal(denominator)
            product.cost = weighted.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            product.save(update_fields=["cost"])
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
        if kind == "sale" and due < timezone.localdate():
            raise ValidationError("The due date cannot be in the past.")
        if kind == "purchase" and due < document_date:
            raise ValidationError("Supplier due date cannot be before the supplier invoice date.")
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
        "external_reference": external_reference if kind == "purchase" else "",
        "document_date": str(document_date) if kind == "purchase" else "",
        "new_products_created": created_products if kind == "purchase" else [],
    })
    if kind == "sale" and not any(key in payload for key in ("customer_consent", "send_sms", "send_whatsapp")):
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
    invoice_qs = Document.objects.filter(pk=payload.get("invoice"), branch=branch)
    invoice = invoice_qs.filter(kind__in=["purchase", "creditor_charge"]).first() if supplier else invoice_qs.filter(kind="sale").first()
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


EXPENSE_FUNDING_SOURCES = {
    "today_sales_receipts": "Today's sales receipts",
    "petty_cash": "Petty cash",
    "prior_business_funds": "Prior business funds",
    "owner_manager_funds": "Owner / manager funds",
    "bank_account": "Business bank account",
    "momo_wallet": "Business MoMo wallet",
    "unpaid_credit": "Unpaid / on credit",
    "other": "Other source",
}


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
    category = str(payload.get("category", "other")).strip().lower()
    categories = {"transport", "fuel", "utilities", "rent", "maintenance", "marketing", "salary", "tax", "office", "security", "professional", "other"}
    if category not in categories:
        raise ValidationError("Choose a valid expense category.")
    if amount <= 0 or len(note) < 5:
        raise ValidationError("Provide a positive amount and a meaningful expense description.")

    funding_source = str(payload.get("funding_source", "today_sales_receipts")).strip().lower()
    if funding_source not in EXPENSE_FUNDING_SOURCES:
        raise ValidationError("Choose a valid source of funds for this expense.")
    raw_affects = payload.get("affects_daily_closing", "1")
    affects_daily_closing = str(raw_affects).lower() in {"1", "true", "yes", "on"}
    funding_note = str(payload.get("funding_note", "")).strip()[:500]
    method = str(payload.get("method", "cash")).strip().lower()

    if funding_source == "today_sales_receipts" and not affects_daily_closing:
        raise ValidationError("Today's sales receipts must reduce the matching Daily Closing channel.")
    if funding_source != "today_sales_receipts" and affects_daily_closing:
        raise ValidationError("Only an expense paid from today's sales receipts may reduce Daily Closing.")
    if funding_source == "unpaid_credit" and affects_daily_closing:
        raise ValidationError("An unpaid expense cannot reduce Daily Closing.")
    if funding_source == "other" and len(funding_note) < 8:
        raise ValidationError("Describe the other funding source using at least eight characters.")

    if funding_source == "petty_cash":
        method = "cash"
    elif funding_source == "bank_account":
        method = "bank"
    elif funding_source == "momo_wallet":
        method = "momo"

    company = company_policy()
    elevated = bool(company.expense_manager_threshold and amount > company.expense_manager_threshold)
    if elevated and not user.has_perm("core.approve_operations"):
        raise ValidationError(
            f"A manager with approval authority must post expenses above "
            f"{company.currency} {company.expense_manager_threshold}."
        )

    paid = ZERO if funding_source == "unpaid_credit" else amount
    doc = Document.objects.create(
        branch=branch,
        kind="expense",
        reference=reference("expense", branch),
        total=amount,
        paid=paid,
        note=note,
        expense_category=category,
        expense_funding_source=funding_source,
        expense_affects_daily_closing=affects_daily_closing,
        expense_funding_note=funding_note,
        created_by=user,
    )

    # Owner-funded expenses and unpaid credit do not move a KOFAD cash/bank channel.
    if funding_source not in {"owner_manager_funds", "unpaid_credit"}:
        payments(doc, [{"method": method, "amount": amount}], -1)

    request.document = doc
    request.save(update_fields=["document"])
    audit(user, branch, "expense.posted", doc.reference, {
        "amount": str(amount),
        "note": note,
        "category": category,
        "manager_threshold": elevated,
        "funding_source": funding_source,
        "funding_source_label": EXPENSE_FUNDING_SOURCES[funding_source],
        "affects_daily_closing": affects_daily_closing,
        "payment_method": method if funding_source not in {"owner_manager_funds", "unpaid_credit"} else "",
        "funding_note": funding_note,
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
    rows = Payment.objects.filter(document__branch=branch, document__created_at__date=day).exclude(
        document__kind="expense",
        document__expense_affects_daily_closing=False,
    )
    totals = {method: ZERO for method, _ in Payment.METHODS}
    for row in rows:
        totals[row.method] += row.amount * row.direction
    # Payroll salary payments are direct controlled outflows rather than sales-ledger
    # documents, but they still move real cash/bank/MoMo and must reconcile at closing.
    from .models import PayrollPayment
    payroll_rows = PayrollPayment.objects.filter(
        entry__period__branch=branch, created_at__date=day
    )
    for row in payroll_rows:
        totals[row.method] -= row.amount
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
        if payment.document.kind == "expense" and not payment.document.expense_affects_daily_closing:
            continue
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
        "expenses_closing_deducted": total(expenses.filter(expense_affects_daily_closing=True)),
        "expenses_accounting_only": total(expenses.filter(expense_affects_daily_closing=False)),
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
def submit_closing(user, branch, day, counted, note, opening_cash=0, cash_in=0, cash_out=0, owner_direct=False):
    if owner_direct:
        if not (user.is_superuser or user.has_perm("core.manage_company")):
            raise PermissionDenied("Owner / company administrator authority is required.")
    else:
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
        verified_by=user if owner_direct else None,
    )
    audit(user, branch, "closing.submitted", closing.pk, {
        "date": str(day),
        "expected": closing.expected,
        "counted": closing.counted,
        "variance": {key: str(value) for key, value in variances.items()},
        "opening_cash": str(opening_cash),
        "cash_in": str(cash_in),
        "cash_out": str(cash_out),
        "owner_direct": bool(owner_direct),
    }, category="finance", severity="high" if owner_direct else "notice", entity_type="closing", entity_id=str(closing.pk))
    from . import automations
    transaction.on_commit(
        lambda closing_id=closing.pk, actor_id=user.pk:
            automations.safe_prepare_closing(closing_id, actor_id)
    )
    return closing


@transaction.atomic
def verify_closing(user, closing, owner_direct=False):
    if owner_direct:
        if not (user.is_superuser or user.has_perm("core.manage_company")):
            raise PermissionDenied("Owner / company administrator authority is required.")
    else:
        permit(user, closing.branch, "approve_operations")
    closing = Closing.objects.select_for_update().get(pk=closing.pk)
    if closing.verified_by_id:
        raise ValidationError("This closing has already been verified.")
    if closing.submitted_by_id == user.pk:
        raise ValidationError("A submitted closing must be independently verified. Owner-direct closings are finalized at submission instead.")
    closing.verified_by = user
    closing.save(update_fields=["verified_by"])
    audit(user, closing.branch, "closing.verified", closing.pk)


@transaction.atomic
def request_correction(user, branch, original_id, reason, refund_method="cash"):
    permit(user, branch, "operate_finance")
    lock_branch(branch)
    original = Document.objects.filter(pk=original_id, branch=branch, kind__in=[
        "sale", "expense", "collection", "supplier_payment", "creditor_charge"]).first()
    if not original:
        raise ValidationError("This record cannot be reversed through this workflow.")
    if len(reason.strip()) < 10:
        raise ValidationError("Explain the correction in at least ten characters.")
    if Correction.objects.filter(original=original).exists():
        raise ValidationError("This document already has a correction request.")
    if original.kind == "creditor_charge":
        allocated = original.settlements.exclude(payment_document__correction__status="approved").aggregate(total=Sum("amount"))["total"] or ZERO
        if allocated > 0:
            raise ValidationError("Reverse the creditor payments allocated to this bill before reversing the bill itself.")
    if refund_method not in dict(Payment.METHODS):
        raise ValidationError("Choose a valid refund channel.")
    if original.kind == "sale" and not payment_method_enabled(refund_method):
        raise ValidationError(f"{dict(Payment.METHODS)[refund_method]} is disabled in Settings.")
    item = Correction.objects.create(original=original, requested_by=user, reason=reason, refund_method=refund_method)
    audit(user, branch, "correction.requested", original.reference, {"reason":reason})
    return item


@transaction.atomic
def review_correction(user, branch, correction_id, approve, owner_direct=False):
    if owner_direct:
        if not (user.is_superuser or user.has_perm("core.manage_company")):
            raise PermissionDenied("Owner / company administrator authority is required.")
    else:
        permit(user, branch, "approve_operations")
    branch = lock_branch(branch)
    item = Correction.objects.select_for_update(of=("self",)).select_related("original").get(pk=correction_id, original__branch=branch)
    if item.status != "requested":
        raise ValidationError("This request has already been reviewed.")
    if item.requested_by_id == user.pk and not owner_direct:
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
