"""Customer identity matching for branch-scoped POS and contact creation.

Never silently associate a cashier-typed name with a stored debtor merely
because the phone matches. An exact match is a *conflict requiring selection*,
not proof of identity. Do not merge old customer records automatically.
"""
from django.core.exceptions import ValidationError
from .models import Party
from .services import party_debt


def name_key(value):
    return " ".join(str(value or "").split()).casefold()


def customer_conflicts(branch, name, phone, *, exclude_pk=None):
    """Find any branch customer with the same canonical phone OR normalized name.

    Older customers may have numbers stored with spaces/brackets or inconsistent
    leading zeros. Check those too before allowing a new identity. We deliberately
    do not auto-merge, even when both fields match.
    """
    from .identity import normalize_ghana_phone

    name = str(name or "").strip()
    phone = str(phone or "").strip()
    canonical = normalize_ghana_phone(phone) if phone else ""
    normalized_name = name_key(name)
    if not normalized_name and not canonical:
        return []
    records = Party.objects.filter(branch=branch, kind="customer").only(
        "pk", "name", "phone", "email", "consent", "debt_email_opt_in",
    )
    if exclude_pk:
        records = records.exclude(pk=exclude_pk)
    matches = []
    # Streaming avoids loading the whole branch's contact register into memory.
    # New contacts are already serialized on the branch's financial row lock.
    for party in records.iterator(chunk_size=500):
        same_name = bool(normalized_name and name_key(party.name) == normalized_name)
        same_phone = False
        if canonical:
            try:
                same_phone = normalize_ghana_phone(party.phone) == canonical
            except ValidationError:
                pass  # Historical bad phone: do not exclude a same-name match.
        if not (same_phone or same_name):
            continue
        matches.append({
            "party": party,
            "phone_match": same_phone,
            "name_match": same_name,
            "debt": party_debt(party),
        })
        if len(matches) >= 20:  # Never return an unbounded list to the cashier.
            break
    return matches

def assert_unique_customer(branch, name, phone, *, exclude_pk=None):
    matches = customer_conflicts(branch, name, phone, exclude_pk=exclude_pk)
    if matches:
        first = matches[0]
        reason = ("phone number and name" if first["phone_match"] and first["name_match"]
                  else "phone number" if first["phone_match"] else "name")
        raise ValidationError(
            f"A saved customer already uses this {reason}. "
            "Search customers and select the existing record instead of creating a duplicate. "
            "If two people have the same name, ask an authorized manager to review the records."
        )
    return None
