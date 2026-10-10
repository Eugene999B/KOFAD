from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import payroll_engine as engine
from .context import shell
from .exports import export
from .models import Payment, PayrollEntry, PayrollPeriod, PayrollRule, Worker
from .services import audit, permit
from .views import protected, problem, branch_for


RELEASED_PAYROLL_STATUSES = ("approved", "locked", "reconciled")


def _full_payroll_access(user):
    return any(user.has_perm("core." + permission) for permission in (
        "operate_finance", "view_reports", "manage_company",
    ))


def payroll_portal(view):
    """A linked worker may access ONLY their own released records."""
    @login_required
    @wraps(view)
    def inner(request, *args, **kwargs):
        branch = branch_for(request)
        if not _full_payroll_access(request.user) and not Worker.objects.filter(
            branch=branch, user=request.user
        ).exists():
            raise PermissionDenied("Your staff account is not linked to a worker here.")
        return view(request, branch, *args, **kwargs)
    return inner


ENTRY_FIELDS = [
    "basic_salary", "allowances", "bonus", "overtime",
    "other_earnings", "pretax_relief", "other_deductions",
]


def _decimal(value, label):
    try:
        number = Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValidationError(f"{label} must be a valid amount.")
    if number < 0:
        raise ValidationError(f"{label} cannot be negative.")
    return number


def _rate(value, label):
    number = _decimal(value, label)
    if number > 100:
        raise ValidationError(f"{label} cannot exceed 100%.")
    return number


def _period_summary(period):
    totals = engine.period_totals(period)
    issue_count = sum(len(entry.validation_flags or []) for entry in period.entries.all())
    paid_workers = sum(1 for entry in period.entries.all() if entry.balance <= 0 and entry.net_pay > 0)
    return {
        **totals,
        "workers": period.entries.count(),
        "issues": issue_count,
        "paid_workers": paid_workers,
    }


@payroll_portal
def payroll(request, branch):
    if not _full_payroll_access(request.user):
        if request.method != "GET":
            raise PermissionDenied("Personal payroll access is read-only.")
        worker = get_object_or_404(Worker, branch=branch, user=request.user)
        entries = PayrollEntry.objects.filter(
            worker=worker, period__branch=branch,
            period__status__in=RELEASED_PAYROLL_STATUSES,
        ).select_related("period", "period__rule").order_by("-period__year", "-period__month")[:36]
        return render(request, "payroll_self_service.html", {
            "title": "My payslips", "worker": worker, "entries": entries,
        })
    if request.method == "POST":
        permit(request.user, branch, "operate_finance")
        try:
            period = engine.create_period(
                request.user, branch,
                request.POST.get("year"), request.POST.get("month"),
            )
            audit(request.user, branch, "payroll.period.opened", period.pk, {
                "year": period.year, "month": period.month, "rule": period.rule.name,
            })
            messages.success(request, f"{period.label} payroll is ready.")
            return redirect("payroll_period", pk=period.pk)
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))

    periods = PayrollPeriod.objects.filter(branch=branch).select_related("rule", "prepared_by", "approved_by")
    year = request.GET.get("year", "").strip()
    status = request.GET.get("status", "").strip()
    if year.isdigit():
        periods = periods.filter(year=int(year))
    if status:
        periods = periods.filter(status=status)
    period_cards = [{"period": item, "summary": _period_summary(item)} for item in periods[:36]]
    current_rule = engine.rule_for(timezone.localdate())
    worker_count = Worker.objects.filter(branch=branch, status__in=["active", "leave"]).count()
    latest = PayrollPeriod.objects.filter(branch=branch).first()
    latest_summary = _period_summary(latest) if latest else None
    return render(request, "payroll.html", {
        "title": "Payroll", "period_cards": period_cards, "year": year, "status": status,
        "statuses": PayrollPeriod.STATUSES, "current_rule": current_rule,
        "worker_count": worker_count, "latest": latest, "latest_summary": latest_summary,
        "current_year": timezone.localdate().year, "current_month": timezone.localdate().month,
    })


@protected("operate_finance|view_reports|manage_company")
def payroll_period(request, branch, pk):
    period = get_object_or_404(
        PayrollPeriod.objects.select_related("rule", "prepared_by", "approved_by", "locked_by"),
        pk=pk, branch=branch,
    )
    entries = period.entries.select_related("worker").order_by("worker__last_name", "worker__first_name")
    q = request.GET.get("q", "").strip()[:100]
    department = request.GET.get("department", "").strip()[:100]
    issue = request.GET.get("issue", "")
    if q:
        entries = entries.filter(
            Q(worker__employee_code__icontains=q) | Q(worker__first_name__icontains=q) |
            Q(worker__last_name__icontains=q) | Q(worker__phone__icontains=q)
        )
    if department:
        entries = entries.filter(worker__department=department)
    if issue == "1":
        ids = [entry.pk for entry in entries if entry.validation_flags]
        entries = entries.filter(pk__in=ids)

    departments = Worker.objects.filter(branch=branch).exclude(
        department=""
    ).values_list("department", flat=True).distinct().order_by("department")
    return render(request, "payroll_period.html", {
        "title": f"Payroll · {period.label}", "period": period, "entries": entries,
        "summary": _period_summary(period), "q": q, "department": department, "issue": issue,
        "departments": departments, "methods": Payment.METHODS,
        "can_manage": request.user.has_perm("core.operate_finance"),
        "can_approve": request.user.has_perm("core.approve_operations"),
    })


@protected("operate_finance")
@require_POST
def payroll_entry_update(request, branch, pk, entry_id):
    period = get_object_or_404(PayrollPeriod, pk=pk, branch=branch)
    entry = get_object_or_404(PayrollEntry, pk=entry_id, period=period)
    try:
        if period.status != "draft":
            raise ValidationError("Payroll earnings can only be changed while the period is in draft.")
        before = {field: str(getattr(entry, field)) for field in ENTRY_FIELDS}
        for field in ENTRY_FIELDS:
            setattr(entry, field, _decimal(request.POST.get(field), field.replace("_", " ").title()))
        entry.save(update_fields=ENTRY_FIELDS + ["updated_at"])
        engine.calculate_entry(entry)
        audit(request.user, branch, "payroll.entry.updated", entry.pk, {
            "worker": entry.worker.employee_code, "before": before,
            "after": {field: str(getattr(entry, field)) for field in ENTRY_FIELDS},
        })
        messages.success(request, f"{entry.worker.full_name}'s payroll calculation was updated.")
    except ValidationError as exc:
        messages.error(request, problem(exc))
    return redirect("payroll_period", pk=period.pk)


@protected("operate_finance|manage_company")
@require_POST
def payroll_action(request, branch, pk):
    period = get_object_or_404(PayrollPeriod, pk=pk, branch=branch)
    action = request.POST.get("action")
    try:
        if action == "recalculate":
            if period.status != "draft":
                raise ValidationError("Only draft payroll can be recalculated.")
            engine.recalculate_period(period)
            messages.success(request, "Payroll recalculated from the effective statutory rule.")
        elif action == "prepare":
            period, issues = engine.prepare_period(request.user, period)
            audit(request.user, branch, "payroll.prepared", period.pk, {"issues": issues, "totals": {k: str(v) for k, v in engine.period_totals(period).items()}})
            if request.user.is_superuser or request.user.has_perm("core.manage_company"):
                period = engine.approve_period(request.user, period, owner_direct=True)
                audit(request.user, branch, "payroll.approved", period.pk, {"approved_by": request.user.username, "owner_direct": True})
                messages.success(request, "Payroll prepared and owner-approved. Locking for payment remains a separate control.")
            else:
                messages.success(request, "Payroll prepared and sent to the Approval Center.")
        elif action == "approve":
            owner_direct = request.user.is_superuser or request.user.has_perm("core.manage_company")
            if not owner_direct:
                permit(request.user, branch, "approve_operations")
            period = engine.approve_period(request.user, period, owner_direct=owner_direct)
            audit(request.user, branch, "payroll.approved", period.pk, {"approved_by": request.user.username})
            messages.success(request, "Payroll approved by an independent reviewer.")
        elif action == "lock":
            permit(request.user, branch, "approve_operations")
            period = engine.lock_period(request.user, period)
            audit(request.user, branch, "payroll.locked", period.pk, {"locked_by": request.user.username})
            messages.success(request, "Payroll locked for salary payments.")
        elif action == "return_to_draft":
            permit(request.user, branch, "approve_operations")
            period = engine.return_to_draft(period)
            audit(request.user, branch, "payroll.returned_to_draft", period.pk, {"by": request.user.username})
            messages.success(request, "Payroll returned to draft for correction.")
        elif action == "reconcile":
            period, outstanding = engine.reconcile_period(period)
            audit(request.user, branch, "payroll.reconciled", period.pk, {"outstanding": outstanding})
            messages.success(
                request,
                "Payroll fully reconciled." if not outstanding else "Reconciliation checked; outstanding salary balances remain.",
            )
        else:
            raise ValidationError("Choose a valid payroll action.")
    except ValidationError as exc:
        messages.error(request, problem(exc))
    return redirect("payroll_period", pk=period.pk)


@protected("operate_finance")
@require_POST
def payroll_payment(request, branch, pk, entry_id):
    period = get_object_or_404(PayrollPeriod, pk=pk, branch=branch)
    entry = get_object_or_404(PayrollEntry, pk=entry_id, period=period)
    try:
        payment = engine.record_payment(
            request.user, entry, request.POST.get("amount"),
            request.POST.get("method", "bank"),
            request.POST.get("reference", ""), request.POST.get("note", ""),
        )
        audit(request.user, branch, "payroll.payment.posted", payment.pk, {
            "worker": entry.worker.employee_code, "amount": str(payment.amount),
            "method": payment.method, "reference": payment.reference,
        })
        messages.success(request, f"Salary payment posted for {entry.worker.full_name}.")
    except ValidationError as exc:
        messages.error(request, problem(exc))
    return redirect("payroll_period", pk=period.pk)


@protected("operate_finance|view_reports|manage_company")
def payroll_export(request, branch, pk, format):
    period = get_object_or_404(PayrollPeriod.objects.select_related("rule"), pk=pk, branch=branch)
    rows = []
    for entry in period.entries.select_related("worker"):
        rows.append({
            "employee": entry.worker.employee_code, "worker": entry.worker.full_name,
            "department": entry.worker.department, "basic": entry.basic_salary,
            "allowances": entry.allowances, "bonus": entry.bonus, "overtime": entry.overtime,
            "other": entry.other_earnings, "gross": entry.gross_pay,
            "ssnit": entry.ssnit_employee,
            "paye": entry.paye_tax + entry.bonus_tax + entry.overtime_tax,
            "deductions": entry.other_deductions, "net": entry.net_pay,
            "paid": entry.paid_amount, "balance": entry.balance,
            "flags": "; ".join(entry.validation_flags or []),
        })
    columns = [
        ("employee", "Employee ID"), ("worker", "Worker"), ("department", "Department"),
        ("basic", "Basic salary"), ("allowances", "Allowances"), ("bonus", "Bonus"),
        ("overtime", "Overtime"), ("other", "Other earnings"), ("gross", "Gross pay"),
        ("ssnit", "Employee SSNIT"), ("paye", "PAYE & special taxes"),
        ("deductions", "Other deductions"), ("net", "Net pay"), ("paid", "Paid"),
        ("balance", "Outstanding"), ("flags", "Validation flags"),
    ]
    totals = engine.period_totals(period)
    audit(request.user, branch, "payroll.exported", period.pk, {"format": format, "rows": len(rows)})
    return export(
        rows, format, f"Payroll register · {period.label}", shell(request)["company"], columns,
        filename=f"kofad-payroll-{period.year}-{period.month:02d}", sheet_name="Payroll",
        metadata={
            "Location": branch.name, "Period": period.label, "Status": period.get_status_display(),
            "Statutory rule": period.rule.name, "Rule effective": period.rule.effective_from,
        },
        summary={
            "Workers": len(rows), "Gross pay": totals["gross"], "Net pay": totals["net"],
            "Outstanding": totals["balance"],
        },
        notes=["Payroll figures are preserved with the statutory rule snapshot used for the period."],
    )


@payroll_portal
def payslip(request, branch, pk, entry_id, format="pdf"):
    period = get_object_or_404(PayrollPeriod, pk=pk, branch=branch)
    entry = get_object_or_404(PayrollEntry.objects.select_related("worker", "period__rule"), pk=entry_id, period=period)
    if not _full_payroll_access(request.user):
        if entry.worker.user_id != request.user.pk or period.status not in RELEASED_PAYROLL_STATUSES:
            raise Http404("Payslip not available.")
    rows = [
        {"item": "Basic salary", "earning": entry.basic_salary, "deduction": ""},
        {"item": "Allowances", "earning": entry.allowances, "deduction": ""},
        {"item": "Bonus", "earning": entry.bonus, "deduction": ""},
        {"item": "Overtime", "earning": entry.overtime, "deduction": ""},
        {"item": "Other earnings", "earning": entry.other_earnings, "deduction": ""},
        {"item": "Employee SSNIT", "earning": "", "deduction": entry.ssnit_employee},
        {"item": "PAYE", "earning": "", "deduction": entry.paye_tax},
        {"item": "Bonus tax", "earning": "", "deduction": entry.bonus_tax},
        {"item": "Overtime tax", "earning": "", "deduction": entry.overtime_tax},
        {"item": "Other deductions", "earning": "", "deduction": entry.other_deductions},
    ]
    audit(request.user, branch, "payroll.payslip.downloaded", entry.pk, {"format": format})
    return export(
        rows, format, f"Payslip · {entry.worker.full_name}", shell(request)["company"],
        [("item", "Pay item"), ("earning", "Earnings"), ("deduction", "Deductions")],
        filename=f"kofad-payslip-{entry.worker.employee_code}-{period.year}-{period.month:02d}",
        sheet_name="Payslip",
        metadata={
            "Employee ID": entry.worker.employee_code, "Job title": entry.worker.job_title,
            "Department": entry.worker.department or "—", "Period": period.label,
            "Status": period.get_status_display(),
        },
        summary={
            "Gross pay": entry.gross_pay, "Net pay": entry.net_pay,
            "Paid": entry.paid_amount, "Balance": entry.balance,
        },
    )


@protected("manage_company")
def payroll_rules(request, branch):
    current = engine.rule_for(timezone.localdate())
    if request.method == "POST":
        try:
            effective = date.fromisoformat(request.POST.get("effective_from", ""))
            if effective <= current.effective_from:
                raise ValidationError("A new payroll rule must start after the current rule effective date.")
            amounts = request.POST.getlist("band_amount")
            rates = request.POST.getlist("band_rate")
            if not rates or len(amounts) != len(rates):
                raise ValidationError("Enter complete PAYE bands.")
            bands = []
            for index, rate in enumerate(rates):
                rate_value = _decimal(rate, f"Band {index + 1} rate")
                amount = amounts[index].strip()
                bands.append({
                    "amount": None if not amount else str(_decimal(amount, f"Band {index + 1} amount")),
                    "rate": str(rate_value),
                })
            with transaction.atomic():
                previous = PayrollRule.objects.filter(
                    code="GH-PAYROLL", active=True, effective_from__lt=effective,
                    effective_to__isnull=True,
                ).order_by("-effective_from").first()
                if previous:
                    previous.effective_to = effective - timedelta(days=1)
                    previous.save(update_fields=["effective_to"])
                rule = PayrollRule.objects.create(
                    code="GH-PAYROLL", name=request.POST.get("name", "").strip()[:160] or f"Ghana payroll rules · {effective}",
                    effective_from=effective, resident_bands=bands,
                    employee_ssnit_rate=_rate(request.POST.get("employee_ssnit_rate"), "Employee SSNIT rate"),
                    employer_pension_rate=_rate(request.POST.get("employer_pension_rate"), "Employer pension rate"),
                    first_tier_rate=_rate(request.POST.get("first_tier_rate"), "First-tier rate"),
                    tier2_rate=_rate(request.POST.get("tier2_rate"), "Tier 2 rate"),
                    min_insurable_earnings=_decimal(request.POST.get("min_insurable_earnings"), "Minimum insurable earnings"),
                    max_insurable_earnings=_decimal(request.POST.get("max_insurable_earnings"), "Maximum insurable earnings"),
                    nonresident_rate=_rate(request.POST.get("nonresident_rate"), "Non-resident rate"),
                    casual_rate=_rate(request.POST.get("casual_rate"), "Casual rate"),
                    bonus_rate=_rate(request.POST.get("bonus_rate"), "Bonus rate"),
                    bonus_limit_percent=_rate(request.POST.get("bonus_limit_percent"), "Bonus limit"),
                    junior_overtime_rate=_rate(request.POST.get("junior_overtime_rate"), "Overtime rate"),
                    junior_overtime_excess_rate=_rate(request.POST.get("junior_overtime_excess_rate"), "Overtime excess rate"),
                    junior_overtime_basic_limit=_decimal(request.POST.get("junior_overtime_basic_limit"), "Junior staff limit"),
                    notes=request.POST.get("notes", "").strip(), created_by=request.user,
                )
                if rule.min_insurable_earnings > rule.max_insurable_earnings:
                    raise ValidationError("Minimum insurable earnings cannot exceed the maximum.")
            audit(request.user, branch, "payroll.rule.created", rule.pk, {"effective_from": str(effective), "name": rule.name})
            messages.success(request, "New effective-dated payroll rule saved. Existing payroll snapshots were not changed.")
            return redirect("payroll_rules")
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))
    history = PayrollRule.objects.order_by("-effective_from")[:20]
    bands = current.resident_bands or []
    return render(request, "payroll_rules.html", {
        "title": "Payroll rules", "current": current, "history": history, "bands": bands,
    })
