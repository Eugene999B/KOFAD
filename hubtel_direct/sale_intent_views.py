"""Staff and customer endpoints for consented pre-sale Direct Debit V2."""
import json
import logging
import re
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core import services
from core.views import branch_for
from . import client, debit_api, sale_intents
from .models import DirectDebitSaleIntent

logger = logging.getLogger(__name__)


def _error(exc, status=400):
    message = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
    response = JsonResponse({"error": str(message), "sale_issued": False,
                             "wallet_approved": False}, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _staff_intent(request, intent_id):
    branch = branch_for(request)
    services.permit(request.user, branch, "operate_sales")
    return get_object_or_404(
        DirectDebitSaleIntent.objects.select_related("document", "party"),
        pk=intent_id, branch=branch,
    )


@login_required
@require_POST
def prepare_sale(request):
    """Never posts a sale before the independent provider approval."""
    try:
        branch = branch_for(request)
        services.permit(request.user, branch, "operate_sales")
        if request.content_type != "application/json":
            raise ValidationError("Direct Debit requires a JSON sale intent.")
        body = json.loads(request.body)
        key = request.headers.get("Idempotency-Key")
        intent, created = sale_intents.prepare_intent(request.user, branch, body, key)
        if created:
            intent = sale_intents.begin_preapproval(intent)
        url = reverse("hubtel_v2_staff_wait", kwargs={"intent_id": intent.pk})
        response = JsonResponse({
            "intent_id": str(intent.pk), "status": intent.status,
            "sale_issued": intent.status == "issued",
            "wallet_approved": bool(intent.provider_approved_at),
            "staff_url": url,
            "message": (
                "Hubtel accepted the wallet preapproval request. The customer must complete USSD/OTP approval."
                if intent.status == "pending" else
                "The Hubtel authorization outcome must be checked; no sale was issued."
                if intent.status in {"unknown", "sending"} else
                "Hubtel rejected the wallet preapproval. The sale has NOT been issued."
                if intent.status == "rejected" else
                "Existing Direct Debit request resumed. No duplicate sale or wallet request was sent."
            ),
        }, status=202)
        response["Cache-Control"] = "no-store"
        return response
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _error(exc)


@login_required
@require_GET
def staff_wait(request, intent_id):
    intent = _staff_intent(request, intent_id)
    return render(request, "hubtel_direct/sale_intent_wait.html", {
        "intent": intent, "customer_url": reverse("hubtel_v2_customer_wait", kwargs={"intent_id": intent.pk}),
        "schedule": (intent.terms_snapshot or {}).get("schedule") or [],
    })


@login_required
@require_GET
def staff_status_json(request, intent_id):
    intent = _staff_intent(request, intent_id)
    r = JsonResponse({
        "status": intent.status, "provider_approved": bool(intent.provider_approved_at),
        "sale_issued": intent.status == "issued" and bool(intent.document_id),
        "document_id": str(intent.document_id or ""),
        "mandate_id": str(intent.mandate_id or ""),
        "provider_checked_at": intent.last_provider_check_at.isoformat() if intent.last_provider_check_at else None,
        "message": intent.last_error or "",
    })
    r["Cache-Control"] = "private, no-store"
    return r


@login_required
@require_POST
def staff_check(request, intent_id):
    intent = _staff_intent(request, intent_id)
    # Rate limit manual GETs; the worker also checks every five minutes.
    if not cache.add("hubtel-v2-check:" + str(intent.pk), True, timeout=25):
        return redirect("hubtel_v2_staff_wait", intent_id=intent.pk)
    try:
        sale_intents.reconcile_one(intent, force=True)
    except (ValidationError, client.StatusUnconfirmed) as exc:
        DirectDebitSaleIntent.objects.filter(pk=intent.pk).update(
            last_error=("Provider status unavailable; original request remains unchanged. " + str(exc))[:240],
        )
    return redirect("hubtel_v2_staff_wait", intent_id=intent.pk)


@require_GET
def customer_wait(request, intent_id):
    # UUID is a non-guessable document capability. Show no personal identifying
    # details beyond the last four wallet digits; never reveal stored sale JSON.
    intent = get_object_or_404(DirectDebitSaleIntent, pk=intent_id)
    return render(request, "hubtel_direct/sale_intent_customer.html", {
        "intent": intent,
        "schedule": (intent.terms_snapshot or {}).get("schedule") or [],
    })


@require_POST
def customer_otp(request, intent_id):
    intent = get_object_or_404(DirectDebitSaleIntent, pk=intent_id)
    if intent.status != "pending" or intent.verification_type != "OTP" or not intent.hubtel_preapproval_id:
        return redirect("hubtel_v2_customer_wait", intent_id=intent.pk)
    code = (request.POST.get("code") or "").strip()
    if not re.fullmatch(r"[0-9]{4}", code):
        return render(request, "hubtel_direct/sale_intent_customer.html", {
            "intent": intent, "schedule": (intent.terms_snapshot or {}).get("schedule") or [],
            "error": "Enter only the four digits sent by Hubtel; never enter your MoMo PIN.",
        }, status=400)
    key = "hubtel-v2-otp:" + str(intent.pk)
    if not cache.add(key, 1, timeout=3600):
        try:
            count = cache.incr(key)
        except ValueError:
            count = 10
        if count > 5:
            return HttpResponse("Too many attempts. Contact the KOFAD team.", status=429)
    try:
        debit_api.verify_preapproval_otp(
            phone=client.unseal(intent.phone_encrypted),
            provider_id=intent.hubtel_preapproval_id,
            client_reference=intent.client_reference,
            otp=intent.otp_prefix + "-" + code,
        )
        # A verified OTP response still says PENDING; independent status
        # verification is the only route to APPROVED.
        sale_intents.reconcile_one(intent, force=True)
    except (ValidationError, client.StatusUnconfirmed, client.SubmissionUnknown) as exc:
        logger.warning("V2 Hubtel OTP unresolved for intent %s: %s", intent.pk, type(exc).__name__)
        return render(request, "hubtel_direct/sale_intent_customer.html", {
            "intent": intent, "schedule": (intent.terms_snapshot or {}).get("schedule") or [],
            "error": "Hubtel has not confirmed approval. Do not repeat the request; check the same authorization status.",
        }, status=400)
    return redirect("hubtel_v2_customer_wait", intent_id=intent.pk)


@csrf_exempt
@require_POST
def provider_callback(request):
    """Callback is only a hint. Never trust it to approve a wallet or issue sale."""
    if len(request.body) > 16384:
        return JsonResponse({"ok": False}, status=413)
    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"ok": False}, status=400)
    ref = str(data.get("ClientReferenceId") or data.get("clientReferenceId") or "")
    provider_id = str(data.get("HubtelPreapprovalId") or data.get("hubtelPreApprovalId") or "")
    row = DirectDebitSaleIntent.objects.filter(client_reference=ref).first()
    if row and row.status in {"sending", "pending", "unknown"} and (
            not row.hubtel_preapproval_id or row.hubtel_preapproval_id == provider_id):
        DirectDebitSaleIntent.objects.filter(pk=row.pk).update(
            callback_seen_at=timezone.now(), next_provider_check_at=timezone.now(),
        )
    return JsonResponse({"ok": True})
