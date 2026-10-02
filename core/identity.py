import re

from django.core.exceptions import ValidationError

from .models import Party


def normalize_ghana_phone(value):
    """Return one canonical Ghana E.164-style phone number: +233 + 9 national digits."""
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("233"):
        national = digits[3:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        national = digits
    if len(national) != 9 or not national.isdigit():
        raise ValidationError(
            "Enter a Ghana number as 10 local digits beginning with 0, or the 9 digits after +233."
        )
    return "+233" + national


def phone_variants(value):
    canonical = normalize_ghana_phone(value)
    national = canonical[4:]
    return [canonical, "233" + national, "0" + national, national]


def customer_by_phone(branch, value):
    variants = phone_variants(value)
    return Party.objects.filter(branch=branch, kind="customer", phone__in=variants).order_by("pk").first()


def resolve_sale_customer(user, branch, payload, audit):
    """Select an existing customer or create/reuse one directly from checkout."""
    if payload.get("party"):
        party = Party.objects.filter(pk=payload["party"], branch=branch, kind="customer").first()
        if not party:
            raise ValidationError("Choose a valid customer at this location.")
        return party

    name = str(payload.get("customer_name", "")).strip()
    phone = str(payload.get("customer_phone", "")).strip()
    if len(name) < 2 or len(name) > 120:
        raise ValidationError("Enter the new customer's name.")
    canonical = normalize_ghana_phone(phone)
    existing = customer_by_phone(branch, canonical)
    if existing:
        audit(user, branch, "customer.reused_at_checkout", existing.pk, {
            "submitted_name": name,
            "phone": canonical,
        })
        return existing

    party = Party.objects.create(
        branch=branch,
        kind="customer",
        name=name,
        phone=canonical,
    )
    audit(user, branch, "customer.created_at_checkout", party.pk, {
        "name": party.name,
        "phone": party.phone,
    })
    return party
