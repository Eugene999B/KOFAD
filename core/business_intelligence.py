"""Decision-oriented business intelligence for KOFAD.

This layer sits above transactional reports. It compares periods, measures working
capital and cash conversion, detects operating exceptions, and produces explicit
management actions from evidence already recorded in KOFAD.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Max, Sum
from django.utils import timezone

from . import creditors as creditor_service
from .accounting_engine import statements
from .models import (
    CustomerReturnRequest, Document, Line, PayrollEntry, Product, Stock,
)
from .services import balance

ZERO = Decimal("0.00")


def _sum(qs, field="total"):
    return qs.aggregate(value=Sum(field))["value"] or ZERO


def _pct(numerator, denominator):
    return (Decimal(numerator) / Decimal(denominator) * 100).quantize(Decimal("0.1")) if denominator else ZERO


def _period(branch, first, last):
    docs = Document.objects.filter(branch=branch, created_at__date__range=(first, last))
    sales = _sum(docs.filter(kind="sale"))
    returns = _sum(docs.filter(kind="return"))
    net_sales = sales - returns

    cogs = ZERO
    sale_lines = Line.objects.filter(
        document__branch=branch,
        document__created_at__date__range=(first, last),
        document__kind__in=["sale", "return"],
    ).select_related("document")
    for line in sale_lines:
        value = line.unit_cost * line.quantity * line.factor
        cogs += -value if line.document.kind == "return" else value
    gross_profit = net_sales - cogs

    expenses = _sum(docs.filter(kind="expense")) - _sum(docs.filter(kind="reversal", original__kind="expense"))
    creditor_expense_categories = {
        "transport", "fuel", "utilities", "rent", "maintenance",
        "professional", "tax", "staff", "other",
    }
    expenses += _sum(Document.objects.filter(
        branch=branch, kind="creditor_charge", document_date__range=(first, last),
        payable_category__in=creditor_expense_categories,
    ).exclude(correction__status="approved"))

    losses = _sum(docs.filter(kind="inventory_writeoff"))
    payroll = PayrollEntry.objects.filter(
        period__branch=branch, period__end_date__range=(first, last),
        period__status__in=["locked", "reconciled"],
    ).aggregate(gross=Sum("gross_pay"), employer=Sum("employer_pension"))
    payroll_cost = (payroll["gross"] or ZERO) + (payroll["employer"] or ZERO)
    operating = gross_profit - expenses - losses - payroll_cost

    collections = _sum(docs.filter(kind="collection").exclude(correction__status="approved"))
    sale_cash = docs.filter(kind="sale").aggregate(value=Sum("paid"))["value"] or ZERO
    cash_collected = sale_cash + collections

    purchase_value = _sum(docs.filter(kind="purchase"))
    supplier_payments = _sum(docs.filter(kind="supplier_payment").exclude(correction__status="approved"))
    supplier_return_value = _sum(docs.filter(kind="supplier_return"))

    return {
        "sales": sales, "returns": returns, "net_sales": net_sales,
        "cogs": cogs, "gross_profit": gross_profit, "gross_margin": _pct(gross_profit, net_sales),
        "expenses": expenses, "losses": losses, "payroll_cost": payroll_cost,
        "operating_result": operating, "operating_margin": _pct(operating, net_sales),
        "sale_count": docs.filter(kind="sale").count(),
        "return_count": docs.filter(kind="return").count(),
        "return_rate": _pct(returns, sales),
        "cash_collected": cash_collected,
        "collection_rate": _pct(cash_collected, sales),
        "purchase_value": purchase_value,
        "supplier_payments": supplier_payments,
        "supplier_returns": supplier_return_value,
    }


def _change(current, prior):
    if prior == 0:
        return None
    return ((current - prior) / abs(prior) * 100).quantize(Decimal("0.1"))


def _aging_receivables(branch):
    today = timezone.localdate()
    buckets = {"current": ZERO, "days_1_30": ZERO, "days_31_60": ZERO, "days_61_90": ZERO, "days_90_plus": ZERO}
    total = ZERO
    overdue = ZERO
    rows = []
    for doc in Document.objects.filter(branch=branch, kind="sale", party__isnull=False).select_related("party"):
        outstanding = max(ZERO, balance(doc))
        if not outstanding:
            continue
        total += outstanding
        due = doc.due_date or timezone.localtime(doc.created_at).date()
        days = max((today - due).days, 0)
        if days:
            overdue += outstanding
        key = "current" if days == 0 else "days_1_30" if days <= 30 else "days_31_60" if days <= 60 else "days_61_90" if days <= 90 else "days_90_plus"
        buckets[key] += outstanding
        rows.append({
            "customer": doc.party.name, "reference": doc.reference,
            "outstanding": outstanding, "days_overdue": days, "due": due,
        })
    rows.sort(key=lambda x: (x["days_overdue"], x["outstanding"]), reverse=True)
    return {"total": total, "overdue": overdue, "buckets": buckets, "top": rows[:8]}


def _inventory_intelligence(branch, first, last):
    stock_rows = list(Stock.objects.filter(branch=branch).select_related("product"))
    stock_value = sum((row.quantity * row.product.cost for row in stock_rows), ZERO)
    low = [row for row in stock_rows if row.quantity <= row.product.reorder_level]
    out = [row for row in stock_rows if row.quantity == 0]

    sold = defaultdict(int)
    revenue = defaultdict(lambda: ZERO)
    profit = defaultdict(lambda: ZERO)
    latest_sale = {}
    sale_lines = Line.objects.filter(
        document__branch=branch, document__kind="sale",
        document__created_at__date__range=(first, last),
    ).select_related("product", "document")
    for line in sale_lines:
        units = line.quantity * line.factor
        sold[line.product_id] += units
        revenue[line.product_id] += line.total
        profit[line.product_id] += line.total - (line.unit_cost * units)
        latest_sale[line.product_id] = max(latest_sale.get(line.product_id, line.document.created_at), line.document.created_at)

    top_products = []
    for pid, value in revenue.items():
        product = next((row.product for row in stock_rows if row.product_id == pid), None)
        if product:
            top_products.append({
                "product": product.name, "sku": product.sku, "revenue": value,
                "profit": profit[pid], "units": sold[pid],
            })
    top_products.sort(key=lambda x: x["revenue"], reverse=True)

    cutoff = timezone.now() - timedelta(days=90)
    last_ever = {
        row["product_id"]: row["last"]
        for row in Line.objects.filter(document__branch=branch, document__kind="sale")
        .values("product_id").annotate(last=Max("document__created_at"))
    }
    slow = []
    for row in stock_rows:
        if row.quantity <= 0:
            continue
        last = last_ever.get(row.product_id)
        if not last or last < cutoff:
            slow.append({
                "product": row.product.name, "sku": row.product.sku,
                "quantity": row.quantity, "value": row.quantity * row.product.cost,
                "last_sale": last,
            })
    slow.sort(key=lambda x: x["value"], reverse=True)

    return {
        "stock_value": stock_value, "low_count": len(low), "out_count": len(out),
        "slow_value": sum((row["value"] for row in slow), ZERO),
        "slow": slow[:8], "top_products": top_products[:8],
    }


def _customer_intelligence(branch, first, last):
    sales = Document.objects.filter(
        branch=branch, kind="sale", created_at__date__range=(first, last)
    ).select_related("party")
    grouped = defaultdict(lambda: {"sales": ZERO, "count": 0})
    for doc in sales:
        key = doc.party.name if doc.party else "Walk-in customers"
        grouped[key]["sales"] += doc.total
        grouped[key]["count"] += 1
    rows = [{"customer": name, **data} for name, data in grouped.items()]
    rows.sort(key=lambda x: x["sales"], reverse=True)
    total = sum((x["sales"] for x in rows), ZERO)
    top5 = sum((x["sales"] for x in rows[:5]), ZERO)
    return {"top": rows[:8], "top5_concentration": _pct(top5, total)}


def _trend(branch, last, months=6):
    rows = []
    cursor = last.replace(day=1)
    for _ in range(months):
        month_last = min(last, (cursor.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))
        data = _period(branch, cursor, month_last)
        rows.append({"month": cursor.strftime("%b %Y"), **data})
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    rows.reverse()
    max_value = max([abs(x["net_sales"]) for x in rows] + [Decimal("1")])
    for row in rows:
        row["sales_width"] = int(abs(row["net_sales"]) / max_value * 100)
    return rows


def _actions(current, prior, receivables, payables, inventory, accounting, pending_approvals):
    actions = []
    def add(severity, title, evidence, action):
        actions.append({"severity": severity, "title": title, "evidence": evidence, "action": action})

    if current["collection_rate"] < 75 and current["sales"] > 0:
        add("high", "Cash conversion is weak",
            f"Only {current['collection_rate']}% of gross sales value was collected in the period.",
            "Prioritise outstanding-customer follow-up and tighten credit terms on weak accounts.")
    if receivables["overdue"] > 0:
        ratio = _pct(receivables["overdue"], receivables["total"])
        add("high" if ratio >= 40 else "medium", "Overdue receivables are tying up working capital",
            f"{ratio}% of current receivables are overdue.",
            "Work the oldest debt buckets first and assign collection owners.")
    if payables["overdue"] > 0:
        ratio = _pct(payables["overdue"], payables["total_payables"])
        add("high" if ratio >= 40 else "medium", "Supplier obligations require attention",
            f"{ratio}% of current supplier payables are overdue.",
            "Protect key supplier relationships and schedule overdue settlements by criticality.")
    if current["gross_margin"] < 15 and current["net_sales"] > 0:
        add("high", "Gross margin is thin",
            f"Gross margin is {current['gross_margin']}%.",
            "Review product pricing, discounting, purchase cost and low-margin product mix.")
    if prior["gross_margin"] and current["gross_margin"] < prior["gross_margin"] - Decimal("5"):
        add("medium", "Margin deteriorated versus the prior period",
            f"Gross margin moved from {prior['gross_margin']}% to {current['gross_margin']}%.",
            "Drill into product margin and purchase-cost movements before adjusting prices.")
    if current["return_rate"] >= 5:
        add("medium", "Customer return value is elevated",
            f"Returns equal {current['return_rate']}% of gross sales.",
            "Review return reasons by product and distinguish quality, fulfilment and customer-choice causes.")
    if inventory["slow_value"] > 0 and inventory["stock_value"]:
        ratio = _pct(inventory["slow_value"], inventory["stock_value"])
        add("high" if ratio >= 30 else "medium", "Capital is trapped in slow-moving stock",
            f"About {ratio}% of stock cost value has no sale in the last 90 days.",
            "Prioritise sell-through, purchasing restraint and supplier-return opportunities for slow lines.")
    if inventory["out_count"]:
        add("medium", "Stock-outs may be suppressing revenue",
            f"{inventory['out_count']} active product(s) currently have zero sellable stock.",
            "Replenish proven fast movers first; do not indiscriminately restock slow lines.")
    if current["losses"] > 0:
        add("high", "Inventory losses were recorded",
            f"Inventory write-offs total {current['losses']:.2f}.",
            "Trace loss documents, causes and responsible controls before the next inventory verification.")
    if pending_approvals >= 5:
        add("medium", "Approval backlog is growing",
            f"{pending_approvals} controlled requests are waiting.",
            "Clear high-value and time-sensitive approvals to avoid operational delay.")
    if accounting["balance_check"] != 0:
        add("critical", "Accounting equation is out of balance",
            f"Assets less liabilities and equity differ by {accounting['balance_check']:.2f}.",
            "Stop relying on the statement view until the ledger imbalance is identified and corrected.")
    inventory_control = accounting.get("inventory_control")
    if inventory_control and inventory_control["difference"] != 0:
        add("high", "Inventory subledger does not reconcile to operational stock",
            f"Operational inventory and ledger inventory differ by {inventory_control['difference']:.2f}.",
            "Review opening inventory and historical adjustments; post a controlled opening-balance/manual journal where supported by evidence.")
    if current["operating_result"] < 0:
        add("critical", "The selected period produced an operating loss",
            f"Operating result is {current['operating_result']:.2f}.",
            "Separate margin, payroll, operating-expense and loss drivers and assign immediate corrective actions.")

    priority = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actions.sort(key=lambda x: priority[x["severity"]])
    return actions[:10]


def intelligence(branch, first, last, pending_approvals=0):
    current = _period(branch, first, last)
    days = (last - first).days + 1
    prior_last = first - timedelta(days=1)
    prior_first = prior_last - timedelta(days=days - 1)
    prior = _period(branch, prior_first, prior_last)

    comparisons = {
        key: _change(current[key], prior[key])
        for key in ("net_sales", "gross_profit", "expenses", "payroll_cost", "operating_result", "cash_collected")
    }
    receivables = _aging_receivables(branch)
    payables_raw = creditor_service.creditors_overview(branch, include_settled=False)
    payables = {
        "total_payables": payables_raw["total_payables"],
        "overdue": payables_raw["overdue"],
        "aging": payables_raw["aging"],
        "top": payables_raw["rows"][:8],
    }
    inventory = _inventory_intelligence(branch, first, last)
    customers = _customer_intelligence(branch, first, last)
    accounting = statements(branch, first, last)

    dso = (receivables["total"] / current["net_sales"] * days).quantize(Decimal("0.1")) if current["net_sales"] > 0 else ZERO
    dpo = (payables["total_payables"] / current["purchase_value"] * days).quantize(Decimal("0.1")) if current["purchase_value"] > 0 else ZERO
    working_capital = receivables["total"] + inventory["stock_value"] - payables["total_payables"]

    actions = _actions(current, prior, receivables, payables, inventory, accounting, pending_approvals)
    risk_weight = sum({"critical": 28, "high": 14, "medium": 6, "low": 0}[a["severity"]] for a in actions)
    health_score = max(0, 100 - risk_weight)
    health_label = "Strong" if health_score >= 85 else "Watch closely" if health_score >= 65 else "Immediate attention"

    narrative = []
    if current["net_sales"] > prior["net_sales"]:
        narrative.append(f"Net sales improved {(_change(current['net_sales'], prior['net_sales']) or ZERO)}% versus the prior comparable period.")
    elif prior["net_sales"] > 0:
        narrative.append(f"Net sales declined {abs(_change(current['net_sales'], prior['net_sales']) or ZERO)}% versus the prior comparable period.")
    narrative.append(f"Gross margin is {current['gross_margin']}% and operating margin is {current['operating_margin']}%.")
    narrative.append(f"Working capital tied in receivables and inventory, net of trade payables, is {working_capital:.2f}.")
    if actions:
        narrative.append(f"The highest-priority signal is: {actions[0]['title']}. {actions[0]['action']}")
    else:
        narrative.append("No high-priority exception is currently detected; maintain daily reconciliation and review trends.")

    return {
        "current": current, "prior": prior, "comparisons": comparisons,
        "prior_first": prior_first, "prior_last": prior_last,
        "receivables": receivables, "payables": payables, "inventory": inventory,
        "customers": customers, "accounting": accounting,
        "working_capital": working_capital, "dso": dso, "dpo": dpo,
        "pending_approvals": pending_approvals,
        "actions": actions, "health_score": health_score, "health_label": health_label,
        "narrative": narrative, "trend": _trend(branch, last, 6),
    }
