from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import F, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import render
from django.utils import timezone

from .context import shell
from .exports import export
from .models import Document, Line, Party, Payment, PayrollEntry, PayrollPeriod, Stock
from .services import audit, balance
from .views import protected


EXPENSE_CATEGORIES = [
    ("transport", "Transport & delivery"),
    ("fuel", "Fuel"),
    ("utilities", "Utilities"),
    ("rent", "Rent & premises"),
    ("maintenance", "Repairs & maintenance"),
    ("marketing", "Marketing & promotion"),
    ("salary", "Salary / staff welfare"),
    ("tax", "Taxes, levies & fees"),
    ("office", "Office & administration"),
    ("security", "Security"),
    ("professional", "Professional services"),
    ("other", "Other"),
]
CATEGORY_LABELS = dict(EXPENSE_CATEGORIES)
ZERO = Decimal("0")


def _range(request):
    today = timezone.localdate()
    start = request.GET.get("start", today.replace(day=1).isoformat())
    end = request.GET.get("end", today.isoformat())
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise ValidationError("Enter a valid accounting date range.")
    if first > last:
        raise ValidationError("Accounting start date cannot be after end date.")
    return first, last, start, end


def _sum(queryset, field="total"):
    return queryset.aggregate(value=Sum(field))["value"] or ZERO


def accounting_snapshot(branch, first, last, category="", method=""):
    docs = Document.objects.filter(branch=branch, created_at__date__range=(first, last))
    sales = _sum(docs.filter(kind="sale"))
    returns = _sum(docs.filter(kind="return"))
    net_sales = sales - returns

    cogs = ZERO
    sales_lines = Line.objects.filter(
        document__branch=branch, document__created_at__date__range=(first, last),
        document__kind__in=["sale", "return"],
    ).select_related("document")
    for line in sales_lines:
        amount = line.unit_cost * line.quantity * line.factor
        cogs += -amount if line.document.kind == "return" else amount
    gross_profit = net_sales - cogs

    expense_docs = docs.filter(kind="expense")
    if category:
        expense_docs = expense_docs.filter(expense_category=category)
    expenses = _sum(expense_docs)
    reversals = docs.filter(kind="reversal", original__kind="expense")
    if category:
        reversals = reversals.filter(original__expense_category=category)
    expenses -= _sum(reversals)
    inventory_losses = _sum(docs.filter(kind="inventory_writeoff"))

    payroll_entries = PayrollEntry.objects.filter(
        period__branch=branch,
        period__end_date__range=(first, last),
        period__status__in=["locked", "reconciled"],
    )
    payroll = payroll_entries.aggregate(gross=Sum("gross_pay"), employer=Sum("employer_pension"))
    payroll_gross = payroll["gross"] or ZERO
    employer_pension = payroll["employer"] or ZERO
    payroll_cost = payroll_gross + employer_pension

    operating_result = gross_profit - expenses - inventory_losses - payroll_cost
    gross_margin = (gross_profit / net_sales * 100) if net_sales else ZERO
    operating_margin = (operating_result / net_sales * 100) if net_sales else ZERO

    current_receivables = ZERO
    for doc in Document.objects.filter(branch=branch, kind="sale", party__isnull=False):
        current_receivables += max(ZERO, balance(doc))
    current_payables = ZERO
    for doc in Document.objects.filter(branch=branch, kind="purchase", party__isnull=False):
        current_payables += max(ZERO, balance(doc))
    stock_value = sum((row.quantity * row.product.cost for row in Stock.objects.filter(branch=branch).select_related("product")), ZERO)

    payment_rows = Payment.objects.filter(
        document__branch=branch, document__created_at__date__range=(first, last)
    )
    if method:
        payment_rows = payment_rows.filter(method=method)
    channels = defaultdict(lambda: {"in": ZERO, "out": ZERO, "net": ZERO})
    for payment in payment_rows:
        bucket = channels[payment.method]
        if payment.direction > 0:
            bucket["in"] += payment.amount
        else:
            bucket["out"] += payment.amount
        bucket["net"] += payment.amount * payment.direction

    expense_breakdown = defaultdict(lambda: ZERO)
    base_expenses = docs.filter(kind="expense")
    for row in base_expenses:
        expense_breakdown[row.expense_category or "other"] += row.total
    for row in docs.filter(kind="reversal", original__kind="expense").select_related("original"):
        expense_breakdown[row.original.expense_category or "other"] -= row.total
    expense_rows = [
        {"code": code, "category": CATEGORY_LABELS.get(code, code.replace("_", " ").title()), "amount": amount}
        for code, amount in sorted(expense_breakdown.items(), key=lambda item: item[1], reverse=True)
    ]

    trend = []
    cursor = first.replace(day=1)
    while cursor <= last:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_last = min(last, next_month - timedelta(days=1))
        month_first = max(first, cursor)
        month_docs = Document.objects.filter(branch=branch, created_at__date__range=(month_first, month_last))
        month_sales = _sum(month_docs.filter(kind="sale")) - _sum(month_docs.filter(kind="return"))
        month_expense = _sum(month_docs.filter(kind="expense")) - _sum(month_docs.filter(kind="reversal", original__kind="expense"))
        month_payroll = PayrollEntry.objects.filter(
            period__branch=branch, period__end_date__range=(month_first, month_last),
            period__status__in=["locked", "reconciled"],
        ).aggregate(g=Sum("gross_pay"), e=Sum("employer_pension"))
        month_payroll_cost = (month_payroll["g"] or ZERO) + (month_payroll["e"] or ZERO)
        trend.append({
            "month": cursor.strftime("%b %Y"), "sales": month_sales,
            "expenses": month_expense, "payroll": month_payroll_cost,
        })
        cursor = next_month

    return {
        "net_sales": net_sales, "cogs": cogs, "gross_profit": gross_profit,
        "gross_margin": gross_margin.quantize(Decimal("0.01")),
        "expenses": expenses, "inventory_losses": inventory_losses,
        "payroll_gross": payroll_gross, "employer_pension": employer_pension,
        "payroll_cost": payroll_cost, "operating_result": operating_result,
        "operating_margin": operating_margin.quantize(Decimal("0.01")),
        "receivables": current_receivables, "payables": current_payables,
        "stock_value": stock_value, "channels": dict(channels),
        "expense_rows": expense_rows, "trend": trend,
    }


@protected("view_reports|operate_finance")
def accounting(request, branch):
    first, last, start, end = _range(request)
    category = request.GET.get("category", "").strip()
    method = request.GET.get("method", "").strip()
    data = accounting_snapshot(branch, first, last, category, method)
    prior_days = (last - first).days + 1
    prior_last = first - timedelta(days=1)
    prior_first = prior_last - timedelta(days=prior_days - 1)
    prior = accounting_snapshot(branch, prior_first, prior_last)
    comparisons = {}
    for key in ["net_sales", "gross_profit", "expenses", "payroll_cost", "operating_result"]:
        old, new = prior[key], data[key]
        comparisons[key] = None if old == 0 else ((new - old) / abs(old) * 100).quantize(Decimal("0.1"))
    max_trend = max(
        [abs(row["sales"]) for row in data["trend"]] +
        [abs(row["expenses"] + row["payroll"]) for row in data["trend"]] + [Decimal("1")]
    )
    for row in data["trend"]:
        row["sales_width"] = int(abs(row["sales"]) / max_trend * 100)
        row["cost_width"] = int(abs(row["expenses"] + row["payroll"]) / max_trend * 100)
    return render(request, "accounting.html", {
        "title": "Accounting intelligence", "data": data, "prior": prior,
        "comparisons": comparisons, "start": start, "end": end,
        "category": category, "method": method, "categories": EXPENSE_CATEGORIES,
        "methods": Payment.METHODS, "prior_start": prior_first, "prior_end": prior_last,
    })


@protected("view_reports|operate_finance")
def accounting_export(request, branch, format):
    first, last, start, end = _range(request)
    category = request.GET.get("category", "").strip()
    method = request.GET.get("method", "").strip()
    data = accounting_snapshot(branch, first, last, category, method)
    rows = [
        {"line": "Net sales", "amount": data["net_sales"], "type": "Revenue"},
        {"line": "Cost of goods sold", "amount": data["cogs"], "type": "Direct cost"},
        {"line": "Gross profit", "amount": data["gross_profit"], "type": "Margin"},
        {"line": "Operating expenses", "amount": data["expenses"], "type": "Operating cost"},
        {"line": "Inventory losses", "amount": data["inventory_losses"], "type": "Operating cost"},
        {"line": "Payroll gross", "amount": data["payroll_gross"], "type": "People cost"},
        {"line": "Employer pension", "amount": data["employer_pension"], "type": "People cost"},
        {"line": "Operating result", "amount": data["operating_result"], "type": "Management result"},
        {"line": "Current receivables", "amount": data["receivables"], "type": "Balance snapshot"},
        {"line": "Current payables", "amount": data["payables"], "type": "Balance snapshot"},
        {"line": "Current stock value", "amount": data["stock_value"], "type": "Balance snapshot"},
    ]
    audit(request.user, branch, "accounting.exported", format, {
        "start": start, "end": end, "category": category, "method": method,
    })
    return export(
        rows, format, f"Management accounting · {start} to {end}", shell(request)["company"],
        [("line", "Account / KPI"), ("type", "Classification"), ("amount", "Amount")],
        filename=f"kofad-management-accounting-{start}-{end}", sheet_name="Management accounts",
        metadata={
            "Location": branch.name, "From": first, "To": last,
            "Expense category": CATEGORY_LABELS.get(category, "All") if category else "All",
            "Payment channel": dict(Payment.METHODS).get(method, "All") if method else "All",
        },
        summary={
            "Net sales": data["net_sales"], "Gross profit": data["gross_profit"],
            "Operating result": data["operating_result"], "Gross margin %": data["gross_margin"],
        },
        notes=[
            "Management accounting is built from KOFAD operational records and payroll. It is not a statutory general ledger or audited financial statement.",
            "Receivables, payables and stock values are current snapshots; period filters apply to trading, expenses, losses and payroll.",
        ],
    )
