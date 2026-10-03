import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Closing, PayrollEntry, PayrollPayment, PayrollPeriod, PayrollRule, Worker


ZERO = Decimal("0")
HUNDRED = Decimal("100")
CENT = Decimal("0.01")


def q(value):
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def pct(value, rate):
    return q(Decimal(str(value or 0)) * Decimal(str(rate or 0)) / HUNDRED)


def progressive_tax(amount, bands):
    remaining = max(ZERO, Decimal(str(amount or 0)))
    total = ZERO
    for band in bands or []:
        if remaining <= 0:
            break
        raw_amount = band.get("amount")
        width = remaining if raw_amount in (None, "") else min(remaining, Decimal(str(raw_amount)))
        total += width * Decimal(str(band.get("rate") or 0)) / HUNDRED
        remaining -= width
    return q(total)


def rule_for(day):
    rule = PayrollRule.objects.filter(
        active=True, effective_from__lte=day
    ).filter(
        models_q_effective(day)
    ).order_by("-effective_from", "-pk").first()
    if not rule:
        raise ValidationError("No approved payroll rule covers this payroll period.")
    return rule


def models_q_effective(day):
    from django.db.models import Q
    return Q(effective_to__isnull=True) | Q(effective_to__gte=day)


def create_period(user, branch, year, month):
    try:
        year, month = int(year), int(month)
        first = date(year, month, 1)
    except (TypeError, ValueError):
        raise ValidationError("Choose a valid payroll month.")
    last = date(year, month, calendar.monthrange(year, month)[1])
    rule = rule_for(last)
    period, created = PayrollPeriod.objects.get_or_create(
        branch=branch, year=year, month=month,
        defaults={
            "start_date": first, "end_date": last, "rule": rule,
            "created_by": user,
        },
    )
    if not created and period.status != "draft":
        return period
    if period.rule_id != rule.pk:
        period.rule = rule
        period.save(update_fields=["rule"])
    active_workers = Worker.objects.filter(
        branch=branch, status__in=["active", "leave"]
    ).filter(hire_date__lte=last).exclude(exit_date__lt=first)
    existing = set(period.entries.values_list("worker_id", flat=True))
    for worker in active_workers:
        if worker.pk in existing:
            continue
        PayrollEntry.objects.create(
            period=period, worker=worker, basic_salary=worker.base_salary,
            allowances=worker.recurring_allowance,
        )
    recalculate_period(period)
    return period


def _prior_bonus(entry):
    return q(PayrollEntry.objects.filter(
        worker=entry.worker,
        period__year=entry.period.year,
        period__start_date__lt=entry.period.start_date,
    ).aggregate(total=Sum("bonus"))["total"] or 0)


def _previous_entry(entry):
    return PayrollEntry.objects.filter(
        worker=entry.worker, period__start_date__lt=entry.period.start_date
    ).order_by("-period__start_date").first()


def calculate_entry(entry):
    rule = entry.period.rule
    worker = entry.worker
    basic = q(entry.basic_salary)
    allowance = q(entry.allowances)
    bonus = q(entry.bonus)
    overtime = q(entry.overtime)
    other = q(entry.other_earnings)
    relief = q(entry.pretax_relief)
    other_deductions = q(entry.other_deductions)
    gross = q(basic + allowance + bonus + overtime + other)

    insurable = ZERO
    if worker.ssnit_enabled and basic > 0:
        insurable = basic
        if rule.min_insurable_earnings:
            insurable = max(insurable, rule.min_insurable_earnings)
        if rule.max_insurable_earnings:
            insurable = min(insurable, rule.max_insurable_earnings)
    ssnit_employee = pct(insurable, rule.employee_ssnit_rate)
    employer_pension = pct(insurable, rule.employer_pension_rate)
    first_tier = pct(insurable, rule.first_tier_rate)
    tier2 = pct(insurable, rule.tier2_rate)

    annual_basic = basic * Decimal("12")
    bonus_cap = annual_basic * Decimal(str(rule.bonus_limit_percent)) / HUNDRED
    remaining_bonus_cap = max(ZERO, bonus_cap - _prior_bonus(entry))
    special_bonus = min(bonus, remaining_bonus_cap)
    bonus_tax = pct(special_bonus, rule.bonus_rate)
    regular_bonus = max(ZERO, bonus - special_bonus)

    overtime_tax = ZERO
    regular_overtime = overtime
    if worker.junior_staff and basic <= rule.junior_overtime_basic_limit:
        normal_ot = min(overtime, basic * Decimal("0.50"))
        excess_ot = max(ZERO, overtime - normal_ot)
        overtime_tax = q(
            normal_ot * Decimal(str(rule.junior_overtime_rate)) / HUNDRED
            + excess_ot * Decimal(str(rule.junior_overtime_excess_rate)) / HUNDRED
        )
        regular_overtime = ZERO

    chargeable = q(max(
        ZERO,
        basic + allowance + other + regular_bonus + regular_overtime - ssnit_employee - relief,
    ))
    if worker.tax_mode == "resident":
        paye_tax = progressive_tax(chargeable, rule.resident_bands)
    elif worker.tax_mode == "nonresident":
        paye_tax = pct(chargeable, rule.nonresident_rate)
    elif worker.tax_mode == "casual":
        paye_tax = pct(gross, rule.casual_rate)
        bonus_tax = ZERO
        overtime_tax = ZERO
    else:
        paye_tax = bonus_tax = overtime_tax = ZERO

    total_tax = paye_tax + bonus_tax + overtime_tax
    net = q(gross - ssnit_employee - total_tax - other_deductions)
    flags = []
    if worker.ssnit_enabled and not worker.ssnit_number:
        flags.append("Missing SSNIT number")
    if worker.tax_mode == "resident" and not worker.ghana_card_number:
        flags.append("Missing Ghana Card / taxpayer identity")
    if not worker.bank_account_number and not worker.momo_number:
        flags.append("No salary payment account recorded")
    if net < 0:
        flags.append("Net pay is negative")
    previous = _previous_entry(entry)
    if previous and previous.gross_pay:
        change = abs(gross - previous.gross_pay) / previous.gross_pay
        if change >= Decimal("0.20"):
            flags.append(f"Gross pay changed {q(change * 100)}% from prior payroll")

    entry.gross_pay = gross
    entry.ssnit_employee = ssnit_employee
    entry.employer_pension = employer_pension
    entry.first_tier_remittance = first_tier
    entry.tier2_contribution = tier2
    entry.chargeable_income = chargeable
    entry.paye_tax = paye_tax
    entry.bonus_tax = bonus_tax
    entry.overtime_tax = overtime_tax
    entry.net_pay = net
    entry.validation_flags = flags
    entry.calculation_snapshot = {
        "rule_id": rule.pk,
        "rule_name": rule.name,
        "rule_effective_from": rule.effective_from.isoformat(),
        "worker": {
            "employee_code": worker.employee_code, "name": worker.full_name,
            "job_title": worker.job_title, "department": worker.department,
            "tax_mode": worker.tax_mode, "ssnit_enabled": worker.ssnit_enabled,
        },
        "rates": {
            "employee_ssnit": str(rule.employee_ssnit_rate),
            "employer_pension": str(rule.employer_pension_rate),
            "first_tier": str(rule.first_tier_rate),
            "tier2": str(rule.tier2_rate),
        },
        "resident_bands": rule.resident_bands,
    }
    entry.save(update_fields=[
        "gross_pay", "ssnit_employee", "employer_pension", "first_tier_remittance",
        "tier2_contribution", "chargeable_income", "paye_tax", "bonus_tax",
        "overtime_tax", "net_pay", "validation_flags", "calculation_snapshot", "updated_at",
    ])
    return entry


def recalculate_period(period):
    if period.status != "draft":
        return period
    for entry in period.entries.select_related("worker", "period__rule"):
        calculate_entry(entry)
    return period


def period_totals(period):
    totals = period.entries.aggregate(
        gross=Sum("gross_pay"), net=Sum("net_pay"), paye=Sum("paye_tax"),
        bonus_tax=Sum("bonus_tax"), overtime_tax=Sum("overtime_tax"),
        employee_ssnit=Sum("ssnit_employee"), employer_pension=Sum("employer_pension"),
        first_tier=Sum("first_tier_remittance"), tier2=Sum("tier2_contribution"),
        paid=Sum("paid_amount"),
    )
    cleaned = {key: q(value) for key, value in totals.items()}
    cleaned["tax_total"] = q(cleaned["paye"] + cleaned["bonus_tax"] + cleaned["overtime_tax"])
    cleaned["employer_cost"] = q(cleaned["gross"] + cleaned["employer_pension"])
    cleaned["balance"] = q(cleaned["net"] - cleaned["paid"])
    return cleaned


def validate_period(period):
    recalculate_period(period)
    issues = []
    if not period.entries.exists():
        issues.append("No workers are included in this payroll.")
    for entry in period.entries.select_related("worker"):
        for flag in entry.validation_flags:
            issues.append(f"{entry.worker.employee_code}: {flag}")
    return issues


@transaction.atomic
def prepare_period(user, period):
    locked = PayrollPeriod.objects.select_for_update().get(pk=period.pk)
    if locked.status != "draft":
        raise ValidationError("Only a draft payroll can be prepared.")
    issues = validate_period(locked)
    blocking = [item for item in issues if "negative" in item.lower()]
    if blocking:
        raise ValidationError("Resolve blocking payroll validation issues before review.")
    locked.status = "prepared"
    locked.prepared_by = user
    locked.prepared_at = timezone.now()
    locked.save(update_fields=["status", "prepared_by", "prepared_at"])
    return locked, issues


@transaction.atomic
def approve_period(user, period):
    locked = PayrollPeriod.objects.select_for_update().get(pk=period.pk)
    if locked.status != "prepared":
        raise ValidationError("Payroll must be prepared before approval.")
    if locked.prepared_by_id == user.pk and not (user.is_superuser or user.has_perm("core.manage_company")):
        raise ValidationError("A different authorised user must approve payroll.")
    locked.status = "approved"
    locked.approved_by = user
    locked.approved_at = timezone.now()
    locked.save(update_fields=["status", "approved_by", "approved_at"])
    return locked


@transaction.atomic
def lock_period(user, period):
    locked = PayrollPeriod.objects.select_for_update().get(pk=period.pk)
    if locked.status != "approved":
        raise ValidationError("Only approved payroll can be locked for payment.")
    locked.status = "locked"
    locked.locked_by = user
    locked.locked_at = timezone.now()
    locked.save(update_fields=["status", "locked_by", "locked_at"])
    return locked


@transaction.atomic
def record_payment(user, entry, amount, method, reference="", note=""):
    row = PayrollEntry.objects.select_for_update().select_related("period").get(pk=entry.pk)
    if row.period.status not in ["locked", "reconciled"]:
        raise ValidationError("Payroll must be locked before salary payments are posted.")
    if Closing.objects.filter(branch=row.period.branch, date=timezone.localdate()).exists():
        raise ValidationError("Today is already closed. Salary payments cannot be posted after daily closing.")
    amount = q(amount)
    if amount <= 0 or amount > row.balance:
        raise ValidationError("Salary payment must be positive and cannot exceed the outstanding net pay.")
    payment = PayrollPayment.objects.create(
        entry=row, amount=amount, method=method, reference=str(reference or "")[:120],
        note=str(note or ""), created_by=user,
    )
    row.paid_amount = q(row.paid_amount + amount)
    row.save(update_fields=["paid_amount", "updated_at"])
    return payment


@transaction.atomic
def return_to_draft(period):
    locked = PayrollPeriod.objects.select_for_update().get(pk=period.pk)
    if locked.status not in ["prepared", "approved"]:
        raise ValidationError("Only prepared or approved payroll can be returned to draft.")
    locked.status = "draft"
    locked.prepared_by = None
    locked.prepared_at = None
    locked.approved_by = None
    locked.approved_at = None
    locked.save(update_fields=["status", "prepared_by", "prepared_at", "approved_by", "approved_at"])
    return locked


@transaction.atomic
def reconcile_period(period):
    locked = PayrollPeriod.objects.select_for_update().get(pk=period.pk)
    if locked.status not in ["locked", "reconciled"]:
        raise ValidationError("Only locked payroll can be reconciled.")
    outstanding = any(entry.balance > 0 for entry in locked.entries.all())
    if not outstanding:
        locked.status = "reconciled"
        locked.reconciled_at = timezone.now()
        locked.save(update_fields=["status", "reconciled_at"])
    return locked, outstanding
