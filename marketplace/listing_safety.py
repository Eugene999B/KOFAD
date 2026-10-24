"""Protect customers from accidentally ordering unreconciled showcase listings.

A SHOW- product code is a review signal rather than permission to delete
historical stock/accounting records. Owners can verify specific SKUs via an
explicit configured allowlist once physical stock, sourcing and prices are
reconciled; new genuine SKUs are unaffected.
"""
from django.conf import settings
from django.core.exceptions import ValidationError


def requires_review(listing):
    sku = str(getattr(getattr(listing, "product", None), "sku", "") or "").strip().upper()
    if not sku.startswith("SHOW-"):
        return False
    if not getattr(settings, "KOFAD_REQUIRE_SHOWCASE_VERIFICATION", True):
        return False
    verified = getattr(settings, "KOFAD_VERIFIED_SHOWCASE_SKUS", ())
    return sku not in {str(item).strip().upper() for item in verified}


def ensure_orderable(listing):
    if requires_review(listing):
        raise ValidationError(
            "This product is awaiting KOFAD's confirmation of stock and price. "
            "Please contact customer care; no payment is required."
        )
