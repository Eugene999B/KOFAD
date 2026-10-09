"""Read-only financial integrity controls for KOFAD source records.

This is an exception register, NOT a second ledger or an automated bookkeeper:
it never edits a transaction, retries a gateway charge, or assumes provider
settlement confirms cash received in a bank account.
"""
from collections import Counter, defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.utils import timezone

from . import services as s
from .models import Allocation, Closing, Document, HeldSale, Line, Payment

ZERO = Decimal("0.00")
LIMIT = 25000

COLUMNS = [
    ("date", "Business date"), ("source", "Control area"),
    ("reference", "Source reference"), ("check", "Reconciliation check"),
    ("expected", "Expected (GHS)"), ("actual", "Recorded (GHS)"),
    ("difference", "Difference (GHS)"), ("status", "Status"),
    ("details", "Explanation / investigation"),
]


def _limit(queryset, label):
    if queryset.count() > LIMIT:
        raise ValidationError(
            f"{label} has more than {LIMIT:,} records in this scope. Narrow the date range. "
            "No partial audit or misleading totals were generated."
        )
    return queryset


def financial_controls(branch, first, last):
    """Return individually explainable checks and accurate exception counts."""
    checks = []

    def record(day, source, ref, title, expected, actual, detail="", *, condition=True, discrepancy="REVIEW"):
        expected, actual = Decimal(str(expected)), Decimal(str(actual))
        diff = actual - expected
        status = "OK" if diff == ZERO and condition else discrepancy
        checks.append({
            "date": day, "source": source, "reference": str(ref),
            "check": title, "expected": expected, "actual": actual,
            "difference": diff, "status": status, "details": detail,
        })

    documents = list(_limit(Document.objects.filter(
        branch=branch, created_at__date__range=(first, last)
    ).select_related("party", "original", "correction").prefetch_related(
        Prefetch("lines", queryset=Line.objects.all(), to_attr="audit_lines"),
        Prefetch("payments", queryset=Payment.objects.all(), to_attr="audit_payments"),
        Prefetch("allocations", queryset=Allocation.objects.select_related("invoice"), to_attr="audit_allocations"),
    ).order_by("created_at", "pk"), "Financial documents"))

    for doc in documents:
        day, ref = timezone.localtime(doc.created_at).date(), doc.reference
        lines, payment_rows, allocations = doc.audit_lines, doc.audit_payments, doc.audit_allocations
        if doc.kind in {"sale", "purchase", "return", "supplier_return"}:
            expected = sum((line.total for line in lines), ZERO)
            record(day, "Document", ref, "Document total = saved product lines",
                   expected, doc.total, f"{doc.get_kind_display()} · {len(lines)} lines")
            for line in lines:
                expected_line = line.unit_price * line.quantity
                record(day, "Product line", ref, f"Saved line #{line.pk} quantity × unit price",
                       expected_line, line.total, line.description)
        # Correctly account for owner-funded and unpaid expenses that
        # intentionally have no payment-channel posting.
        exempt = (
            (doc.kind == "expense" and doc.expense_funding_source in {
                "owner_manager_funds", "unpaid_credit",
            })
            or (doc.kind == "reversal" and doc.original_id
                and doc.original.kind == "expense"
                and doc.original.expense_funding_source in {
                    "owner_manager_funds", "unpaid_credit",
                })
        )
        if doc.kind in {"sale", "purchase", "return", "supplier_return", "collection",
                        "supplier_payment", "expense", "reversal"} and not exempt:
            expected_paid = sum((p.amount for p in payment_rows), ZERO)
            record(day, "Payment", ref, "Document paid = payment channel entries",
                   expected_paid, doc.paid, f"{len(payment_rows)} channel records")
        if exempt and payment_rows:
            record(day, "Payment", ref, "Externally funded/unpaid expense has no business channel payment",
                   ZERO, sum((p.amount for p in payment_rows), ZERO),
                   "Check expense funding source and channel movements.")
        directions = {"sale": 1, "collection": 1, "supplier_return": 1,
                      "purchase": -1, "expense": -1, "return": -1, "supplier_payment": -1}
        sign = directions.get(doc.kind)
        if exempt:
            sign = None
        if doc.kind == "reversal" and doc.original_id:
            sign = -directions.get(doc.original.kind, 0)
        if sign:
            for p in payment_rows:
                record(day, "Payment direction", ref,
                       f"{p.get_method_display()} cash-flow sign", sign, p.direction,
                       f"Payment #{p.pk}; expect {'inflow' if sign > 0 else 'outflow'}")
        if doc.kind in {"collection", "supplier_payment", "return", "supplier_return"}:
            amount = sum((a.amount for a in allocations), ZERO)
            record(day, "Allocation", ref, "Allocation + refund/settled amount = source total",
                   doc.total, amount + (doc.paid if doc.kind in {"return", "supplier_return"} else ZERO),
                   "Payments and credit notes must have matching allocations.")
            for a in allocations:
                record(day, "Allocation", ref, "Allocation uses same business location",
                       1, int(a.invoice.branch_id == branch.pk),
                       f"Invoice {a.invoice.reference}")
        if doc.kind == "creditor_charge" and doc.payable_category in {"inventory", "equipment", "loan"}:
            record(day, "Accounting classification", ref,
                   "Special creditor bill needs accountant classification", 1, 0,
                   f"Category '{doc.payable_category}' currently maps to a generic expense. "
                   "Confirm inventory receipt, asset capitalization or loan principal before final accounts.")
        if doc.kind in {"sale", "purchase", "creditor_charge"}:
            remaining = s.balance(doc)
            if remaining < ZERO:
                record(day, "Receivables / payables", ref, "Outstanding balance must be nonnegative",
                       ZERO, -remaining,
                       "Allocated payments/credits exceed the amount legally outstanding.")

    # Reconciliation of payroll and manual salary expenses is also important:
    # these can represent the same economic wage obligation entered twice.
    salary_count = Document.objects.filter(
        branch=branch, kind="expense", expense_category="salary",
        created_at__date__range=(first, last),
    ).count()
    if salary_count:
        from .models import PayrollEntry
        recorded_payroll = PayrollEntry.objects.filter(
            period__branch=branch,
            period__status__in=["locked", "reconciled"],
            period__end_date__range=(first, last),
        ).exists()
        if recorded_payroll:
            record(last, "Payroll", "PAYROLL-VS-EXPENSE",
                   "Manual salary expense may duplicate locked payroll accrual", 1, 0,
                   f"{salary_count} salary expense document(s) and locked payroll in the same period. "
                   "Verify they represent separate obligations before booking.")

    from marketplace.models import MarketPaymentAttempt, OnlineOrder
    orders = list(_limit(OnlineOrder.objects.filter(
        branch=branch, created_at__date__range=(first, last)
    ).select_related("sale_document").prefetch_related("lines"), "Online orders"))
    for order in orders:
        day, ref = timezone.localtime(order.created_at).date(), order.public_reference
        product_total = sum((line.total for line in order.lines.all()), ZERO)
        record(day, "Market order", ref, "Saved order subtotal matches immutable item lines",
               product_total, order.subtotal, order.get_payment_status_display())
        record(day, "Market order", ref, "Payable total = subtotal + delivery",
               order.subtotal + order.delivery_fee, order.total,
               "No hidden extra checkout amount.")
        if order.sale_document_id:
            record(day, "Online ledger", ref, "Paid order total matches posted sale document",
                   order.total, order.sale_document.total,
                   order.sale_document.reference,
                   condition=order.payment_status in {"paid", "refunded"})
        elif order.payment_status in {"paid", "refunded"}:
            record(day, "Online ledger", ref, "Paid order requires a reconciled sales document",
                   1, 0, f"Ledger state: {order.ledger_status}; investigate posting")
        for line in order.lines.all():
            record(day, "Market item", ref, f"Line #{line.pk} qty × fixed unit price",
                   line.quantity * line.unit_price, line.total, line.sku)

    attempts = _limit(MarketPaymentAttempt.objects.filter(
        order__branch=branch, created_at__date__range=(first, last)
    ).select_related("order"), "Provider attempts")
    for attempt in attempts:
        day = timezone.localtime(attempt.created_at).date()
        record(day, "Provider", attempt.reference,
               "Provider request amount matches frozen online order total",
               attempt.order.total, attempt.amount,
               f"{attempt.provider} · {attempt.status} · {attempt.order.public_reference}")
        if attempt.status in {"success", "paid"} and attempt.order.payment_status not in {"paid", "refunded"}:
            record(day, "Provider", attempt.reference,
                   "Verified provider payment must have a paid order", 1, 0,
                   "Potential settled-but-unposted payment. Investigate before retrying.")

    pos_held = list(_limit(HeldSale.objects.filter(
        branch=branch, created_at__date__range=(first, last),
        label__startswith="Paystack MoMo ",
    ) | HeldSale.objects.filter(
        branch=branch, created_at__date__range=(first, last),
        label__startswith="Hubtel MoMo ",
    ), "Staff POS provider payments"))
    for held in pos_held:
        if not isinstance(held.cart, dict):
            continue
        state = held.cart.get("payment_request") or {}
        if not isinstance(state, dict):
            continue
        ref = state.get("reference") or held.label
        if state.get("status") == "success":
            doc = Document.objects.filter(pk=state.get("document_id"), branch=branch).first() if state.get("document_id") else None
            actual = doc.paid if doc else ZERO
            expected = Decimal(str(state.get("amount") or "0"))
            record(timezone.localtime(held.created_at).date(), "Staff MoMo", ref,
                   "Provider-verified MoMo matches posted sale payment", expected, actual,
                   f"{state.get('provider') or 'Paystack'} · {'posted' if doc else 'MISSING SALE'}",
                   condition=doc is not None)

    closings = _limit(Closing.objects.filter(branch=branch, date__range=(first, last)),
                      "Daily closings")
    for closing in closings:
        recomputed_channels = s.channel_totals(branch, closing.date)
        recomputed_channels["cash"] += closing.opening_cash + closing.cash_in - closing.cash_out
        for method in ("cash", "momo", "bank", "card"):
            try:
                expected = Decimal(str((closing.expected or {}).get(method, "0")))
                counted = Decimal(str((closing.counted or {}).get(method, "0")))
            except (ValueError, TypeError):
                record(closing.date, "Daily closing", closing.date,
                       f"{method.upper()} counted reconciliation", 1, 0,
                       "Invalid saved channel amount: manager investigation required")
            else:
                record(closing.date, "Daily closing", closing.date,
                       f"{method.upper()} stored expected versus recomputed source movements",
                       recomputed_channels[method], expected,
                       "Recalculate from signed transaction channels, payroll and recorded cash float; "
                       "investigate a closing snapshot that differs from its source records.")
                record(closing.date, "Daily closing", closing.date,
                       f"{method.upper()} counted versus expected", expected, counted,
                       "Physical/provider balance differs from the closing; variance requires review",
                       discrepancy="VARIANCE")

    counts = Counter(item["status"] for item in checks)
    total_variance = sum((abs(x["difference"]) for x in checks if x["status"] != "OK"), ZERO)
    checks.sort(key=lambda x: (x["date"], x["source"], x["reference"], x["check"]))
    return checks, {
        "Checks completed": len(checks),
        "Checks passed": counts["OK"],
        "Exceptions for investigation": counts["REVIEW"],
        "Closing channel variances": counts["VARIANCE"],
        "Sum of absolute flagged differences (GHS)": total_variance,
        "Scope": "Read-only source and closing reconciliation; no provider settlement/bank-feed confirmation",
    }


def payment_channel_rows(branch, first, last):
    """Signed cash-channel register; negative amounts are real outflows."""
    data = []
    payments = _limit(Payment.objects.filter(
        document__branch=branch, document__created_at__date__range=(first, last)
    ).select_related("document", "document__party"), "Payment movements")
    for payment in payments:
        doc = payment.document
        data.append({
            "date": timezone.localtime(doc.created_at),
            "reference": doc.reference, "source": doc.get_kind_display(),
            "party": doc.party.name if doc.party else "Walk-in / internal",
            "channel": payment.get_method_display(),
            "direction": "Inflow" if payment.direction > 0 else "Outflow",
            "amount": payment.amount * payment.direction,
            "provider_reference": payment.reference,
            "recorded_by": doc.created_by.username if doc.created_by_id else "",
        })
    from .models import PayrollPayment
    wages = _limit(PayrollPayment.objects.filter(
        entry__period__branch=branch, created_at__date__range=(first, last)
    ).select_related("entry__worker"), "Payroll payments")
    for payment in wages:
        data.append({
            "date": timezone.localtime(payment.created_at),
            "reference": payment.reference, "source": "Salary payment",
            "party": payment.entry.worker.full_name,
            "channel": dict(Payment.METHODS).get(payment.method, payment.method),
            "direction": "Outflow", "amount": -payment.amount,
            "provider_reference": payment.reference,
            "recorded_by": "",
        })
    data.sort(key=lambda x: (x["date"], x["reference"]))
    return data, [
        ("date", "Timestamp"), ("reference", "Document / reference"),
        ("source", "Source"), ("party", "Customer / supplier / worker"),
        ("channel", "Payment channel"), ("direction", "Cash direction"),
        ("amount", "Signed amount (GHS)"), ("provider_reference", "Provider reference"),
        ("recorded_by", "Recorded by"),
    ]
