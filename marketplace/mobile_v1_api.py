"""KOFAD Market versioned mobile API: public, bounded and server-authoritative.

Mobile account login, cart mutations and payments deliberately remain unavailable
until an independently reviewed PKCE-based native identity/session flow is added.
These are *not* replicas of the protected website endpoints.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.html import strip_tags
from django.views.decorators.http import require_GET

from core.models import Stock
from . import services
from .models import MarketListing
from .native_api import NATIVE_ORIGINS
from .pricing import online_markup_percent


def _public_json(request, payload, *, max_age=30):
    response = JsonResponse(payload)
    response["Cache-Control"] = f"public, max-age={max_age}"
    response["X-Content-Type-Options"] = "nosniff"
    response["Vary"] = "Origin"
    origin = request.headers.get("Origin", "").strip()
    if origin in NATIVE_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Methods"] = "GET"
        response["Access-Control-Max-Age"] = "300"
    return response


@require_GET
def bootstrap(request):
    """A truthful public capabilities contract, not a signed-in user profile."""
    return _public_json(request, {
        "version": 1,
        "channel": "customer",
        "currency": "GHS",
        "features": {
            "guest_catalog": True,
            "product_details": True,
            "device_local_favorites": True,
            "mobile_account_session": False,
            "mobile_cart": False,
            "mobile_checkout": False,
            "background_push": False,
        },
        "routes": {
            "catalog": "/market/app/catalog.json",
            "product_detail_pattern": "/market/mobile/v1/products/{id}/",
            "browser_sign_in": "/market/access/",
        },
    }, max_age=300)


@require_GET
def product_detail(request, pk):
    """Single enabled listing only; no supplier cost or internal stock quantity."""
    listing = get_object_or_404(
        MarketListing.objects.select_related("product"),
        pk=pk, enabled=True, product__active=True,
    )
    try:
        branch = services.market_branch()
    except ValidationError:
        branch = None
    quantity = 0
    if branch:
        quantity = Stock.objects.filter(branch=branch, product_id=listing.product_id) \
            .values_list("quantity", flat=True).first() or 0
    listing._online_price_percent = online_markup_percent()
    price = listing.market_price.quantize(Decimal("0.01"))
    gallery = []
    for photo in listing.gallery_images.order_by("sort_order", "pk")[:8]:
        if photo.image_data or photo.image_thumb:
            gallery.append({
                "image_path": f"/market/gallery/{photo.pk}/image/thumb/",
                "alt": str(photo.alt_text or listing.display_name)[:160],
            })
    tags = [tag.strip()[:45] for tag in (listing.tags or "").split(",") if tag.strip()][:12]
    highlights = [
        value.strip()[:130]
        for value in (listing.highlights or [])[:8]
        if isinstance(value, str) and value.strip()
    ] if isinstance(listing.highlights, list) else []
    return _public_json(request, {
        "version": 1,
        "product": {
            "id": listing.pk,
            "name": listing.display_name,
            "description": " ".join(strip_tags(listing.description or "").split())[:1600],
            "category": listing.product.category,
            "price": str(price),
            "currency": "GHS",
            "selling_unit": listing.selling_label,
            "image_path": f"/market/products/{listing.pk}/image/thumb/" if listing.image_thumb else "",
            "gallery": gallery,
            "tags": tags,
            "highlights": highlights,
            "featured": listing.featured,
            "in_stock_snapshot": int(quantity) >= max(listing.factor, 1),
            "availability_note": "Final price and stock are checked by KOFAD during checkout.",
        },
    })
