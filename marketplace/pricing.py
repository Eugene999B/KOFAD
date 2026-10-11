"""One source of truth for all-inclusive KOFAD online product pricing.

Retail prices and staff cash sales stay unchanged. The company-managed
percentage is folded into every *online* unit price before displaying it.
Existing order and provider amounts remain frozen at creation/request time.
"""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError

CENTS = Decimal("0.01")
MAX_RATE = Decimal("100.000")
RATE_PATTERN = re.compile(r"(?:[0-9]{1,3}(?:\.[0-9]{1,3})?|\.[0-9]{1,3})\Z")


def parse_rate(value):
    """Only ASCII digits and one dot; no signs, whitespace, commas, or exponent."""
    raw = str(value)
    if not RATE_PATTERN.fullmatch(raw):
        raise ValidationError("Enter numbers and at most one decimal point, e.g. 2 or 1.95.")
    try:
        rate = Decimal(raw)
    except InvalidOperation as exc:
        raise ValidationError("Enter a valid percentage.") from exc
    if not rate.is_finite() or not (Decimal("0") <= rate <= MAX_RATE):
        raise ValidationError("The percentage must be between 0 and 100.")
    return rate.quantize(Decimal("0.001"))


def online_markup_percent():
    """Database is authoritative: changes affect all web workers immediately."""
    from .models import PaymentConfiguration
    return PaymentConfiguration.objects.filter(pk=1).values_list(
        "online_price_markup_percent", flat=True
    ).first() or Decimal("0")


def all_in_unit_price(base, rate=None):
    """Round at the UNIT boundary, before multiplying by quantity."""
    amount = Decimal(str(base))
    if amount < 0 or not amount.is_finite():
        raise ValidationError("Invalid product price.")
    if rate is None:
        rate = online_markup_percent()
    rate = Decimal(str(rate))
    if not rate.is_finite() or rate < 0 or rate > MAX_RATE:
        raise ValidationError("Invalid online payment pricing configuration.")
    result = (amount * (Decimal("1") + rate / Decimal("100"))).quantize(
        CENTS, rounding=ROUND_HALF_UP,
    )
    if result > Decimal("999999999999.99"):
        raise ValidationError("The calculated product price is too large.")
    return result
