import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

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
    consent_requested = any(payload.get(key) is True for key in ("customer_consent", "send_sms", "send_whatsapp"))

    submitted_email = str(payload.get("customer_email", "")).strip().lower()
    if submitted_email:
        validate_email(submitted_email)
    credit_choice = payload.get("customer_debt_email_opt_in")
    if credit_choice is not None and not isinstance(credit_choice, bool):
        raise ValidationError("Invalid debt email preference.")
    if credit_choice is True and not submitted_email:
        raise ValidationError("Enter the customer email before enabling debt messages.")

    if payload.get("party"):
        party = Party.objects.filter(pk=payload["party"], branch=branch, kind="customer").first()
        if not party:
            raise ValidationError("Choose a valid customer at this location.")
        updates = []
        if consent_requested and not party.consent:
            party.consent = True
            updates.append("consent")
            audit(user, branch, "customer.messaging_consent_enabled_at_checkout", party.pk)
        if credit_choice is not None:
            if submitted_email and party.email and party.email.casefold() != submitted_email:
                raise ValidationError("This customer has a different saved email. Update their account details before sending private debt notices.")
            if submitted_email and not party.email:
                party.email = submitted_email
                updates.append("email")
            if party.debt_email_opt_in != credit_choice:
                party.debt_email_opt_in = credit_choice
                updates.append("debt_email_opt_in")
                audit(user, branch, "customer.debt_email_preference_at_checkout", party.pk, {"enabled": credit_choice})
        elif submitted_email and not party.email:
            party.email = submitted_email
            updates.append("email")
        if updates:
            party.save(update_fields=updates)
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
    # Never silently link a typed name to a debtor or any existing customer.
    # Cashiers must select the existing record via its ID, after confirming
    # identity. A matching name with a changed phone is also not new.
    from .customer_guard import assert_unique_customer
    assert_unique_customer(branch, name, canonical)

    party = Party.objects.create(
        branch=branch,
        kind="customer",
        name=name,
        phone=canonical,
        email=submitted_email,
        debt_email_opt_in=credit_choice is True,
        consent=consent_requested,
    )
    audit(user, branch, "customer.created_at_checkout", party.pk, {
        "name": party.name,
        "phone": party.phone,
        "messaging_consent": party.consent,
    })
    return party
