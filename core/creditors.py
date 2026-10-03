"""Supplier-first accounts payable services for KOFAD's Creditors workspace."""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from . import services as s
from .models import Allocation, Document, Party


ZERO = Decimal("0.00")
PAYABLE_KINDS = ("purchase", "creditor_charge")
PAYABLE_CATEGORIES = [
    ("inventory", "Inventory / stock"),
    ("transport", "Transport & delivery"),
    ("fuel", "Fuel"),
    ("utilities", "Utilities"),
    ("rent", "Rent & premises"),
    ("maintenance", "Repairs & maintenance"),
    ("professional", "Professional services"),
    ("equipment", "Equipment / asset"),
    ("tax", "Taxes, levies & fees"),
    ("staff", "Staff-related"),
    ("loan", "Loan / financing"),
    ("other", "Other"),
]
CATEGORY_LABELS = dict(PAYABLE_CATEGORIES)


def _business_date(value, label, default=None):
    if value in (None, ""):
        return default
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"{label} must be a valid date.")


def open_supplier_bills(party, for_update=False):
    qs = Document.objects.filter(
        branch=party.branch,
        party=party,
        kind__in=PAYABLE_KINDS,
    ).select_related("created_by").order_by(
        F("due_date").asc(nulls_last=True),
        F("document_date").asc(nulls_last=True),
        "created_at",
        "pk",
    )
    if for_update:
        qs = qs.select_for_update(of=("self",))
    rows = []
    for bill in qs:
        outstanding = s.balance(bill)
        if outstanding > 0:
            rows.append((bill, outstanding))
    return rows


def _days_overdue(bill, today):
    base = bill.due_date or bill.document_date or bill.created_at.date()
    return max(0, (today - base).days) if base < today else 0


def supplier_account_snapshot(party):
    today = timezone.localdate()
    bills = open_supplier_bills(party)
    outstanding = sum((amount for _, amount in bills), ZERO)
    overdue = ZERO
    due_today = ZERO
    due_7_days = ZERO
    aging = {
        "current": ZERO,
        "days_1_30": ZERO,
        "days_31_60": ZERO,
        "days_61_90": ZERO,
        "days_90_plus": ZERO,
    }
    bill_rows = []
    max_days = 0

    for bill, amount in bills:
        days = _days_overdue(bill, today)
        max_days = max(max_days, days)
        if bill.due_date == today:
            due_today += amount
        if bill.due_date and today <= bill.due_date <= today + timedelta(days=7):
            due_7_days += amount
        if days == 0:
            aging["current"] += amount
            status = "due_today" if bill.due_date == today else "current"
        elif days <= 30:
            aging["days_1_30"] += amount
            overdue += amount
            status = "overdue"
        elif days <= 60:
            aging["days_31_60"] += amount
            overdue += amount
            status = "overdue"
        elif days <= 90:
            aging["days_61_90"] += amount
            overdue += amount
            status = "overdue"
        else:
            aging["days_90_plus"] += amount
            overdue += amount
            status = "overdue"
        bill_rows.append({
            "bill": bill,
            "outstanding": amount,
            "paid_so_far": bill.total - amount,
            "days_overdue": days,
            "status": status,
            "source": "Purchase" if bill.kind == "purchase" else "Direct creditor bill",
            "category": CATEGORY_LABELS.get(bill.payable_category or "other", (bill.payable_category or "Other").title()),
        })

    all_bills = Document.objects.filter(branch=party.branch, party=party, kind__in=PAYABLE_KINDS)
    purchases = all_bills.filter(kind="purchase")
    direct_bills = all_bills.filter(kind="creditor_charge")
    payments = Document.objects.filter(
        branch=party.branch, party=party, kind="supplier_payment"
    ).exclude(correction__status="approved")
    total_billed = all_bills.aggregate(total=Sum("total"))["total"] or ZERO
    total_purchases = purchases.aggregate(total=Sum("total"))["total"] or ZERO
    total_direct = direct_bills.aggregate(total=Sum("total"))["total"] or ZERO
    total_paid = payments.aggregate(total=Sum("total"))["total"] or ZERO
    next_due = min((bill.due_date for bill, _ in bills if bill.due_date), default=None)
    last_bill = all_bills.order_by("-created_at").first()
    last_payment = payments.order_by("-created_at").first()

    return {
        "outstanding": outstanding,
        "overdue": overdue,
        "due_today": due_today,
        "due_7_days": due_7_days,
        "bill_count": len(bills),
        "total_billed": total_billed,
        "total_purchases": total_purchases,
        "total_direct": total_direct,
        "total_paid": total_paid,
        "aging": aging,
        "maximum_days_overdue": max_days,
        "next_due": next_due,
        "last_bill": last_bill,
        "last_payment": last_payment,
        "bill_rows": bill_rows,
        "recent_payments": list(payments.select_related("created_by").order_by("-created_at")[:20]),
    }


def creditor_accounts(branch, query="", include_settled=False):
    parties = Party.objects.filter(branch=branch, kind="supplier")
    query = str(query or "").strip()[:100]
    if query:
        parties = parties.filter(
            Q(name__icontains=query) | Q(phone__icontains=query) |
            Q(email__icontains=query) | Q(address__icontains=query)
        )
    rows = []
    for party in parties.order_by("name")[:600]:
        snapshot = supplier_account_snapshot(party)
        if snapshot["outstanding"] > 0 or (include_settled and snapshot["total_billed"] > 0):
            rows.append({"party": party, **snapshot})
    rows.sort(key=lambda row: (
        -int(row["maximum_days_overdue"]),
        -row["overdue"],
        -row["outstanding"],
        row["party"].name.casefold(),
    ))
    return rows


def creditors_overview(branch, query="", include_settled=False):
    rows = creditor_accounts(branch, query, include_settled)
    today = timezone.localdate()
    month_start = today.replace(day=1)
    payments = Document.objects.filter(
        branch=branch, kind="supplier_payment",
        created_at__date__gte=month_start,
        created_at__date__lte=today,
    ).exclude(correction__status="approved")
    return {
        "rows": rows,
        "total_payables": sum((row["outstanding"] for row in rows), ZERO),
        "overdue": sum((row["overdue"] for row in rows), ZERO),
        "due_7_days": sum((row["due_7_days"] for row in rows), ZERO),
        "creditors_owing": sum(1 for row in rows if row["outstanding"] > 0),
        "overdue_creditors": sum(1 for row in rows if row["overdue"] > 0),
        "paid_this_month": payments.aggregate(total=Sum("total"))["total"] or ZERO,
        "aging": {
            key: sum((row["aging"][key] for row in rows), ZERO)
            for key in ("current", "days_1_30", "days_31_60", "days_61_90", "days_90_plus")
        },
    }


def _supplier_from_payload(user, branch, payload):
    supplier_id = str(payload.get("party") or "").strip()
    if supplier_id.isdigit():
        supplier = Party.objects.filter(pk=int(supplier_id), branch=branch, kind="supplier").first()
        if supplier:
            return supplier
        raise ValidationError("Choose a valid creditor / supplier.")

    name = str(payload.get("supplier_name") or "").strip()[:120]
    phone = str(payload.get("supplier_phone") or "").strip()[:40]
    if len(name) < 2:
        raise ValidationError("Choose an existing creditor or enter the new creditor name.")
    if not phone:
        raise ValidationError("Enter a phone number for the new creditor.")
    existing = Party.objects.filter(
        branch=branch, kind="supplier", name__iexact=name, phone=phone
    ).first()
    if existing:
        return existing
    supplier = Party.objects.create(
        branch=branch,
        kind="supplier",
        name=name,
        phone=phone,
        email=str(payload.get("supplier_email") or "").strip()[:254],
        address=str(payload.get("supplier_address") or "").strip()[:2000],
    )
    s.audit(user, branch, "creditor.created_inline", supplier.pk, {"name": supplier.name, "phone": supplier.phone})
    return supplier


@transaction.atomic
def post_creditor_bill(user, branch, payload, key):
    """Create a payable that did not originate from inventory Purchasing."""
    s.permit(user, branch, "operate_finance")
    branch = s.lock_branch(branch)
    request = s.begin_request(user, branch, key, {"creditor_bill": True, **payload})
    if request.document_id:
        return request.document
    s.ensure_open(branch)

    supplier = _supplier_from_payload(user, branch, payload)
    amount = s.money(payload.get("amount"))
    if amount <= 0:
        raise ValidationError("Creditor amount must be greater than zero.")
    document_date = _business_date(payload.get("document_date"), "Bill date", timezone.localdate())
    if document_date > timezone.localdate():
        raise ValidationError("Bill date cannot be in the future.")
    due_date = _business_date(payload.get("due_date"), "Due date")
    if not due_date:
        raise ValidationError("A due date is required.")
    if due_date < document_date:
        raise ValidationError("Due date cannot be before the bill date.")

    category = str(payload.get("category") or "other").strip().lower()
    if category not in CATEGORY_LABELS:
        raise ValidationError("Choose a valid creditor category.")
    external_reference = str(payload.get("external_reference") or "").strip()[:120]
    note = str(payload.get("note") or "").strip()[:2000]
    if len(note) < 5:
        raise ValidationError("Add a meaningful description of what the business owes.")

    if external_reference and Document.objects.filter(
        branch=branch,
        party=supplier,
        kind__in=PAYABLE_KINDS,
        external_reference__iexact=external_reference,
    ).exists():
        raise ValidationError("This supplier invoice/reference is already recorded for this creditor.")

    doc = Document.objects.create(
        branch=branch,
        kind="creditor_charge",
        party=supplier,
        reference=s.reference("creditor_charge", branch),
        total=amount,
        paid=ZERO,
        due_date=due_date,
        document_date=document_date,
        external_reference=external_reference,
        payable_category=category,
        note=note,
        created_by=user,
    )
    request.document = doc
    request.save(update_fields=["document"])
    s.audit(user, branch, "creditor.bill_posted", doc.reference, {
        "supplier": supplier.pk,
        "amount": str(amount),
        "bill_date": str(document_date),
        "due_date": str(due_date),
        "external_reference": external_reference,
        "category": category,
        "note": note,
    })
    return doc


@transaction.atomic
def post_supplier_account_payment(user, branch, payload, key):
    """Pay one supplier account and allocate oldest due bills first, or one selected bill."""
    s.permit(user, branch, "operate_finance")
    branch = s.lock_branch(branch)
    request = s.begin_request(user, branch, key, {"creditor_payment": True, **payload})
    if request.document_id:
        return request.document
    s.ensure_open(branch)

    supplier = Party.objects.filter(
        pk=payload.get("party"), branch=branch, kind="supplier"
    ).first()
    if not supplier:
        raise ValidationError("Choose a valid creditor account.")

    open_bills = open_supplier_bills(supplier, for_update=True)
    target = str(payload.get("invoice") or "").strip()
    if target:
        open_bills = [(bill, amount) for bill, amount in open_bills if str(bill.pk) == target]
        if not open_bills:
            raise ValidationError("The selected creditor bill is no longer outstanding.")

    total_outstanding = sum((amount for _, amount in open_bills), ZERO)
    if total_outstanding <= 0:
        raise ValidationError("This creditor has no outstanding balance.")

    pay_full = str(payload.get("pay_full", "")).lower() in ("1", "true", "yes", "on")
    amount = total_outstanding if pay_full else s.money(payload.get("amount"))
    if amount <= 0:
        raise ValidationError("Enter a positive payment amount.")
    if amount > total_outstanding:
        raise ValidationError("Payment cannot exceed the selected outstanding balance.")

    method = payload.get("method")
    if not s.payment_method_enabled(method):
        raise ValidationError("Choose an enabled payment channel.")
    reference = str(payload.get("reference") or "").strip()[:100]
    note = str(payload.get("note") or "").strip()[:500]

    doc = Document.objects.create(
        branch=branch,
        kind="supplier_payment",
        party=supplier,
        original=open_bills[0][0],
        reference=s.reference("supplier_payment", branch),
        total=amount,
        paid=amount,
        document_date=timezone.localdate(),
        note=("Creditor account payment · oldest due first" + (f" · {note}" if note else "")),
        created_by=user,
    )
    s.payments(doc, [{"method": method, "amount": amount, "reference": reference}], -1)

    remaining = amount
    allocations = []
    for bill, outstanding in open_bills:
        if remaining <= 0:
            break
        applied = min(outstanding, remaining)
        Allocation.objects.create(payment_document=doc, invoice=bill, amount=applied)
        allocations.append({
            "bill": bill.reference,
            "supplier_reference": bill.external_reference,
            "amount": str(applied),
            "due_date": str(bill.due_date or ""),
        })
        remaining -= applied
    if remaining != ZERO:
        raise ValidationError("Creditor payment allocation did not reconcile to zero.")

    request.document = doc
    request.save(update_fields=["document"])
    s.audit(user, branch, "creditor.payment_posted", doc.reference, {
        "supplier": supplier.pk,
        "amount": str(amount),
        "method": method,
        "reference": reference,
        "allocation_method": "selected_bill" if target else "oldest_due_first",
        "allocations": allocations,
        "remaining_payable": str(supplier_account_snapshot(supplier)["outstanding"]),
        "note": note,
    })
    return doc


def creditor_statement_rows(party):
    """Chronological supplier-account statement with a running payable balance."""
    docs = Document.objects.filter(
        branch=party.branch,
        party=party,
    ).filter(
        Q(kind__in=PAYABLE_KINDS) | Q(kind="supplier_payment") |
        Q(kind="reversal", original__kind__in=["supplier_payment", "creditor_charge"])
    ).select_related("original", "created_by").order_by("created_at", "pk")

    running = ZERO
    rows = []
    for doc in docs:
        if doc.kind in PAYABLE_KINDS:
            change = s.balance(doc) + sum(
                (a.amount for a in doc.settlements.exclude(payment_document__correction__status="approved").all()),
                ZERO,
            )
            # The statement is historical cash/account activity, so show the original
            # invoice amount as the charge rather than its current outstanding balance.
            change = doc.total
            debit = ZERO
            credit = doc.total
        elif doc.kind == "supplier_payment":
            allocated = sum((a.amount for a in doc.allocations.all()), ZERO)
            change = -allocated
            debit = allocated
            credit = ZERO
        elif doc.original_id and doc.original.kind == "supplier_payment":
            allocated = sum((a.amount for a in doc.original.allocations.all()), ZERO)
            change = allocated
            debit = ZERO
            credit = allocated
        elif doc.original_id and doc.original.kind == "creditor_charge":
            change = -doc.original.total
            debit = doc.original.total
            credit = ZERO
        else:
            change = ZERO
            debit = ZERO
            credit = ZERO
        running += change
        rows.append({
            "date": doc.document_date or doc.created_at.date(),
            "entered_at": doc.created_at,
            "reference": doc.reference,
            "external_reference": doc.external_reference,
            "description": doc.note or doc.get_kind_display(),
            "type": doc.get_kind_display(),
            "charge": credit,
            "payment": debit,
            "running": running,
            "document": doc,
        })
    return rows
