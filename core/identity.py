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
    consent_requested = payload.get("customer_consent") is True

    if payload.get("party"):
        party = Party.objects.filter(pk=payload["party"], branch=branch, kind="customer").first()
        if not party:
            raise ValidationError("Choose a valid customer at this location.")
        if consent_requested and not party.consent:
            party.consent = True
            party.save(update_fields=["consent"])
            audit(user, branch, "customer.messaging_consent_enabled_at_checkout", party.pk)
        return party

    name = str(payload.get("customer_name", "")).strip()
    phone = str(payload.get("customer_phone", "")).strip()

    # A fully paid counter sale may remain a walk-in sale. Named customer
    # identity is required only when the cashier actually enters customer
    # details (or downstream credit/threshold policy requires a customer).
    if not name and not phone and not consent_requested:
        return None
    if len(name) < 2 or len(name) > 120:
        raise ValidationError("Enter the new customer's name.")
    canonical = normalize_ghana_phone(phone)
    existing = customer_by_phone(branch, canonical)
    if existing:
        if consent_requested and not existing.consent:
            existing.consent = True
            existing.save(update_fields=["consent"])
            audit(user, branch, "customer.messaging_consent_enabled_at_checkout", existing.pk)
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
        consent=consent_requested,
    )
    audit(user, branch, "customer.created_at_checkout", party.pk, {
        "name": party.name,
        "phone": party.phone,
        "messaging_consent": party.consent,
    })
    return party
