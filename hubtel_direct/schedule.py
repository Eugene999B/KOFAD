"""Auditable repayment timetable. These are proposed terms, not Hubtel charges.

Dates are local Ghana business dates; an hour is an earliest eligible collection
window, not a guarantee that the provider will settle at that exact minute.
"""
from datetime import date
from decimal import Decimal, ROUND_UP
from django.core.exceptions import ValidationError

from . import client


def next_date(day, frequency):
    from .mandates import _period_next
    return _period_next(day, frequency)


def calculate(balance, first_date, final_date, frequency, hour=9):
    balance = client.money(balance)
    if isinstance(first_date, str):
        try:
            first_date = date.fromisoformat(first_date)
        except ValueError as exc:
            raise ValidationError("Choose a valid first collection date.") from exc
    if isinstance(final_date, str):
        try:
            final_date = date.fromisoformat(final_date)
        except ValueError as exc:
            raise ValidationError("Choose a valid final repayment date.") from exc
    if (not isinstance(first_date, date) or not isinstance(final_date, date)
            or final_date < first_date or (final_date-first_date).days > 366):
        raise ValidationError("Final repayment date must be on or after the first date, within one year.")
    if balance <= 0:
        raise ValidationError("The sale has no unpaid balance.")
    if frequency not in {"once", "daily", "weekly", "monthly", "quarterly", "yearly"}:
        raise ValidationError("Choose a valid repayment frequency.")
    try:
        hour = int(hour)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Choose a valid collection hour.") from exc
    if not 0 <= hour <= 23:
        raise ValidationError("Choose a valid collection hour.")
    if frequency == "once" and final_date != first_date:
        raise ValidationError("One-time repayment has only one collection date.")
    days = []
    upcoming = first_date
    while upcoming and upcoming <= final_date:
        days.append(upcoming)
        if len(days) > 366:
            raise ValidationError("The repayment plan contains too many collections.")
        upcoming = next_date(upcoming, frequency)
    if not days:
        raise ValidationError("The selected dates contain no scheduled collections.")
    amount = (balance / Decimal(len(days))).quantize(Decimal("0.01"), rounding=ROUND_UP)
    last_amount = balance - amount * (len(days)-1)
    if last_amount <= 0:
        raise ValidationError("This balance is too small for the chosen number of collections.")
    entries = [
        {"date": day, "amount": amount if index < len(days)-1 else last_amount,
         "hour": hour, "number": index+1}
        for index, day in enumerate(days)
    ]
    return {"count": len(days), "amount": amount,
            "last_amount": last_amount, "final_date": days[-1],
            "requested_final_date": final_date, "hour": hour,
            "total": balance, "entries": entries}


def for_mandate(mandate):
    snapshot = mandate.terms_snapshot or {}
    end = snapshot.get("requested_final_date")
    if not end:
        # Existing agreements were signed without an end-date promise.
        return []
    try:
        return calculate(mandate.maximum_total, mandate.first_due_date,
                         end, mandate.frequency, snapshot.get("collection_hour", 9))["entries"]
    except ValidationError:
        return []
