"""Customer-first receivables services for KOFAD's Debt Desk."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from . import services as s
from .models import Allocation, DebtSettings, Document, Party


ZERO = Decimal("0.00")


def open_customer_invoices(party, for_update=False):
    qs = Document.objects.filter(
        branch=party.branch,
        party=party,
        kind="sale",
    ).select_related("created_by").order_by(F("due_date").asc(nulls_last=True), "created_at", "pk")
    if for_update:
        qs = qs.select_for_update(of=("self",))
    rows = []
    for invoice in qs:
        outstanding = s.balance(invoice)
        if outstanding > 0:
            rows.append((invoice, outstanding))
    return rows


def debt_policy():
    return DebtSettings.objects.first() or DebtSettings()


def overdue_on(invoice, policy=None):
    if not invoice.due_date:
        return None
    policy = policy or debt_policy()
    return invoice.due_date + timedelta(days=policy.overdue_grace_days)


def _invoice_age(invoice, today, policy=None):
    threshold = overdue_on(invoice, policy)
    if not threshold or today <= threshold:
        return 0
    return (today - threshold).days


def customer_account_snapshot(party):
    today = timezone.localdate()
    policy = debt_policy()
    invoices = open_customer_invoices(party)
    outstanding = sum((amount for _, amount in invoices), ZERO)
    overdue = sum(
        (amount for invoice, amount in invoices if overdue_on(invoice, policy) and today > overdue_on(invoice, policy)),
        ZERO,
    )
    due_today = sum(
        (amount for invoice, amount in invoices if invoice.due_date == today),
        ZERO,
    )
    sales = Document.objects.filter(branch=party.branch, party=party, kind="sale")
    returns = Document.objects.filter(branch=party.branch, party=party, kind="return")
    collections = Document.objects.filter(branch=party.branch, party=party, kind="collection").exclude(
        correction__status="approved"
    )
    total_sales = sales.aggregate(total=Sum("total"))["total"] or ZERO
    total_returns = returns.aggregate(total=Sum("total"))["total"] or ZERO
    total_collections = collections.aggregate(total=Sum("total"))["total"] or ZERO
    last_sale = sales.order_by("-created_at").first()
    last_payment = collections.order_by("-created_at").first()

    invoice_rows = []
    aging = {"current": ZERO, "days_1_30": ZERO, "days_31_60": ZERO, "days_61_plus": ZERO}
    maximum_days_overdue = 0
    for invoice, amount in invoices:
        days_overdue = _invoice_age(invoice, today, policy)
        maximum_days_overdue = max(maximum_days_overdue, days_overdue)
        if days_overdue == 0:
            aging["current"] += amount
            status = "due_today" if invoice.due_date == today else "current"
        elif days_overdue <= 30:
            aging["days_1_30"] += amount
            status = "overdue"
        elif days_overdue <= 60:
            aging["days_31_60"] += amount
            status = "overdue"
        else:
            aging["days_61_plus"] += amount
            status = "overdue"
        invoice_rows.append({
            "invoice": invoice,
            "outstanding": amount,
            "days_overdue": days_overdue,
            "status": status,
            "paid_so_far": invoice.total - amount,
            "overdue_on": overdue_on(invoice, policy),
        })

    recent_payments = list(collections.select_related("created_by").order_by("-created_at")[:12])
    credit_limit = party.credit_limit or ZERO
    available_credit = None
    credit_usage_percent = None
    if credit_limit > 0:
        available_credit = max(credit_limit - outstanding, ZERO)
        credit_usage_percent = min((outstanding * Decimal("100") / credit_limit), Decimal("999.99")).quantize(
            Decimal(".01")
        )

    next_due = min((invoice.due_date for invoice, _ in invoices if invoice.due_date), default=None)
    return {
        "outstanding": outstanding,
        "overdue": overdue,
        "due_today": due_today,
        "invoice_count": len(invoices),
        "total_sales": total_sales,
        "total_returns": total_returns,
        "total_collections": total_collections,
        "last_sale": last_sale,
        "last_payment": last_payment,
        "invoices": invoices,
        "invoice_rows": invoice_rows,
        "recent_payments": recent_payments,
        "aging": aging,
        "maximum_days_overdue": maximum_days_overdue,
        "next_due": next_due,
        "overdue_grace_days": policy.overdue_grace_days,
        "credit_limit": credit_limit,
        "available_credit": available_credit,
        "credit_usage_percent": credit_usage_percent,
    }


def debt_customers(branch, query=""):
    parties = Party.objects.filter(branch=branch, kind="customer")
    query = str(query or "").strip()[:100]
    if query:
        parties = parties.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    rows = []
    for party in parties.order_by("name")[:500]:
        snapshot = customer_account_snapshot(party)
        if snapshot["outstanding"] > 0:
            rows.append({"party": party, **snapshot})
    rows.sort(
        key=lambda row: (
            -int(row["maximum_days_overdue"]),
            -row["overdue"],
            -row["outstanding"],
            row["party"].name.casefold(),
        )
    )
    return rows


@transaction.atomic
def post_customer_payment(user, branch, payload, key):
    """Record one customer-account payment and allocate oldest due invoices first."""
    s.permit(user, branch, "operate_finance")
    branch = s.lock_branch(branch)
    request = s.begin_request(user, branch, key, payload)
    if request.document_id:
        return request.document
    s.ensure_open(branch)

    party = Party.objects.filter(
        pk=payload.get("party"),
        branch=branch,
        kind="customer",
    ).first()
    if not party:
        raise ValidationError("Choose a valid customer account.")

    invoices = open_customer_invoices(party, for_update=True)
    total_outstanding = sum((amount for _, amount in invoices), ZERO)
    if total_outstanding <= 0:
        raise ValidationError("This customer has no outstanding debt.")

    pay_full = str(payload.get("pay_full", "")).lower() in ("1", "true", "yes", "on")
    amount = total_outstanding if pay_full else s.money(payload.get("amount"))
    if amount <= 0:
        raise ValidationError("Enter a positive payment amount.")
    if amount > total_outstanding:
        raise ValidationError("Payment cannot exceed the customer's outstanding balance.")

    method = payload.get("method")
    if not s.payment_method_enabled(method):
        raise ValidationError("Choose an enabled payment channel.")
    reference = str(payload.get("reference", "")).strip()[:100]
    note = str(payload.get("note", "")).strip()[:500]

    doc = Document.objects.create(
        branch=branch,
        kind="collection",
        party=party,
        original=invoices[0][0],
        reference=s.reference("collection", branch),
        total=amount,
        paid=amount,
        note=("Customer account payment · oldest due first" + (f" · {note}" if note else "")),
        created_by=user,
    )
    s.payments(doc, [{"method": method, "amount": amount, "reference": reference}], 1)

    remaining = amount
    allocations = []
    for invoice, outstanding in invoices:
        if remaining <= 0:
            break
        applied = min(outstanding, remaining)
        Allocation.objects.create(payment_document=doc, invoice=invoice, amount=applied)
        allocations.append({
            "invoice": invoice.reference,
            "amount": str(applied),
            "due_date": str(invoice.due_date or ""),
        })
        remaining -= applied

    if remaining != ZERO:
        raise ValidationError("Payment allocation did not reconcile to zero.")

    request.document = doc
    request.save(update_fields=["document"])
    s.audit(user, branch, "customer_debt.payment_posted", doc.reference, {
        "customer": party.pk,
        "amount": str(amount),
        "method": method,
        "reference": reference,
        "allocation_method": "oldest_due_first",
        "allocations": allocations,
        "remaining_customer_debt": str(total_outstanding - amount),
        "note": note,
    })
    from .debt_email import safe_queue_debt_payment
    transaction.on_commit(lambda document_id=doc.pk: safe_queue_debt_payment(document_id))
    from . import automations
    transaction.on_commit(
        lambda document_id=doc.pk, actor_id=user.pk:
            automations.safe_prepare_payment_confirmation(document_id, actor_id)
    )
    return doc


def debt_overview(branch, query=""):
    rows = debt_customers(branch, query)
    today = timezone.localdate()
    start = today - timedelta(days=30)
    collections = Document.objects.filter(
        branch=branch,
        kind="collection",
        created_at__date__gte=start,
        created_at__date__lte=today,
    ).exclude(correction__status="approved")
    collected_30_days = collections.aggregate(total=Sum("total"))["total"] or ZERO
    return {
        "rows": rows,
        "total_receivables": sum((row["outstanding"] for row in rows), ZERO),
        "overdue": sum((row["overdue"] for row in rows), ZERO),
        "due_today": sum((row["due_today"] for row in rows), ZERO),
        "customers_owing": len(rows),
        "overdue_customers": sum(1 for row in rows if row["overdue"] > 0),
        "collected_30_days": collected_30_days,
    }
