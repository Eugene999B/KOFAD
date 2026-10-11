"""Customer-only, bearer-authenticated native shopping basket.

No orders, reservations, payment intents, browser cookies or financial writes.
Server-side public prices and availability are refreshed on every read. Native
authentication AND this separate feature gate must be enabled.
"""
import json
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.models import Stock
from . import services
from .mobile_identity import _bearer_session, _preflight, _public_response, _request_allowed
from .models import CustomerAccount, MarketListing, NativeCartItem, StockReservation
from .pricing import online_markup_percent

MAX_ROWS = 40
MAX_QUANTITY = 20
MAX_BODY_BYTES = 4096


def _reply(request, data, status=200):
    response = _public_response(request, data, status=status)
    response["Access-Control-Allow-Methods"] = "GET, PUT, OPTIONS"
    return response


def _parse_items(request):
    if not request.content_type.startswith("application/json") or len(request.body) > MAX_BODY_BYTES:
        return None
    try:
        payload = json.loads(request.body)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"items"}:
        return None
    items = payload["items"]
    if not isinstance(items, list) or len(items) > MAX_ROWS:
        return None
    normalized = {}
    for entry in items:
        if not isinstance(entry, dict) or set(entry) != {"id", "quantity"}:
            return None
        listing_id, quantity = entry["id"], entry["quantity"]
        # bool inherits from int in Python; it must never be an accepted product ID.
        if type(listing_id) is not int or type(quantity) is not int:
            return None
        if not 1 <= listing_id <= 2147483647 or not 1 <= quantity <= MAX_QUANTITY:
            return None
        if listing_id in normalized:
            return None
        normalized[listing_id] = quantity
    return normalized


def _items_for_customer(customer):
    entries = list(NativeCartItem.objects.filter(customer=customer)
                   .select_related("listing", "listing__product")
                   .order_by("pk")[:MAX_ROWS])
    active = [entry for entry in entries if entry.listing.enabled and entry.listing.product.active]
    products = {entry.listing.product_id for entry in active}
    try:
        branch = services.market_branch()
    except ValidationError:
        branch = None
    stock = {}
    reserved = {}
    if branch and products:
        stock = dict(Stock.objects.filter(branch=branch, product_id__in=products)
                     .values_list("product_id", "quantity"))
        reserved = dict(StockReservation.objects.filter(
            branch=branch, product_id__in=products, active=True,
            expires_at__gt=timezone.now(),
        ).values("product_id").annotate(total=Sum("units"))
                        .values_list("product_id", "total"))
    markup = online_markup_percent()
    subtotal = Decimal("0.00")
    items = []
    for entry in entries:
        listing = entry.listing
        enabled = listing.enabled and listing.product.active
        listing._online_price_percent = markup
        price = listing.market_price.quantize(Decimal("0.01")) if enabled else None
        available_units = max(0, int(stock.get(listing.product_id, 0)) -
                              int(reserved.get(listing.product_id, 0) or 0))
        available = enabled and price is not None and price > 0 and (
            available_units >= entry.quantity * max(listing.factor, 1)
        )
        line_total = price * entry.quantity if price is not None else None
        if line_total is not None:
            subtotal += line_total
        items.append({
            "id": listing.pk, "name": listing.display_name[:160],
            "quantity": entry.quantity,
            "selling_unit": listing.selling_label[:60] if enabled else "",
            "unit_price": str(price) if price is not None else None,
            "line_total": str(line_total) if line_total is not None else None,
            "available": bool(available),
            "active": bool(enabled),
            "image_path": (
                f"/market/products/{listing.pk}/image/thumb/"
                if enabled and listing.image_thumb else ""
            ),
        })
    return {
        "version": 1, "channel": "customer", "currency": "GHS",
        "items": items, "count": sum(item["quantity"] for item in items),
        "estimated_subtotal": str(subtotal.quantize(Decimal("0.01"))),
        "checkout_ready": False,
        "notice": "Basket estimates only; no stock reserved. Final prices, delivery and payment are checked at checkout.",
    }


@csrf_exempt
@require_http_methods(["GET", "PUT", "OPTIONS"])
def cart(request):
    if not (getattr(settings, "KOFAD_NATIVE_AUTH_ENABLED", False)
            and getattr(settings, "KOFAD_NATIVE_CART_ENABLED", False)):
        return HttpResponse(status=404)
    if request.method == "OPTIONS":
        # Only the bundled app origins are allowed to proceed through CORS.
        result = _preflight(request)
        result["Access-Control-Allow-Methods"] = "GET, PUT, OPTIONS"
        return result
    if not _request_allowed(request):
        return _reply(request, {"error": "origin_not_allowed"}, 403)
    session = _bearer_session(request, "customer")
    if session is None or not session.customer_id:
        return _reply(request, {"error": "authentication_required"}, 401)
    if request.method == "PUT":
        normalized = _parse_items(request)
        if normalized is None:
            return _reply(request, {"error": "invalid_cart", "max_items": MAX_ROWS,
                                    "max_quantity": MAX_QUANTITY}, 400)
        if normalized:
            allowed = set(MarketListing.objects.filter(
                pk__in=normalized, enabled=True, product__active=True,
            ).values_list("pk", flat=True))
            if allowed != set(normalized):
                return _reply(request, {"error": "listing_unavailable"}, 409)
        # Serialize concurrent replacement requests for the same customer,
        # instead of interleaving destructive edits from two app sessions.
        with transaction.atomic():
            CustomerAccount.objects.select_for_update().get(pk=session.customer_id)
            NativeCartItem.objects.filter(customer_id=session.customer_id).delete()
            NativeCartItem.objects.bulk_create([
                NativeCartItem(customer_id=session.customer_id,
                               listing_id=pk, quantity=quantity)
                for pk, quantity in normalized.items()
            ])
    return _reply(request, _items_for_customer(session.customer))
