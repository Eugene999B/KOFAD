"""Public read-only catalog for locally bundled KOFAD native mobile clients.

No customer/account/payment information or staff data is exposed. Native
sessions are NOT shared with this API; checkout stays on the official Market
site where Django's existing controls apply.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from core.models import Stock
from .models import MarketListing
from . import services


# Capacitor's built-in secure local origins, not arbitrary websites.
NATIVE_ORIGINS = frozenset({
    "capacitor://localhost",  # iOS WKWebView
    "http://localhost",       # Android WebView
    "https://localhost",      # Explicit Android secure scheme, if configured
})


@require_GET
def public_native_catalog(request):
    origin = request.headers.get("Origin", "").strip()
    query = request.GET.get("q", "").strip()[:70]
    try:
        page = max(1, min(100, int(request.GET.get("page", "1"))))
    except (ValueError, TypeError):
        page = 1
    qs = (
        MarketListing.objects.filter(enabled=True, product__active=True)
        .select_related("product")
        .order_by("-featured", "sort_order", "product__name")
    )
    if query:
        qs = qs.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
            | Q(product__name__icontains=query) | Q(product__category__icontains=query)
        )
    # Limit both database work and mobile bandwidth. Public prices follow
    # exactly the same server pricing/online markup as the website.
    size = 20
    start = (page - 1) * size
    batch = list(qs[start:start + size + 1])
    has_more = len(batch) > size
    batch = batch[:size]

    try:
        branch = services.market_branch()
    except ValidationError:
        branch = None
    from .pricing import online_markup_percent
    rate = online_markup_percent()
    stock_by_product = {}
    if branch and batch:
        ids = [x.product_id for x in batch]
        stock_by_product = dict(
            Stock.objects.filter(branch=branch, product_id__in=ids)
            .values_list("product_id", "quantity")
        )
    items = []
    for listing in batch:
        listing._online_price_percent = rate
        # An informational snapshot, not a sellable-stock guarantee. Stock
        # is validated again by Django during actual cart/checkout operations.
        stock_units = max(int(stock_by_product.get(listing.product_id, 0)), 0)
        image = (
            f"/market/products/{listing.pk}/image/thumb/"
            if listing.image_thumb else ""
        )
        price = listing.market_price
        items.append({
            "id": listing.pk,
            "name": listing.display_name,
            "category": listing.product.category,
            "price": str(price.quantize(Decimal("0.01"))),
            "currency": "GHS",
            "selling_unit": listing.selling_label,
            "image_path": image,
            "product_path": f"/market/products/{listing.pk}/",
            "featured": listing.featured,
            "in_stock_snapshot": stock_units >= max(listing.factor, 1),
        })
    response = JsonResponse({
        "schema": 1, "page": page, "next_page": page + 1 if has_more else None,
        "items": items,
    })
    response["Cache-Control"] = "public, max-age=120"
    response["Vary"] = "Origin"
    response["X-Content-Type-Options"] = "nosniff"
    if origin in NATIVE_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Methods"] = "GET"
        response["Access-Control-Max-Age"] = "300"
    return response
