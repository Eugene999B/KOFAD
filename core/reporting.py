from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Branch, Document, Line, Party, Payment, PayrollEntry, PayrollPayment, Product,
    QuarantineItem, Stock, Worker,
)
from .services import balance

ZERO = Decimal("0")
REGISTER = [("reference", "Reference"), ("date", "Date"), ("kind", "Type"), ("party", "Party"), ("total", "Total"), ("paid", "Paid"), ("balance", "Balance")]
FAMILIES = {
    "executive": "Executive performance",
    "trend": "Sales, cost & operating trend",
    "sales": "Sales and gross profit",
    "customers": "Customer performance",
    "creditors": "Creditors & payable aging",
    "expenses": "Expense analysis",
    "payments": "Payments & cash channels",
    "inventory": "Inventory valuation",
    "aging": "Receivables aging",
    "losses": "Inventory losses",
    "workforce": "Workforce",
    "payroll": "Payroll",
    "branches": "Branch comparison",
    "register": "Transaction register",
}


def limited(queryset, maximum):
    if queryset.count() > maximum:
        raise ValidationError(
            "This report exceeds its export limit. Narrow the date range; no partial totals were produced."
        )
    return queryset


def _sum(queryset, field="total"):
    return queryset.aggregate(value=Sum(field))["value"] or ZERO


def _period_metrics(branch, first, last):
    docs = Document.objects.filter(branch=branch, created_at__date__range=(first, last))
    sales = _sum(docs.filter(kind="sale"))
    returns = _sum(docs.filter(kind="return"))
    net_sales = sales - returns
    cogs = ZERO
    for line in Line.objects.filter(
        document__branch=branch, document__created_at__date__range=(first, last),
        document__kind__in=["sale", "return"],
    ).select_related("document"):
        amount = line.unit_cost * line.quantity * line.factor
        cogs += -amount if line.document.kind == "return" else amount
    gross_profit = net_sales - cogs
    expenses = _sum(docs.filter(kind="expense")) - _sum(docs.filter(kind="reversal", original__kind="expense"))
    losses = _sum(docs.filter(kind="inventory_writeoff"))
    payroll = PayrollEntry.objects.filter(
        period__branch=branch, period__end_date__range=(first, last),
        period__status__in=["locked", "reconciled"],
    ).aggregate(gross=Sum("gross_pay"), employer=Sum("employer_pension"))
    payroll_cost = (payroll["gross"] or ZERO) + (payroll["employer"] or ZERO)
    operating = gross_profit - expenses - losses - payroll_cost
    sales_count = docs.filter(kind="sale").count()
    average_sale = sales / sales_count if sales_count else ZERO
    return {
        "net_sales": net_sales, "cogs": cogs, "gross_profit": gross_profit,
        "expenses": expenses, "losses": losses, "payroll_cost": payroll_cost,
        "operating_result": operating, "sales_count": Decimal(sales_count),
        "average_sale": average_sale,
    }


def business_kpis(branch, first, last):
    current = _period_metrics(branch, first, last)
    days = (last - first).days + 1
    prior_last = first - timedelta(days=1)
    prior_first = prior_last - timedelta(days=days - 1)
    prior = _period_metrics(branch, prior_first, prior_last)
    output = {}
    for key, value in current.items():
        old = prior[key]
        output[key] = {
            "value": value,
            "previous": old,
            "change": None if old == 0 else ((value - old) / abs(old) * 100).quantize(Decimal("0.1")),
        }
    output["prior_first"] = prior_first
    output["prior_last"] = prior_last
    return output


def _match(rows, query):
    query = (query or "").casefold().strip()
    if not query:
        return rows
    return [
        row for row in rows
        if query in " ".join(str(value) for value in row.values()).casefold()
    ]


def build_report(branch, first, last, family="register", query="", category="", method=""):
    docs = Document.objects.filter(branch=branch, created_at__date__range=(first, last))

    if family == "executive":
        kpis = business_kpis(branch, first, last)
        labels = [
            ("net_sales", "Net sales"), ("cogs", "Cost of goods sold"),
            ("gross_profit", "Gross profit"), ("expenses", "Operating expenses"),
            ("payroll_cost", "Payroll employer cost"), ("losses", "Inventory losses"),
            ("operating_result", "Operating result"), ("sales_count", "Sales count"),
            ("average_sale", "Average sale"),
        ]
        rows = []
        for key, label in labels:
            item = kpis[key]
            rows.append({
                "metric": label, "current": item["value"], "previous": item["previous"],
                "change": "—" if item["change"] is None else f'{item["change"]:+}%',
            })
        return rows, [("metric", "Metric"), ("current", "Current period"), ("previous", "Previous comparable period"), ("change", "Change")]

    if family == "trend":
        daily = (last - first).days <= 62
        buckets = defaultdict(lambda: {"sales": ZERO, "returns": ZERO, "expenses": ZERO, "losses": ZERO})
        for doc in docs:
            key = doc.created_at.date().isoformat() if daily else doc.created_at.strftime("%Y-%m")
            if doc.kind == "sale":
                buckets[key]["sales"] += doc.total
            elif doc.kind == "return":
                buckets[key]["returns"] += doc.total
            elif doc.kind == "expense":
                buckets[key]["expenses"] += doc.total
            elif doc.kind == "reversal" and doc.original_id and doc.original.kind == "expense":
                buckets[key]["expenses"] -= doc.total
            elif doc.kind == "inventory_writeoff":
                buckets[key]["losses"] += doc.total
        cost_by_bucket = defaultdict(lambda: ZERO)
        for line in Line.objects.filter(
            document__branch=branch, document__created_at__date__range=(first, last),
            document__kind__in=["sale", "return"],
        ).select_related("document"):
            key = line.document.created_at.date().isoformat() if daily else line.document.created_at.strftime("%Y-%m")
            amount = line.unit_cost * line.quantity * line.factor
            cost_by_bucket[key] += -amount if line.document.kind == "return" else amount
        rows = []
        for key in sorted(buckets):
            net = buckets[key]["sales"] - buckets[key]["returns"]
            cogs = cost_by_bucket[key]
            gross = net - cogs
            operating_before_payroll = gross - buckets[key]["expenses"] - buckets[key]["losses"]
            rows.append({
                "period": key, "net_sales": net, "cogs": cogs, "gross_profit": gross,
                "expenses": buckets[key]["expenses"], "losses": buckets[key]["losses"],
                "contribution": operating_before_payroll,
            })
        return rows, [
            ("period", "Period"), ("net_sales", "Net sales"), ("cogs", "COGS"),
            ("gross_profit", "Gross profit"), ("expenses", "Expenses"),
            ("losses", "Inventory losses"), ("contribution", "Contribution before payroll"),
        ]

    if family == "sales":
        grouped = {}
        for line in limited(Line.objects.filter(
            document__in=docs, document__kind__in=["sale", "return"]
        ).select_related("document", "product"), 100000):
            key = (line.product_id, line.mode)
            row = grouped.setdefault(key, {
                "sku": line.product.sku, "product": line.description,
                "mode": line.mode.replace("_", " "), "units": 0,
                "revenue": ZERO, "cost": ZERO, "profit": ZERO, "margin": ZERO,
            })
            sign = -1 if line.document.kind == "return" else 1
            row["units"] += line.quantity * line.factor * sign
            row["revenue"] += line.total * sign
            row["cost"] += line.unit_cost * line.quantity * line.factor * sign
            row["profit"] = row["revenue"] - row["cost"]
            row["margin"] = (row["profit"] / row["revenue"] * 100).quantize(Decimal("0.1")) if row["revenue"] else ZERO
        rows = _match(list(grouped.values()), query)
        return rows, [
            ("sku", "SKU"), ("product", "Product"), ("mode", "Mode"), ("units", "Net units"),
            ("revenue", "Net revenue"), ("cost", "Standard cost"), ("profit", "Gross profit"), ("margin", "Margin %"),
        ]

    if family == "customers":
        rows = []
        for party in Party.objects.filter(branch=branch, kind="customer").order_by("name"):
            period = docs.filter(party=party)
            sales = _sum(period.filter(kind="sale"))
            returns = _sum(period.filter(kind="return"))
            transactions = period.filter(kind="sale").count()
            outstanding = sum((max(ZERO, balance(doc)) for doc in Document.objects.filter(branch=branch, kind="sale", party=party)), ZERO)
            if sales == 0 and returns == 0 and outstanding == 0:
                continue
            rows.append({
                "customer": party.name, "phone": party.phone, "sales": sales,
                "returns": returns, "net_sales": sales - returns,
                "transactions": transactions, "outstanding": outstanding,
                "average": sales / transactions if transactions else ZERO,
            })
        rows = _match(rows, query)
        return rows, [
            ("customer", "Customer"), ("phone", "Phone"), ("sales", "Sales"),
            ("returns", "Returns"), ("net_sales", "Net sales"), ("transactions", "Sales count"),
            ("average", "Average sale"), ("outstanding", "Current outstanding"),
        ]

    if family == "creditors":
        from . import creditors as creditor_service
        overview = creditor_service.creditors_overview(branch, query, include_settled=True)
        rows = [{
            "creditor": row["party"].name,
            "phone": row["party"].phone,
            "outstanding": row["outstanding"],
            "overdue": row["overdue"],
            "due_7": row["due_7_days"],
            "open_bills": row["bill_count"],
            "next_due": row["next_due"] or "",
            "max_days": row["maximum_days_overdue"],
            "purchases": row["total_purchases"],
            "direct": row["total_direct"],
            "payments": row["total_paid"],
        } for row in overview["rows"] if row["total_billed"] > 0]
        return rows, [
            ("creditor", "Creditor / supplier"), ("phone", "Phone"),
            ("outstanding", "Outstanding"), ("overdue", "Overdue"),
            ("due_7", "Due next 7 days"), ("open_bills", "Open bills"),
            ("next_due", "Next due date"), ("max_days", "Max days overdue"),
            ("purchases", "Purchase value"), ("direct", "Direct bills"),
            ("payments", "Supplier payments"),
        ]

    if family == "expenses":
        expense_docs = docs.filter(kind="expense").select_related("created_by").prefetch_related("payments")
        if category:
            expense_docs = expense_docs.filter(expense_category=category)
        rows = []
        for doc in limited(expense_docs, 20000):
            payment = doc.payments.first()
            if method and (not payment or payment.method != method):
                continue
            rows.append({
                "date": doc.created_at.strftime("%Y-%m-%d %H:%M"), "reference": doc.reference,
                "category": (doc.expense_category or "other").replace("_", " ").title(),
                "description": doc.note, "amount": doc.total,
                "method": payment.get_method_display() if payment else "",
                "staff": doc.created_by.username,
            })
        rows = _match(rows, query)
        return rows, [
            ("date", "Date"), ("reference", "Reference"), ("category", "Category"),
            ("description", "Description"), ("amount", "Amount"),
            ("method", "Payment channel"), ("staff", "Recorded by"),
        ]

    if family == "payments":
        payments = Payment.objects.filter(
            document__branch=branch, document__created_at__date__range=(first, last)
        ).select_related("document", "document__party")
        if method:
            payments = payments.filter(method=method)
        rows = []
        for payment in limited(payments, 30000):
            rows.append({
                "date": payment.document.created_at.strftime("%Y-%m-%d %H:%M"),
                "reference": payment.document.reference,
                "type": payment.document.get_kind_display(),
                "party": payment.document.party.name if payment.document.party else "",
                "method": payment.get_method_display(), "direction": "In" if payment.direction > 0 else "Out",
                "amount": payment.amount, "provider_reference": payment.reference,
            })
        salary_payments = PayrollPayment.objects.filter(
            entry__period__branch=branch, created_at__date__range=(first, last)
        ).select_related("entry__period", "entry__worker")
        if method:
            salary_payments = salary_payments.filter(method=method)
        for payment in salary_payments:
            rows.append({
                "date": payment.created_at.strftime("%Y-%m-%d %H:%M"),
                "reference": f"Payroll {payment.entry.period.label}",
                "type": "Salary payment",
                "party": payment.entry.worker.full_name,
                "method": payment.get_method_display(), "direction": "Out",
                "amount": payment.amount, "provider_reference": payment.reference,
            })
        rows.sort(key=lambda row: row["date"], reverse=True)
        rows = _match(rows, query)
        return rows, [
            ("date", "Date"), ("reference", "Document"), ("type", "Type"), ("party", "Party"),
            ("method", "Channel"), ("direction", "Flow"), ("amount", "Amount"),
            ("provider_reference", "Bank / provider reference"),
        ]

    if family == "inventory":
        balances = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
        held = dict(QuarantineItem.objects.filter(branch=branch, status="held").values("product_id").annotate(total=Sum("quantity")).values_list("product_id", "total"))
        products = limited(Product.objects.filter(pk__in=set(balances) | set(held)).order_by("name"), 10000)
        rows = []
        for product in products:
            sellable = balances.get(product.pk, 0)
            quarantine = held.get(product.pk, 0)
            rows.append({
                "sku": product.sku, "product": product.name, "category": product.category,
                "units": sellable, "sellable": sellable, "quarantine": quarantine, "physical": sellable + quarantine,
                "reorder": product.reorder_level, "status": "Reorder" if sellable <= product.reorder_level else "Healthy",
                "cost": product.cost, "value": product.cost * sellable,
            })
        rows = _match(rows, query)
        return rows, [
            ("sku", "SKU"), ("product", "Product"), ("category", "Category"),
            ("sellable", "Sellable units"), ("quarantine", "Quarantined units"),
            ("physical", "Physical total"), ("reorder", "Reorder level"),
            ("status", "Stock status"), ("cost", "Unit cost"), ("value", "Stock value"),
        ]

    if family == "losses":
        rows = []
        for doc in limited(Document.objects.filter(
            branch=branch, kind="inventory_writeoff",
            created_at__date__range=(first, last),
        ).prefetch_related("lines").select_related("created_by"), 10000):
            line = doc.lines.first()
            rows.append({
                "reference": doc.reference, "date": doc.created_at.strftime("%Y-%m-%d %H:%M"),
                "product": line.description if line else "", "units": line.quantity if line else 0,
                "cost": line.unit_cost if line else ZERO, "value": doc.total,
                "staff": doc.created_by.username, "reason": doc.note,
            })
        return _match(rows, query), [
            ("reference", "Reference"), ("date", "Date"), ("product", "Product"), ("units", "Units"),
            ("cost", "Unit cost"), ("value", "Loss value"), ("staff", "Recorded by"), ("reason", "Reason"),
        ]

    if family == "aging":
        rows = []
        today = timezone.localdate()
        for doc in limited(Document.objects.filter(branch=branch, kind="sale", party__isnull=False).select_related("party"), 10000):
            amount = balance(doc)
            if amount <= 0:
                continue
            overdue = max(0, (today - doc.due_date).days) if doc.due_date else 0
            bucket = "Current" if overdue == 0 else "1-30" if overdue <= 30 else "31-60" if overdue <= 60 else "61-90" if overdue <= 90 else "90+"
            rows.append({
                "reference": doc.reference, "customer": doc.party.name, "phone": doc.party.phone,
                "due": str(doc.due_date or ""), "days": overdue, "bucket": bucket, "balance": amount,
            })
        return _match(rows, query), [
            ("reference", "Invoice"), ("customer", "Customer"), ("phone", "Phone"),
            ("due", "Due date"), ("days", "Days overdue"), ("bucket", "Aging bucket"), ("balance", "Outstanding"),
        ]

    if family == "workforce":
        workers = Worker.objects.filter(branch=branch)
        rows = [{
            "employee": worker.employee_code, "name": worker.full_name,
            "department": worker.department, "job_title": worker.job_title,
            "employment": worker.get_employment_type_display(), "status": worker.get_status_display(),
            "hire_date": worker.hire_date, "phone": worker.phone,
            "base_salary": worker.base_salary, "allowance": worker.recurring_allowance,
        } for worker in workers]
        return _match(rows, query), [
            ("employee", "Employee ID"), ("name", "Worker"), ("department", "Department"),
            ("job_title", "Job title"), ("employment", "Employment type"), ("status", "Status"),
            ("hire_date", "Hire date"), ("phone", "Phone"), ("base_salary", "Base salary"),
            ("allowance", "Recurring allowance"),
        ]

    if family == "payroll":
        entries = PayrollEntry.objects.filter(
            period__branch=branch, period__end_date__range=(first, last)
        ).select_related("period", "worker")
        rows = [{
            "period": entry.period.label, "employee": entry.worker.employee_code,
            "worker": entry.worker.full_name, "department": entry.worker.department,
            "gross": entry.gross_pay, "employee_ssnit": entry.ssnit_employee,
            "tax": entry.paye_tax + entry.bonus_tax + entry.overtime_tax,
            "net": entry.net_pay, "paid": entry.paid_amount, "balance": entry.balance,
            "status": entry.period.get_status_display(), "flags": "; ".join(entry.validation_flags or []),
        } for entry in entries]
        return _match(rows, query), [
            ("period", "Payroll period"), ("employee", "Employee ID"), ("worker", "Worker"),
            ("department", "Department"), ("gross", "Gross pay"), ("employee_ssnit", "Employee SSNIT"),
            ("tax", "Tax"), ("net", "Net pay"), ("paid", "Paid"), ("balance", "Outstanding"),
            ("status", "Period status"), ("flags", "Validation flags"),
        ]

    rows = [{
        "reference": doc.reference, "date": doc.created_at.strftime("%Y-%m-%d %H:%M"),
        "kind": doc.get_kind_display(), "party": doc.party.name if doc.party else "Walk-in",
        "total": -doc.total if doc.kind in ("return", "supplier_return", "reversal") else doc.total,
        "paid": doc.paid,
        "balance": balance(doc) if doc.kind in ("sale", "purchase") else ZERO,
    } for doc in limited(docs.select_related("party"), 10000)]
    return _match(rows, query), REGISTER


def branch_comparison(user, first, last):
    if not user.is_active or not user.has_perm("core.view_reports"):
        raise PermissionDenied("Reporting permission is required.")
    branches = Branch.objects.filter(active=True).order_by("name", "pk")
    if not user.is_superuser:
        branches = branches.filter(access__user=user)
    rows = []
    for branch in branches:
        metrics = _period_metrics(branch, first, last)
        stock = sum((row.quantity * row.product.cost for row in Stock.objects.filter(branch=branch).select_related("product")), ZERO)
        receivables = sum((max(ZERO, balance(doc)) for doc in Document.objects.filter(branch=branch, kind="sale", party__isnull=False)), ZERO)
        payables = sum((max(ZERO, balance(doc)) for doc in Document.objects.filter(branch=branch, kind__in=["purchase", "creditor_charge"], party__isnull=False)), ZERO)
        rows.append({
            "branch": branch.name, "sales": metrics["net_sales"], "cost": metrics["cogs"],
            "profit": metrics["gross_profit"], "expenses": metrics["expenses"],
            "payroll": metrics["payroll_cost"], "operating": metrics["operating_result"],
            "losses": metrics["losses"], "stock": stock, "receivables": receivables, "payables": payables,
        })
    columns = [
        ("branch", "Branch"), ("sales", "Net sales"), ("cost", "COGS"),
        ("profit", "Gross profit"), ("expenses", "Expenses"), ("payroll", "Payroll cost"),
        ("operating", "Operating result"), ("losses", "Inventory losses"), ("stock", "Current stock"),
        ("receivables", "Current receivables"), ("payables", "Current payables"),
    ]
    return rows, columns
