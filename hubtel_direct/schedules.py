"""Deterministic debt schedules from KOFAD's actual unpaid account balance.

The sales user supplies first and final dates plus frequency; KOFAD derives
the installment count and amounts, never a duplicate handwritten price. These dates are the presented authorization
terms; a provider charge can only happen later with confirmed consent and
all independent deployment/finance gates enabled.
"""
from datetime import date, timedelta
from calendar import monthrange
from decimal import Decimal, ROUND_UP

from django.core.exceptions import ValidationError

CENT = Decimal("0.01")
MAX_INSTALLMENTS = 120


def next_date(day, frequency):
    if frequency == "once":
        return None
    if frequency == "daily":
        return day + timedelta(days=1)
    if frequency == "weekly":
        return day + timedelta(days=7)
    if frequency in {"monthly", "quarterly", "yearly"}:
        months = {"monthly": 1, "quarterly": 3, "yearly": 12}[frequency]
        year = day.year + (day.month - 1 + months) // 12
        month = (day.month - 1 + months) % 12 + 1
        return day.replace(year=year, month=month, day=min(day.day, monthrange(year, month)[1]))
    raise ValidationError("Choose a supported repayment interval.")


def preview_by_end_date(total, frequency, first_date, final_date, *, from_hour=8, until_hour=19):
    """Derive the number of agreed collection dates from a date range.

    The last actual payment date may precede the requested cutoff when it
    does not fall exactly on the chosen interval; the dated table is binding.
    """
    if isinstance(first_date, str):
        try:
            first_date = date.fromisoformat(first_date)
        except ValueError as exc:
            raise ValidationError("Choose a valid first collection date.") from exc
    if isinstance(final_date, str):
        try:
            final_date = date.fromisoformat(final_date)
        except ValueError as exc:
            raise ValidationError("Choose a valid final collection date.") from exc
    if (not isinstance(first_date, date) or not isinstance(final_date, date)
            or final_date < first_date or (final_date - first_date).days > 3660):
        raise ValidationError("Final collection date must be on or after the first date, within ten years.")
    if frequency == "once" and final_date != first_date:
        raise ValidationError("One-time repayment must have the same first and final date.")
    cursor = first_date
    count = 0
    while cursor is not None and cursor <= final_date:
        count += 1
        if count > MAX_INSTALLMENTS:
            raise ValidationError("The chosen dates require more than 120 installments.")
        cursor = next_date(cursor, frequency)
    result = preview(total, frequency, first_date, count,
                     from_hour=from_hour, until_hour=until_hour)
    result["requested_end_date"] = final_date.isoformat()
    return result


def preview(total, frequency, first_date, installments, *, from_hour=8, until_hour=19):
    """Return exact dated planned amounts, including final rounding adjustment.

    No payment is requested here. The merchant's execution window is shown
    honestly as a WINDOW, never as a guaranteed exact debit time. Currency is
    rounded upward to cents so sum(amounts) always equals the source balance.
    """
    try:
        count = int(installments)
        if str(installments).strip() != str(count):
            raise ValueError
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValidationError("Enter a whole number of payments.") from exc
    if not 1 <= count <= MAX_INSTALLMENTS:
        raise ValidationError("Choose between 1 and 120 payments.")
    if frequency not in {"once", "daily", "weekly", "monthly", "quarterly", "yearly"}:
        raise ValidationError("Choose a supported repayment interval.")
    if frequency == "once" and count != 1:
        raise ValidationError("One-time collection must have exactly one payment.")
    if not isinstance(first_date, date):
        raise ValidationError("Enter a valid first collection date.")
    amount = Decimal(str(total))
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(CENT):
        raise ValidationError("The recorded unpaid debt must be a positive GHS amount.")
    if count > int(amount / CENT):
        raise ValidationError("There cannot be more payments than GHS 0.01 installments.")
    if not (0 <= int(from_hour) < int(until_hour) <= 24):
        raise ValidationError("Collection hours are not configured.")
    regular = (amount / Decimal(count)).quantize(CENT, rounding=ROUND_UP)
    remaining = amount
    day = first_date
    rows = []
    for ordinal in range(1, count + 1):
        installment_amount = min(regular, remaining)
        if installment_amount <= 0:
            raise ValidationError("Invalid installment schedule.")
        rows.append({
            "number": ordinal, "date": day.isoformat(),
            "amount": str(installment_amount),
        })
        remaining -= installment_amount
        if ordinal < count:
            day = next_date(day, frequency)
    if remaining != Decimal("0.00"):
        raise ValidationError("Calculated installments did not balance.")
    return {
        "installment_count": count,
        "amount_per_period": regular,
        "last_amount": Decimal(rows[-1]["amount"]),
        "end_date": rows[-1]["date"],
        "schedule": rows,
        "collection_window": f"{int(from_hour):02d}:00–{int(until_hour):02d}:00 Ghana time",
    }
