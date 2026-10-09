"""Single reconciled account-statement timeline shared by screens and exports.

Read only. One row per invoice opening, allocation, or approved payment
correction; no double-counting initially collected invoice payments.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from . import services
from .models import Allocation, Document


def account_statement(party, branch):
    """Return chronological display rows and subledger-matched ending balance.

    The party must belong to the authorized location supplied by the caller.
    Never silently span branches, including on historical/cross-linked rows.
    """
    if party.branch_id != branch.pk:
        raise ValidationError("Account belongs to a different location.")
    valid_kinds = {"sale"} if party.kind == "customer" else {"purchase", "creditor_charge"}
    documents = list(Document.objects.filter(party=party, branch=branch).select_related(
        "original"
    ).order_by("created_at", "pk"))
    events = []
    for doc in documents:
        if doc.kind in valid_kinds:
            initial = doc.total - doc.paid
            if initial:
                events.append((doc.created_at, str(doc.pk), doc, initial))
        elif (doc.kind == "reversal" and doc.original_id
              and doc.original.kind == "creditor_charge"
              and party.kind == "supplier"):
            # A reversed supplier bill cancels the unpaid opening balance.
            events.append((doc.created_at, str(doc.pk), doc,
                           -(doc.original.total - doc.original.paid)))

    allocations = Allocation.objects.filter(
        invoice__party=party, invoice__branch=branch
    ).select_related("payment_document", "payment_document__correction__posted", "invoice").order_by(
        "payment_document__created_at", "pk"
    )
    for item in allocations:
        source = item.payment_document
        if source.branch_id != branch.pk:
            # This invalid relationship must be visible to financial controls,
            # and the statement must fail against the operational balance.
            continue
        events.append((source.created_at, str(source.pk), source, -item.amount))
        correction = getattr(source, "correction", None)
        if correction and correction.status == "approved" and correction.posted_id:
            reversal = correction.posted
            if reversal.branch_id != branch.pk:
                continue
            events.append((reversal.created_at, str(reversal.pk), reversal, item.amount))

    events.sort(key=lambda value: (value[0], value[1]))
    running = Decimal("0.00")
    rows = []
    for occurred_at, _, document, change in events:
        running += change
        rows.append({
            "doc": document, "date": timezone.localtime(occurred_at),
            "reference": document.reference, "type": document.get_kind_display(),
            "change": change, "running": running, "note": document.note,
        })
    if running != services.party_debt(party):
        raise ValidationError(
            "The statement does not agree with this account's balance. "
            "Review allocations and approved corrections before exporting."
        )
    return rows, running
