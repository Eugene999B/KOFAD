from datetime import date

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


@protected("view_reports|operate_finance")
def accounting(request, branch):
    first, last, start, end = _range(request)
    view = request.GET.get("view", "overview")
    if view not in {"overview", "trial", "ledger", "pnl", "balance", "cashflow", "journals"}:
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
    if q:
        lowered = q.casefold()
        ledger_rows = [
            row for row in ledger_rows
            if lowered in " ".join([
                str(row["reference"]), str(row["description"]), str(row["account"]),
                str(row["account_code"]), str(row["source"]),
            ]).casefold()
        ]
    if account:
        ledger_rows = [row for row in ledger_rows if row["account_code"] == account]
    if source:
        ledger_rows = [row for row in ledger_rows if row["source"] == source]

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
    sources = sorted({row["source"] for row in engine.ledger(branch, first, last)})
    return render(request, "accounting.html", {
        "title": "Accounting Intelligence",
        "start": start, "end": end, "first": first, "last": last,
        "view": view, "report": report, "ledger_rows": ledger_rows[:2000],
        "pnl_rows": pnl_rows, "balance_rows": balance_rows,
        "accounts": sorted(engine.ACCOUNTS.items()),
        "selected_account": account, "selected_source": source, "sources": sources, "q": q,
        "journals": journals, "today": timezone.localdate(),
        "can_journal": request.user.has_perm("core.operate_finance") or request.user.has_perm("core.manage_company") or request.user.is_superuser,
        "owner_direct": request.user.has_perm("core.manage_company") or request.user.is_superuser,
    })


@protected("view_reports|operate_finance")
def accounting_export(request, branch, format):
    first, last, start, end = _range(request)
    view = request.GET.get("view", "trial")
    report = engine.statements(branch, first, last)

    if view == "ledger":
        rows = engine.ledger(branch, first, last)
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
        metadata={"Location": branch.name, "From": first, "To": last, "Accounting view": title},
        summary={
            "Revenue": report["revenue"], "Expenses": report["expenses"], "Profit / (loss)": report["profit"],
            "Assets": report["assets"], "Liabilities": report["liabilities"], "Equity": report["equity"],
        },
        notes=[
            "KOFAD derives this double-entry management ledger from controlled operational source records and approved manual journals.",
            "The statements are IFRS-informed management information. Statutory reporting still requires the entity's accounting policies, period-end adjustments, disclosures and professional review.",
            f"Balance equation check: {report['balance_check']}. A non-zero value requires accounting review.",
        ],
    )
