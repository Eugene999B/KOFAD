from datetime import date
from decimal import Decimal

from django.core.paginator import Paginator

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone

from . import accounting_engine as engine
from . import services as s
from .context import shell
from .exports import export
from .models import ManualJournal, Payment
from .views import problem, protected


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


def _filtered_ledger(request, rows):
    query = request.GET.get("q", "").strip()[:100].casefold()
    account = request.GET.get("account", "").strip()[:20]
    source = request.GET.get("source", "").strip()[:80]
    return [
        row for row in rows
        if (not account or row["account_code"] == account)
        and (not source or row["source"] == source)
        and (not query or query in " ".join(str(row[key]) for key in
             ("reference", "description", "account", "account_code", "source")).casefold())
    ]


def _journal_lines(request):
    rows = []
    for index in range(1, 9):
        code = request.POST.get(f"account_{index}", "").strip()
        debit = request.POST.get(f"debit_{index}", "").strip()
        credit = request.POST.get(f"credit_{index}", "").strip()
        description = request.POST.get(f"description_{index}", "").strip()
        if not code and not debit and not credit and not description:
            continue
        rows.append({
            "account_code": code, "debit": debit or "0", "credit": credit or "0",
            "description": description,
        })
    return rows


@protected("view_reports|operate_finance|manage_company")
def accounting(request, branch):
    try:
        first, last, start, end = _range(request)
    except ValidationError as exc:
        messages.error(request, problem(exc))
        return redirect("/accounting/")
    view = request.GET.get("view", "overview")
    if view not in {"overview", "trial", "ledger", "pnl", "balance", "cashflow", "journals", "integrity"}:
        view = "overview"

    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "journal":
                journal = engine.create_manual_journal(
                    request.user, branch, request.POST.get("journal_date"),
                    request.POST.get("memo"), _journal_lines(request),
                )
                if journal.status == "posted":
                    messages.success(request, f"Journal {journal.reference} posted under owner authority.")
                else:
                    messages.success(request, f"Journal {journal.reference} sent to the Approval Center.")
                return redirect("/accounting/?view=journals")
            raise ValidationError("Choose a valid accounting action.")
        except (ValidationError, PermissionDenied, ValueError) as exc:
            messages.error(request, problem(exc))

    report = engine.statements(branch, first, last)
    ledger_rows = engine.ledger(branch, first, last)
    q = request.GET.get("q", "").strip()[:100]
    account = request.GET.get("account", "").strip()[:20]
    source = request.GET.get("source", "").strip()[:80]
    sources = sorted({row["source"] for row in ledger_rows})
    ledger_rows = _filtered_ledger(request, ledger_rows)
    ledger_debits = sum((row["debit"] for row in ledger_rows), Decimal("0"))
    ledger_credits = sum((row["credit"] for row in ledger_rows), Decimal("0"))
    page_obj = Paginator(ledger_rows, 100).get_page(request.GET.get("page"))
    parameters = request.GET.copy()
    parameters.pop("page", None)

    pnl_rows = []
    balance_rows = report["balance_sheet"]
    for row in report["trial_balance"]:
        typ = row["type"]
        signed = row["debit"] - row["credit"] if typ in {"asset", "expense", "contra_revenue"} else row["credit"] - row["debit"]
        if typ in {"revenue", "contra_revenue", "expense"}:
            pnl_rows.append({**row, "statement_balance": signed})

    journals = ManualJournal.objects.filter(branch=branch).select_related(
        "requested_by", "reviewed_by"
    ).prefetch_related("lines")[:100]

    cumulative = {row["code"]: row for row in report["cumulative_trial_balance"]}
    period = {row["code"]: row for row in report["trial_balance"]}

    def debit_balance(code):
        row = cumulative.get(code)
        return (row["debit"] - row["credit"]) if row else 0

    def credit_balance(code):
        row = cumulative.get(code)
        return (row["credit"] - row["debit"]) if row else 0

    def period_debit(code):
        row = period.get(code)
        return (row["debit"] - row["credit"]) if row else 0

    cash_equivalents = sum((debit_balance(code) for code in ("1000", "1010", "1020", "1030")), 0)
    receivables = debit_balance("1100")
    inventory_assets = debit_balance("1200") + debit_balance("1210")
    trade_payables = credit_balance("2000")
    cogs = period_debit("5000")
    gross_profit = report["revenue"] - cogs
    gross_margin = (gross_profit * 100 / report["revenue"]) if report["revenue"] else 0
    operating_expenses = report["expenses"] - cogs
    control_checks = []
    for code, label in (
        ("inventory", "Inventory"),
        ("receivables", "Customer receivables"),
        ("payables", "Supplier payables"),
    ):
        control = (report.get("subledger_controls") or {}).get(code)
        if control:
            control_checks.append({
                "code": code,
                "label": label,
                "operational": control["operational"],
                "ledger": control["ledger"],
                "difference": control["difference"],
                "ok": control["difference"] == 0,
            })
    control_ok_count = sum(1 for item in control_checks if item["ok"])
    equation_ok = report["balance_check"] == 0
    integrity_rows, integrity_summary = [], {}
    integrity_page = None
    if view == "integrity":
        from .finance_integrity import financial_controls
        checks, integrity_summary = financial_controls(branch, first, last)
        integrity_rows = [row for row in checks if row["status"] != "OK"]
        integrity_page = Paginator(integrity_rows, 80).get_page(request.GET.get("page"))

    return render(request, "accounting.html", {
        "title": "Accounting Intelligence",
        "start": start, "end": end, "first": first, "last": last,
        "view": view, "report": report, "ledger_rows": page_obj.object_list,
        "ledger_page": page_obj, "ledger_query": parameters.urlencode(),
        "ledger_debits": ledger_debits, "ledger_credits": ledger_credits,
        "pnl_rows": pnl_rows, "balance_rows": balance_rows,
        "accounts": sorted(engine.ACCOUNTS.items()),
        "selected_account": account, "selected_source": source, "sources": sources, "q": q,
        "journals": journals, "today": timezone.localdate(),
        "cash_equivalents": cash_equivalents,
        "receivables": receivables,
        "inventory_assets": inventory_assets,
        "trade_payables": trade_payables,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "gross_margin": gross_margin,
        "operating_expenses": operating_expenses,
        "control_checks": control_checks,
        "control_ok_count": control_ok_count,
        "equation_ok": equation_ok,
        "integrity_summary": integrity_summary,
        "integrity_page": integrity_page,
        "integrity_count": len(integrity_rows),
        "integrity_checked": integrity_summary.get("Checks completed", 0),
        "can_journal": request.user.has_perm("core.operate_finance") or request.user.has_perm("core.manage_company") or request.user.is_superuser,
        "owner_direct": request.user.has_perm("core.manage_company") or request.user.is_superuser,
    })


@protected("view_reports|operate_finance|manage_company")
def accounting_export(request, branch, format):
    try:
        first, last, start, end = _range(request)
    except ValidationError as exc:
        messages.error(request, problem(exc))
        return redirect("/accounting/")
    view = request.GET.get("view", "trial")
    report = engine.statements(branch, first, last)

    if view == "integrity":
        from .finance_integrity import financial_controls, COLUMNS
        rows, _ = financial_controls(branch, first, last)
        columns = COLUMNS
        title, sheet = "Financial integrity reconciliation", "Finance Integrity"
    elif view == "ledger":
        rows = _filtered_ledger(request, engine.ledger(branch, first, last))
        columns = [
            ("date", "Date"), ("reference", "Reference"), ("source", "Source"),
            ("description", "Description"), ("account_code", "Account code"),
            ("account", "Account"), ("debit", "Debit"), ("credit", "Credit"),
        ]
        title, sheet = "General ledger", "General Ledger"
    elif view == "pnl":
        rows = []
        for row in report["trial_balance"]:
            if row["type"] in {"revenue", "contra_revenue", "expense"}:
                normal = row["credit"] - row["debit"] if row["type"] == "revenue" else row["debit"] - row["credit"]
                rows.append({"code": row["code"], "account": row["name"], "type": row["type"], "amount": normal})
        columns = [("code", "Account"), ("account", "Name"), ("type", "Class"), ("amount", "Amount")]
        title, sheet = "Profit and loss", "Profit & Loss"
    elif view == "balance":
        rows = [
            {"code": row["code"], "account": row["name"], "type": row["type"], "amount": row["statement_balance"]}
            for row in report["balance_sheet"]
        ]
        rows.append({"code": "", "account": "Accumulated operating result through reporting date", "type": "equity", "amount": report["accumulated_result"]})
        columns = [("code", "Account"), ("account", "Name"), ("type", "Class"), ("amount", "Amount")]
        title, sheet = "Statement of financial position", "Balance Sheet"
    elif view == "cashflow":
        rows = [
            {"section": "Operating activities", "amount": report["cash_flow"]["operating"]},
            {"section": "Investing activities", "amount": report["cash_flow"]["investing"]},
            {"section": "Financing activities", "amount": report["cash_flow"]["financing"]},
            {"section": "Net change in cash & equivalents", "amount": report["cash_flow"]["net_change"]},
        ]
        columns = [("section", "Cash-flow section"), ("amount", "Amount")]
        title, sheet = "Cash-flow statement", "Cash Flow"
    else:
        rows = report["trial_balance"]
        columns = [
            ("code", "Account code"), ("name", "Account"), ("type", "Class"),
            ("debit", "Debits"), ("credit", "Credits"), ("balance", "Debit balance"),
        ]
        title, sheet = "Trial balance", "Trial Balance"

    s.audit(request.user, branch, "accounting.exported", format, {
        "view": view, "start": start, "end": end,
    }, category="accounting", entity_type="accounting_export")
    return export(
        rows, format, f"{title} · {start} to {end}", shell(request)["company"], columns,
        filename=f"kofad-{view}-{start}-{end}", sheet_name=sheet[:31],
        metadata={"Location": branch.name, "From": first, "To": last, "Accounting view": title,
                  "Account filter": request.GET.get("account", "")[:20] or "All",
                  "Source filter": request.GET.get("source", "")[:80] or "All",
                  "Search": request.GET.get("q", "")[:100] or "None"},
        summary={
            "Revenue": report["revenue"], "Expenses": report["expenses"], "Profit / (loss)": report["profit"],
            "Assets": report["assets"], "Liabilities": report["liabilities"], "Equity": report["equity"],
        },
        notes=[
            "KOFAD derives this double-entry management ledger from controlled operational source records and approved manual journals.",
            "The statements are IFRS-informed management information. Statutory reporting still requires the entity's accounting policies, period-end adjustments, disclosures and professional review.",
            f"Balance equation check: {report['balance_check']}. A non-zero value requires accounting review.",
            "Financial integrity checks identify source mismatches and closing variances; they do not alter any historical financial record.",
            "The payment ledger is not a substitute for independent Paystack, Hubtel, MoMo or bank settlement statements.",
        ],
    )
