"""Customer identity matching for branch-scoped POS and contact creation.

Never silently associate a cashier-typed name with a stored debtor merely
because the phone matches. An exact match is a *conflict requiring selection*,
not proof of identity. Do not merge old customer records automatically.
"""
import re

from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import Party
from .services import party_debt


def name_key(value):
    return " ".join(str(value or "").split()).casefold()


def customer_conflicts(branch, name, phone, *, exclude_pk=None):
    """Find stored customers matching either canonical phone or exact folded name."""
    from .identity import normalize_ghana_phone, phone_variants

    name = str(name or "").strip()
    phone = str(phone or "").strip()
    variants = []
    if phone:
        variants = phone_variants(normalize_ghana_phone(phone))
    if not name and not variants:
        return []
    records = Party.objects.filter(branch=branch, kind="customer")
    if exclude_pk:
        records = records.exclude(pk=exclude_pk)
    candidates = records.filter(
        Q(phone__in=variants) | Q(name__iexact=name)
    ).order_by("pk")
    # Historical names may contain repeated spacing, so check those as well.
    # A bounded branch-level scan avoids hiding duplicates when old records
    # have inconsistent punctuation/spacing; used only on explicit creation.
    if name:
        normalized = name_key(name)
        nearby_ids = [
            p.pk for p in records.only("pk", "name")
            if name_key(p.name) == normalized
        ]
        if nearby_ids:
            candidates = records.filter(
                Q(pk__in=nearby_ids) | Q(phone__in=variants)
            ).order_by("pk")
    matches = []
    for party in candidates:
        same_phone = bool(variants and party.phone in variants)
        # Existing records might have arbitrary spacing around phone punctuation.
        if not same_phone and phone:
            try:
                same_phone = normalize_ghana_phone(party.phone) == normalize_ghana_phone(phone)
            except ValidationError:
                pass
        same_name = bool(name and name_key(party.name) == name_key(name))
        if not (same_phone or same_name):
            continue
        matches.append({
            "party": party,
            "phone_match": same_phone,
            "name_match": same_name,
            "debt": party_debt(party),
        })
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
