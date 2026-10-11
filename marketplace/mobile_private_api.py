"""Authenticated read-only KOFAD mobile order history and staff operation summaries.

The existing KOFAD database remains the sole order, stock, price and user authority.
These endpoints never accept browser cookies, modifications or anonymous requests.
"""
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods

from core.models import Stock
from marketplace.models import OnlineOrder
from .mobile_identity import _bearer_session, _preflight, _public_response, _request_allowed


def _prepare(request):
    if not settings.KOFAD_NATIVE_AUTH_ENABLED:
        return HttpResponse(status=404)
    if request.method == "OPTIONS":
        return _preflight(request)
    if not _request_allowed(request):
        return _public_response(request, {"error": "origin_not_allowed"}, 403)
    return None


def _order_summary(order):
    return {
        "id": str(order.pk),
        "reference": order.confirmed_reference or order.public_reference,
        "status": order.status,
        "payment_status": order.payment_status,
        "fulfilment": order.fulfilment,
        "total": str(order.total),
        "currency": "GHS",
        "created_at": order.created_at.isoformat(),
    }


@require_http_methods(["GET", "OPTIONS"])
def customer_orders(request):
    early = _prepare(request)
    if early is not None:
        return early
    session = _bearer_session(request, "customer")
    if session is None:
        return _public_response(request, {"error": "authentication_required"}, 401)
    rows = OnlineOrder.objects.filter(customer_id=session.customer_id).order_by("-created_at", "-pk")[:30]
    return _public_response(request, {
        "version": 1, "items": [_order_summary(row) for row in rows], "limit": 30,
    })


@require_http_methods(["GET", "OPTIONS"])
def customer_order_detail(request, order_id):
    early = _prepare(request)
    if early is not None:
        return early
    session = _bearer_session(request, "customer")
    if session is None:
        return _public_response(request, {"error": "authentication_required"}, 401)
    # Return generic 404 for another customer's order and for a missing ID.
    order = OnlineOrder.objects.filter(
        pk=order_id, customer_id=session.customer_id
    ).prefetch_related("lines").first()
    if order is None:
        return _public_response(request, {"error": "not_found"}, 404)
    return _public_response(request, {
        "version": 1,
        "order": {**_order_summary(order),
            "delivery_fee": str(order.delivery_fee),
            "subtotal": str(order.subtotal),
            "items": [{
                "name": row.description[:180],
                "quantity": row.quantity,
                "unit_price": str(row.unit_price),
                "total": str(row.total),
            } for row in order.lines.all()[:100]],
        },
    })


@require_http_methods(["GET", "OPTIONS"])
def staff_overview(request):
    early = _prepare(request)
    if early is not None:
        return early
    session = _bearer_session(request, "staff")
    if session is None:
        return _public_response(request, {"error": "authentication_required"}, 401)
    user = session.staff_user
    branch = session.branch
    modules = {}
    if user.has_perm("core.operate_inventory"):
        # The same branch limitation enforced by native auth is repeated here.
        stocks = Stock.objects.filter(branch_id=branch.pk).select_related("product").filter(product__active=True)
        low = sum(1 for s in stocks if s.quantity <= s.product.reorder_level)
        modules["inventory"] = {"low_stock_items": low}
    if user.has_perm("core.operate_sales") or user.has_perm("core.view_reports"):
        orders = OnlineOrder.objects.filter(branch_id=branch.pk)
        modules["orders"] = {
            "awaiting_payment": orders.filter(status="awaiting_payment").count(),
            "preparing": orders.filter(status="preparing").count(),
            "out_for_delivery": orders.filter(status="out_for_delivery").count(),
        }
    return _public_response(request, {
        "version": 1,
        "branch": {"id": branch.pk, "name": branch.name},
        "modules": modules,
    })
