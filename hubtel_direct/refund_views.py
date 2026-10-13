"""Staff refund review, two-person authorization and untrusted Hubtel callback."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.views import branch_for
from marketplace.models import MarketReturnRequest
from . import refunds
from .models import HubtelRefundCase
from .views import _finance_staff


@login_required
@require_POST
def prepare(request, return_id):
    branch = branch_for(request)
    item = get_object_or_404(
        MarketReturnRequest, pk=return_id, order__branch=branch,
    )
    try:
        row = refunds.prepare_case(request.user, item, branch=branch)
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
    else:
        if row.status == "awaiting_order_id":
            messages.warning(request, "Refund review opened. The verified original Hubtel Order ID is missing; no refund can be sent until reconciled.")
        else:
            messages.success(request, "Hubtel refund case prepared for independent management approval.")
    return redirect("staff_market_return", pk=return_id)


@login_required
@require_POST
def approve(request, return_id):
    branch = branch_for(request)
    _finance_staff(request, approval=True)
    row = get_object_or_404(
        HubtelRefundCase, return_request_id=return_id,
        return_request__order__branch=branch,
    )
    try:
        updated = refunds.submit_approved_case(request.user, row.pk, branch=branch)
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
    else:
        if updated.status == "provider_pending":
            messages.success(request, "Hubtel accepted the refund request; awaiting final provider reconciliation.")
        else:
            messages.warning(request, "The submission outcome is uncertain. Do not try the refund again; check Hubtel with the original Order ID.")
    return redirect("staff_market_return", pk=return_id)


@csrf_exempt
@require_POST
def callback(request):
    # Hubtel does not document a signed callback in the provided reference.
    # This endpoint is advisory only: NEVER mark a refund successful from it.
    if len(request.body) > 8192 or request.content_type != "application/json":
        return JsonResponse({"error": "Invalid callback"}, status=400)
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    refunds.note_callback(payload)
    response = JsonResponse({"received": True})
    response["Cache-Control"] = "no-store"
    return response
