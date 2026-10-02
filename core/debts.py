"""Customer-first receivables services for KOFAD's Debt Desk."""
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from . import services as s
from .models import Allocation, Document, Party


ZERO = Decimal("0.00")


def open_customer_invoices(party, for_update=False):
    qs = Document.objects.filter(
        branch=party.branch,
        party=party,
        kind="sale",
    ).order_by(F("due_date").asc(nulls_last=True), "created_at", "pk")
    if for_update:
        qs = qs.select_for_update(of=("self",))
    rows = []
    for invoice in qs:
        outstanding = s.balance(invoice)
        if outstanding > 0:
            rows.append((invoice, outstanding))
    return rows


def customer_account_snapshot(party):
    invoices = open_customer_invoices(party)
    outstanding = sum((amount for _, amount in invoices), ZERO)
    overdue = sum(
        (amount for invoice, amount in invoices if invoice.due_date and invoice.due_date < timezone.localdate()),
        ZERO,
    )
    due_today = sum(
        (amount for invoice, amount in invoices if invoice.due_date == timezone.localdate()),
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
    rows.sort(key=lambda row: (-row["overdue"], -row["outstanding"], row["party"].name.casefold()))
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

    doc = Document.objects.create(
        branch=branch,
        kind="collection",
        party=party,
        original=invoices[0][0],
        reference=s.reference("collection", branch),
        total=amount,
        paid=amount,
        note="Customer account payment · oldest due first",
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
        "allocation_method": "oldest_due_first",
        "allocations": allocations,
        "remaining_customer_debt": str(total_outstanding - amount),
    })
    return doc


def debt_overview(branch, query=""):
    rows = debt_customers(branch, query)
    return {
        "rows": rows,
        "total_receivables": sum((row["outstanding"] for row in rows), ZERO),
        "overdue": sum((row["overdue"] for row in rows), ZERO),
        "due_today": sum((row["due_today"] for row in rows), ZERO),
        "customers_owing": len(rows),
    }
