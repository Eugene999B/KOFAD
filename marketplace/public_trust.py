"""Public contact hygiene without rewriting company settings or inventing details.

The owner must enter a real working phone and branded email in Company Settings.
This only prevents duplicate or clearly placeholder contact numbers appearing as
multiple independent channels on the customer website.
"""
from types import SimpleNamespace


def usable_phone(value):
    raw = str(value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return raw if 9 <= len(digits) <= 15 and not digits.endswith("000000") else ""


def public_contacts(company):
    phone = usable_phone(company.phone)
    secondary = usable_phone(company.secondary_phone)
    whatsapp = usable_phone(company.whatsapp_phone)
    if secondary and "".join(ch for ch in secondary if ch.isdigit()) == "".join(ch for ch in phone if ch.isdigit()):
        secondary = ""
    return SimpleNamespace(
        phone=phone, secondary_phone=secondary, whatsapp_phone=whatsapp,
        email=str(company.email or "").strip(), address=str(company.address or "").strip(),
        phone_needs_review=bool(company.phone and not phone),
        contact_review_needed=bool(company.phone and not phone or
                                   company.secondary_phone and not secondary),
    )
