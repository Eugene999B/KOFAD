"""Double-entry accounting intelligence derived from KOFAD source records plus controlled manual journals."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import services as s
from .models import (
    Allocation, CustomerReturnRequest, Document, ManualJournal, ManualJournalLine,
    Payment, PayrollEntry, PayrollPayment,
)

ZERO = Decimal("0.00")

ACCOUNTS = {
    "1000": ("Cash on hand", "asset"), "1010": ("Mobile Money", "asset"),
    "1020": ("Bank", "asset"), "1030": ("Card clearing", "asset"),
    "1100": ("Trade receivables", "asset"), "1200": ("Inventory - sellable", "asset"),
    "1210": ("Inventory - quarantine", "asset"), "1300": ("Prepayments / other current assets", "asset"),
    "1500": ("Property, plant & equipment", "asset"), "1510": ("Accumulated depreciation", "contra_asset"),
    "2000": ("Trade payables", "liability"), "2100": ("Payroll payable", "liability"),
    "2110": ("PAYE / payroll tax payable", "liability"), "2120": ("Pension / SSNIT payable", "liability"),
    "2130": ("Other payroll deductions payable", "liability"), "2200": ("Tax payable", "liability"),
    "2300": ("Loans / borrowings", "liability"), "2400": ("Other liabilities", "liability"),
    "3000": ("Owner capital", "equity"), "3100": ("Retained earnings", "equity"),
    "4000": ("Sales revenue", "revenue"), "4010": ("Sales returns", "contra_revenue"),
    "4100": ("Other income", "revenue"), "5000": ("Cost of goods sold", "expense"),
    "6000": ("Transport expense", "expense"), "6010": ("Fuel expense", "expense"),
    "6020": ("Utilities expense", "expense"), "6030": ("Rent expense", "expense"),
    "6040": ("Repairs & maintenance", "expense"), "6050": ("Marketing expense", "expense"),
    "6060": ("Office expense", "expense"), "6070": ("Security expense", "expense"),
    "6080": ("Professional fees", "expense"), "6090": ("Tax expense", "expense"),
    "6100": ("Payroll expense", "expense"), "6110": ("Employer pension expense", "expense"),
    "6200": ("Inventory loss / write-off", "expense"), "6300": ("Depreciation expense", "expense"),
    "6400": ("Interest / finance cost", "expense"), "6990": ("Other operating expense", "expense"),
}
PAYMENT_ACCOUNT = {"cash": "1000", "momo": "1010", "bank": "1020", "card": "1030"}
EXPENSE_ACCOUNT = {
    "transport": "6000", "fuel": "6010", "utilities": "6020", "rent": "6030",
    "maintenance": "6040", "marketing": "6050", "office": "6060", "security": "6070",
    "professional": "6080", "tax": "6090", "salary": "6100", "staff": "6100", "other": "6990",
}


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _row(rows, when, reference, source, description, code, debit=ZERO, credit=ZERO):
    debit, credit = _money(debit), _money(credit)
    if debit == 0 and credit == 0:
        return
    name, account_type = ACCOUNTS.get(code, (f"Unmapped account {code}", "other"))
    rows.append({
        "date": when, "reference": reference, "source": source, "description": description,
        "account_code": code, "account": name, "account_type": account_type,
        "debit": debit, "credit": credit,
    })


def _doc_date(doc):
    return doc.document_date or timezone.localtime(doc.created_at).date()


def _payments_by_account(doc):
    totals = defaultdict(lambda: ZERO)
    for payment in doc.payments.all():
        totals[PAYMENT_ACCOUNT.get(payment.method, "1000")] += _money(payment.amount)
    return totals


def _document_entries(branch, first, last):
    rows = []
    docs = Document.objects.filter(branch=branch).select_related("party", "original").prefetch_related(
        "payments", "lines", "lines__product", "allocations", "settlements"
    )
    for doc in docs:
        day = _doc_date(doc)
        if day < first or day > last:
            continue
        ref, note = doc.reference, doc.note or doc.get_kind_display()
        cash = _payments_by_account(doc)

        if doc.kind == "sale":
            for code, amount in cash.items():
                _row(rows, day, ref, "Sale", note, code, debit=amount)
            receivable = _money(doc.total) - sum(cash.values(), ZERO)
            _row(rows, day, ref, "Sale", note, "1100", debit=receivable)
            _row(rows, day, ref, "Sale", note, "4000", credit=doc.total)
            cost = sum((_money(line.unit_cost) * line.quantity * line.factor for line in doc.lines.all()), ZERO)
            _row(rows, day, ref, "Sale", "Cost of inventory sold", "5000", debit=cost)
            _row(rows, day, ref, "Sale", "Inventory relieved on sale", "1200", credit=cost)

        elif doc.kind == "return":
            _row(rows, day, ref, "Customer return", note, "4010", debit=doc.total)
            allocated = sum((_money(a.amount) for a in doc.allocations.all()), ZERO)
            _row(rows, day, ref, "Customer return", "Customer debt reduced", "1100", credit=allocated)
            for code, amount in cash.items():
                _row(rows, day, ref, "Customer return", "Refund to customer", code, credit=amount)
            request = CustomerReturnRequest.objects.filter(posted=doc).prefetch_related("lines").first()
            dispositions = {x.source_line_id: x.disposition for x in request.lines.all()} if request else {}
            for line in doc.lines.all():
                cost = _money(line.unit_cost) * line.quantity * line.factor
                inv = "1210" if dispositions.get(line.source_line_id) == "quarantine" else "1200"
                _row(rows, day, ref, "Customer return", "Returned inventory received", inv, debit=cost)
                _row(rows, day, ref, "Customer return", "COGS reversed", "5000", credit=cost)

        elif doc.kind == "purchase":
            _row(rows, day, ref, "Purchase", note, "1200", debit=doc.total)
            for code, amount in cash.items():
                _row(rows, day, ref, "Purchase", "Paid supplier at purchase", code, credit=amount)
            payable = _money(doc.total) - sum(cash.values(), ZERO)
            _row(rows, day, ref, "Purchase", "Supplier payable created", "2000", credit=payable)

        elif doc.kind == "supplier_return":
            allocated = sum((_money(a.amount) for a in doc.allocations.all()), ZERO)
            _row(rows, day, ref, "Supplier return", "Supplier payable reduced", "2000", debit=allocated)
            for code, amount in cash.items():
                _row(rows, day, ref, "Supplier return", "Supplier refund received", code, debit=amount)
            _row(rows, day, ref, "Supplier return", note, "1200", credit=doc.total)

        elif doc.kind == "creditor_charge":
            if hasattr(doc, "correction") and doc.correction.status == "approved":
                continue
            code = EXPENSE_ACCOUNT.get(doc.payable_category or "other", "6990")
            _row(rows, day, ref, "Creditor bill", note, code, debit=doc.total)
            _row(rows, day, ref, "Creditor bill", "Trade payable created", "2000", credit=doc.total)

        elif doc.kind == "expense":
            if hasattr(doc, "correction") and doc.correction.status == "approved":
                continue
            code = EXPENSE_ACCOUNT.get(doc.expense_category or "other", "6990")
            _row(rows, day, ref, "Expense", note, code, debit=doc.total)
            for cash_code, amount in cash.items():
                _row(rows, day, ref, "Expense", note, cash_code, credit=amount)

        elif doc.kind == "collection":
            if hasattr(doc, "correction") and doc.correction.status == "approved":
                continue
            for code, amount in cash.items():
                _row(rows, day, ref, "Receivable collection", note, code, debit=amount)
            _row(rows, day, ref, "Receivable collection", "Customer debt collected", "1100", credit=doc.total)

        elif doc.kind == "supplier_payment":
            if hasattr(doc, "correction") and doc.correction.status == "approved":
                continue
            _row(rows, day, ref, "Supplier payment", "Trade payable settled", "2000", debit=doc.total)
            for code, amount in cash.items():
                _row(rows, day, ref, "Supplier payment", note, code, credit=amount)

        elif doc.kind == "inventory_writeoff":
            _row(rows, day, ref, "Inventory write-off", note, "6200", debit=doc.total)
            _row(rows, day, ref, "Inventory write-off", note, "1200", credit=doc.total)

        elif doc.kind == "reversal" and doc.original:
            original = doc.original
            if original.kind == "expense":
                for code, amount in cash.items():
                    _row(rows, day, ref, "Expense reversal", note, code, debit=amount)
                _row(rows, day, ref, "Expense reversal", note, EXPENSE_ACCOUNT.get(original.expense_category or "other", "6990"), credit=doc.total)
            elif original.kind == "collection":
                _row(rows, day, ref, "Collection reversal", note, "1100", debit=doc.total)
                for code, amount in cash.items():
                    _row(rows, day, ref, "Collection reversal", note, code, credit=amount)
            elif original.kind == "supplier_payment":
                for code, amount in cash.items():
                    _row(rows, day, ref, "Supplier payment reversal", note, code, debit=amount)
                _row(rows, day, ref, "Supplier payment reversal", note, "2000", credit=doc.total)
            elif original.kind == "creditor_charge":
                _row(rows, day, ref, "Creditor bill reversal", note, "2000", debit=doc.total)
                _row(rows, day, ref, "Creditor bill reversal", note, EXPENSE_ACCOUNT.get(original.payable_category or "other", "6990"), credit=doc.total)
    return rows


def _payroll_entries(branch, first, last):
    rows = []
    entries = PayrollEntry.objects.filter(
        period__branch=branch, period__end_date__range=(first, last),
        period__status__in=["locked", "reconciled"],
    ).select_related("period", "worker")
    for item in entries:
        day = item.period.end_date
        ref = f"PAY-{item.period.year}-{item.period.month:02d}-{item.worker.employee_code}"
        taxes = _money(item.paye_tax) + _money(item.bonus_tax) + _money(item.overtime_tax)
        pension = _money(item.employee_ssnit) + _money(item.employer_pension)
        deductions = _money(item.other_deductions)
        _row(rows, day, ref, "Payroll accrual", item.worker.full_name, "6100", debit=item.gross_pay)
        _row(rows, day, ref, "Payroll accrual", item.worker.full_name, "6110", debit=item.employer_pension)
        _row(rows, day, ref, "Payroll accrual", "Net salary payable", "2100", credit=item.net_pay)
        _row(rows, day, ref, "Payroll accrual", "Payroll taxes payable", "2110", credit=taxes)
        _row(rows, day, ref, "Payroll accrual", "Pension / SSNIT payable", "2120", credit=pension)
        _row(rows, day, ref, "Payroll accrual", "Other deductions payable", "2130", credit=deductions)

    payments = PayrollPayment.objects.filter(
        entry__period__branch=branch, created_at__date__range=(first, last)
    ).select_related("entry__worker")
    for payment in payments:
        day = timezone.localtime(payment.created_at).date()
        ref = payment.reference or f"SALARY-{payment.pk}"
        _row(rows, day, ref, "Salary payment", payment.entry.worker.full_name, "2100", debit=payment.amount)
        _row(rows, day, ref, "Salary payment", payment.entry.worker.full_name, PAYMENT_ACCOUNT.get(payment.method, "1000"), credit=payment.amount)
    return rows


def _manual_entries(branch, first, last):
    rows = []
    for journal in ManualJournal.objects.filter(
        branch=branch, status="posted", journal_date__range=(first, last)
    ).prefetch_related("lines"):
        for line in journal.lines.all():
            _row(rows, journal.journal_date, journal.reference, "Manual journal", line.description or journal.memo,
                 line.account_code, debit=line.debit, credit=line.credit)
    return rows


def ledger(branch, first, last):
    rows = _document_entries(branch, first, last) + _payroll_entries(branch, first, last) + _manual_entries(branch, first, last)
    rows.sort(key=lambda row: (row["date"], row["reference"], row["account_code"]))
    return rows


def trial_balance(branch, first, last):
    totals = {code: {"code": code, "name": name, "type": typ, "debit": ZERO, "credit": ZERO}
              for code, (name, typ) in ACCOUNTS.items()}
    for row in ledger(branch, first, last):
        bucket = totals.setdefault(row["account_code"], {
            "code": row["account_code"], "name": row["account"], "type": row["account_type"], "debit": ZERO, "credit": ZERO
        })
        bucket["debit"] += row["debit"]
        bucket["credit"] += row["credit"]
    result = []
    for item in totals.values():
        balance = item["debit"] - item["credit"]
        if item["debit"] or item["credit"]:
            result.append({**item, "balance": balance})
    result.sort(key=lambda x: x["code"])
    return result


def statements(branch, first, last):
    tb = trial_balance(branch, first, last)
    by_type = defaultdict(lambda: ZERO)
    for row in tb:
        typ = row["type"]
        if typ in {"asset", "expense", "contra_revenue"}:
            value = row["debit"] - row["credit"]
        else:
            value = row["credit"] - row["debit"]
        if typ == "contra_asset":
            by_type["asset"] -= value
        elif typ == "contra_revenue":
            by_type["revenue"] -= value
        else:
            by_type[typ] += value

    revenue = by_type["revenue"]
    expenses = by_type["expense"]
    profit = revenue - expenses
    assets = by_type["asset"]
    liabilities = by_type["liability"]
    equity = by_type["equity"] + profit

    cash_flow = {"operating": ZERO, "investing": ZERO, "financing": ZERO}
    for row in ledger(branch, first, last):
        if row["account_code"] not in PAYMENT_ACCOUNT.values():
            continue
        movement = row["debit"] - row["credit"]
        if row["source"] == "Manual journal":
            # Manual cash entries are conservatively classified as financing unless the memo is an asset acquisition.
            desc = row["description"].lower()
            bucket = "investing" if any(word in desc for word in ("asset", "equipment", "vehicle", "machine")) else "financing"
        else:
            bucket = "operating"
        cash_flow[bucket] += movement
    cash_flow["net_change"] = sum(cash_flow.values(), ZERO)

    return {
        "trial_balance": tb,
        "revenue": revenue, "expenses": expenses, "profit": profit,
        "assets": assets, "liabilities": liabilities, "equity": equity,
        "balance_check": assets - liabilities - equity,
        "cash_flow": cash_flow,
    }


def create_manual_journal(user, branch, journal_date, memo, lines):
    if not (user.is_superuser or user.has_perm("core.manage_company") or user.has_perm("core.operate_finance")):
        raise PermissionDenied("Finance permission is required.")
    try:
        journal_date = date.fromisoformat(str(journal_date))
    except ValueError:
        raise ValidationError("Choose a valid journal date.")
    if journal_date > timezone.localdate():
        raise ValidationError("Manual journals cannot be dated in the future.")
    memo = str(memo or "").strip()
    if len(memo) < 5:
        raise ValidationError("Explain the journal in at least five characters.")
    cleaned = []
    debit_total = credit_total = ZERO
    for line in lines:
        code = str(line.get("account_code") or "")
        if code not in ACCOUNTS:
            raise ValidationError("Choose only accounts from the KOFAD chart of accounts.")
        debit, credit = s.money(line.get("debit") or 0), s.money(line.get("credit") or 0)
        if bool(debit) == bool(credit):
            raise ValidationError("Each journal line must contain either a debit or a credit, not both.")
        cleaned.append((code, str(line.get("description") or "")[:200], debit, credit))
        debit_total += debit
        credit_total += credit
    if len(cleaned) < 2:
        raise ValidationError("A journal requires at least two lines.")
    if debit_total != credit_total:
        raise ValidationError(f"Journal is not balanced: debits {debit_total} versus credits {credit_total}.")

    with transaction.atomic():
        branch = s.lock_branch(branch)
        ref = s.reference("journal", branch)
        direct = bool(user.is_superuser or user.has_perm("core.manage_company"))
        journal = ManualJournal.objects.create(
            branch=branch, journal_date=journal_date, reference=ref, memo=memo,
            status="posted" if direct else "requested", requested_by=user,
            reviewed_by=user if direct else None, reviewed_at=timezone.now() if direct else None,
        )
        ManualJournalLine.objects.bulk_create([
            ManualJournalLine(journal=journal, account_code=code, description=desc, debit=debit, credit=credit)
            for code, desc, debit, credit in cleaned
        ])
        s.audit(user, branch, "journal.posted" if direct else "journal.requested", ref, {
            "memo": memo, "debits": str(debit_total), "credits": str(credit_total), "direct_authority": direct,
        }, category="accounting", severity="high", entity_type="manual_journal", entity_id=str(journal.pk))
    return journal


@transaction.atomic
def review_manual_journal(user, branch, journal_id, approve):
    if not (user.is_superuser or user.has_perm("core.manage_company") or user.has_perm("core.approve_operations")):
        raise PermissionDenied("Approval permission is required.")
    branch = s.lock_branch(branch)
    journal = ManualJournal.objects.select_for_update().prefetch_related("lines").get(pk=journal_id, branch=branch)
    if journal.status != "requested":
        raise ValidationError("This journal has already been reviewed.")
    if journal.requested_by_id == user.pk and not (user.is_superuser or user.has_perm("core.manage_company")):
        raise ValidationError("A different authorized colleague must review this journal.")
    journal.status = "posted" if approve else "rejected"
    journal.reviewed_by = user
    journal.reviewed_at = timezone.now()
    journal.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    s.audit(user, branch, f"journal.{journal.status}", journal.reference, {
        "memo": journal.memo,
    }, category="accounting", severity="high", entity_type="manual_journal", entity_id=str(journal.pk))
    return journal
