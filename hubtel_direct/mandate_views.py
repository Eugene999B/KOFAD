"""Consent-first customer debt mandates; no customer MoMo PIN is ever requested."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from core import services as core_services
from core.views import branch_for
from marketplace import services as sms_service
from . import client, debit_api, mandates
from .models import DebtMandate


@login_required
@require_POST
def staff_create_mandate(request):
    branch = branch_for(request)
    try:
        count = request.POST.get("installment_count") or None
        end_date = request.POST.get("final_due_date") or None
        amount = request.POST.get("amount") if count is None and not end_date else None
        ceiling = request.POST.get("maximum") if count is None and not end_date else None
        mandate = mandates.create_plan(
            request.user, branch, party_id=request.POST.get("party"),
            phone=request.POST.get("phone"), channel=request.POST.get("channel"),
            frequency=request.POST.get("frequency"),
            first_due_date=request.POST.get("first_due_date"),
            amount_per_period=amount, maximum_total=ceiling,
            installment_count=count, final_due_date=end_date,
            source_document_id=request.POST.get("source_document_id") or None,
            allow_one_partial_retry=request.POST.get("partial_retry") == "yes",
            minimum_partial=request.POST.get("minimum_partial") or "0.00",
        )
        messages.success(request, "Repayment schedule calculated. Customer wallet approval is still required.")
        return redirect("hubtel_debt_staff_status", mandate_id=mandate.pk)
    except (ValidationError, ValueError, TypeError) as exc:
        messages.error(request, str(exc))
        return redirect("/debts/?customer=" + str(request.POST.get("party") or ""))



@login_required
@require_GET
def staff_status(request, mandate_id):
    """Cashier's waiting screen. Staff cannot approve a customer's wallet."""
    branch = branch_for(request)
    core_services.permit(
        request.user, branch,
        "operate_finance" if request.user.has_perm("core.operate_finance") else "operate_sales",
    )
    mandate = get_object_or_404(
        DebtMandate.objects.select_related("party"), pk=mandate_id, branch=branch,
    )
    return render(request, "hubtel_direct/debt_staff_waiting.html", {
        "mandate": mandate,
        "real_balance": mandates.outstanding(mandate.party),
        "preapproval_active": bool(getattr(settings, "HUBTEL_DEBT_PREAPPROVAL_ENABLED", False)),
    })


@login_required
@require_GET
def staff_authorization_status_json(request, mandate_id):
    """Read persisted provider-verified state without creating payment traffic."""
    branch = branch_for(request)
    core_services.permit(
        request.user, branch,
        "operate_finance" if request.user.has_perm("core.operate_finance") else "operate_sales",
    )
    mandate = get_object_or_404(DebtMandate, pk=mandate_id, branch=branch)
    response = JsonResponse({
        "status": mandate.status,
        "status_label": mandate.get_status_display(),
        "provider_confirmed": mandate.status == "approved" and bool(mandate.provider_approved_at),
        "provider_checked_at": (mandate.last_provider_check_at.isoformat()
                                if mandate.last_provider_check_at else None),
    })
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def staff_request_prompt(request, mandate_id):
    """Send the REAL provider preapproval prompt; not a payment or KOFAD SMS.

    This can only initiate an independent Hubtel wallet approval. It cannot
    grant authority and cannot submit any debit. Pending is durably persisted
    before the provider POST to prevent replay on unknown outcomes.
    """
    branch = branch_for(request)
    core_services.permit(
        request.user, branch,
        "operate_finance" if request.user.has_perm("core.operate_finance") else "operate_sales",
    )
    mandate = get_object_or_404(DebtMandate, pk=mandate_id, branch=branch)
    try:
        if request.POST.get("customer_requested_prompt") != "yes":
            raise ValidationError("Confirm the customer asked for this Hubtel approval prompt.")
        if mandate.status != "draft":
            raise ValidationError("This wallet authorization has already been requested.")
        result = mandates.prepare_customer_authorization(mandate)
        if result["verification_type"] == "USSD":
            messages.success(request, "Hubtel approval prompt sent to the customer's MoMo phone. Waiting for the customer to approve.")
        else:
            messages.success(request, "Hubtel OTP preapproval initiated. Ask the customer to enter the Hubtel code on their secure approval page.")
    except (ValidationError, client.StatusUnconfirmed, client.SubmissionUnknown) as exc:
        messages.error(request, str(exc))
    return redirect("hubtel_debt_staff_status", mandate_id=mandate.pk)


@login_required
@require_POST
def staff_check_status(request, mandate_id):
    branch = branch_for(request)
    core_services.permit(
        request.user, branch,
        "operate_finance" if request.user.has_perm("core.operate_finance") else "operate_sales",
    )
    mandate = get_object_or_404(DebtMandate, pk=mandate_id, branch=branch)
    try:
        if mandate.status == "pending":
            mandates.refresh_preapproval(mandate)
            messages.success(request, "Approval status checked directly with Hubtel.")
        else:
            messages.info(request, "No pending provider approval to check.")
    except (ValidationError, client.StatusUnconfirmed) as exc:
        messages.error(request, str(exc))
    return redirect("hubtel_debt_staff_status", mandate_id=mandate.pk)


@login_required
@require_POST
def staff_send_agreement(request, mandate_id):
    """Send terms to the wallet holder. SMS is an invitation, NOT debit consent."""
    branch = branch_for(request)
    core_services.permit(
        request.user, branch,
        "operate_finance" if request.user.has_perm("core.operate_finance") else "operate_sales",
    )
    mandate = get_object_or_404(DebtMandate.objects.select_related("party"), pk=mandate_id, branch=branch)
    if mandate.status not in {"draft", "pending"}:
        messages.error(request, "This agreement is not awaiting customer authorization.")
        return redirect("hubtel_debt_staff_status", mandate_id=mandate.pk)
    if not mandate.party.consent:
        messages.error(request, "Customer permission to receive debt authorization messages is required.")
        return redirect("hubtel_debt_staff_status", mandate_id=mandate.pk)
    snapshot = mandate.terms_snapshot or {}
    plan = snapshot.get("schedule") or []
    if not plan:
        messages.error(request, "No dated schedule is available for this agreement.")
        return redirect("hubtel_debt_staff_status", mandate_id=mandate.pk)
    origin = str(settings.MARKET_SITE_ORIGIN).rstrip("/")
    link = origin + reverse("hubtel_debt_authorize", kwargs={"mandate_id": mandate.pk})
    body = (
        f"KOFAD Direct Debit proposal: GHS {mandate.maximum_total:.2f}, "
        f"{len(plan)} {mandate.frequency} collection(s), "
        f"first {plan[0]['date']} GHS {plan[0]['amount']}; "
        f"final {plan[-1]['date']} GHS {plan[-1]['amount']}. "
        f"Collection window: {snapshot.get('collection_window', 'see agreement')}. "
        f"See EVERY date and amount at {link}. "
        "This SMS is not authorization. You must separately approve Hubtel's USSD/OTP. "
        "Never share your MoMo PIN."
    )
    delivery = sms_service.send_transactional_sms(mandates.mandate_phone(mandate), body)
    if delivery in {"accepted", "delivered", "sending", "simulated"}:
        messages.success(request, "Agreement invitation submitted to the SMS provider. Wallet approval remains pending.")
    else:
        messages.warning(request, "Agreement SMS was not accepted. Share the approval link securely instead.")
    return redirect("hubtel_debt_staff_status", mandate_id=mandate.pk)


@login_required
@require_POST
def staff_edit_draft(request, mandate_id):
    """Edit a draft only; already presented/approved Hubtel terms are frozen."""
    branch = branch_for(request)
    mandate = get_object_or_404(
        DebtMandate, pk=mandate_id, branch=branch,
    )
    try:
        mandates.update_draft_plan(
            request.user, branch, mandate_id,
            phone=request.POST.get("phone"),
            channel=request.POST.get("channel"),
            frequency=request.POST.get("frequency"),
            first_due_date=request.POST.get("first_due_date"),
            amount_per_period=request.POST.get("amount"),
            maximum_total=request.POST.get("maximum"),
            installment_count=request.POST.get("installment_count") or None,
            final_due_date=request.POST.get("final_due_date") or None,
        )
        messages.success(
            request,
            "Draft terms updated. The wallet holder must approve the new schedule.",
        )
    except (ValidationError, ValueError, TypeError) as exc:
        messages.error(request, str(exc))
    return redirect("/debts/?customer=" + str(mandate.party_id))


def _safe_mandate(mandate_id):
    return get_object_or_404(
        DebtMandate.objects.select_related("party", "branch"), pk=mandate_id,
    )


@require_GET
def customer_authorize(request, mandate_id):
    mandate = _safe_mandate(mandate_id)
    # Random UUID is an unguessable bearer link, showing only the wallet suffix.
    # The actual wallet holder authorizes using Hubtel's USSD or provider OTP.
    return render(request, "hubtel_direct/debt_authorization.html", {
        "mandate": mandate,
        "debt_balance": mandates.outstanding(mandate.party),
        "payment_channel": "MTN MoMo" if mandate.channel == "mtn-gh-direct-debit" else "Telecel Cash",
    })


@require_POST
def customer_authorize_action(request, mandate_id):
    mandate = _safe_mandate(mandate_id)
    action = request.POST.get("action")
    if action not in {"start", "confirm", "hubtel_otp", "check"}:
        messages.error(request, "Choose an authorization action.")
        return redirect("hubtel_debt_authorize", mandate_id=mandate.pk)
    try:
        if action in {"start", "confirm"} and mandate.status != "draft":
            raise ValidationError("This repayment agreement has already been submitted for approval.")
        if action in {"start", "confirm"}:
            if request.POST.get("accepted_terms") != "yes":
                raise ValidationError("Read and accept the exact scheduled payment amounts, dates and cancellation terms.")
            if mandates.outstanding(mandate.party) <= 0:
                raise ValidationError("No debt remains to authorize.")
            # Hubtel itself verifies the wallet owner via USSD/OTP.
            # A KOFAD SMS code is never represented as payment authorization.
            result = mandates.prepare_customer_authorization(mandate)
            if result["verification_type"] == "OTP":
                messages.success(request, "Hubtel sent its approval code. Complete the Hubtel OTP step below.")
            else:
                messages.success(request, "Hubtel requested wallet approval. Please approve the USSD prompt on your phone.")
        elif action == "hubtel_otp":
            if mandate.status != "pending" or mandate.verification_type != "OTP":
                raise ValidationError("This mandate does not have a pending Hubtel OTP request.")
            raw = (request.POST.get("hubtel_code") or "").strip()
            code = mandate.otp_prefix + "-" + raw
            debit_api.verify_preapproval_otp(
                phone=mandates.mandate_phone(mandate),
                provider_id=mandate.hubtel_preapproval_id,
                client_reference=mandate.client_reference, otp=code,
            )
            messages.success(request, "Hubtel received the OTP. Awaiting independent authorization confirmation.")
        elif action == "check":
            mandates.refresh_preapproval(mandate)
            messages.info(request, "Updated from Hubtel's authorization-status endpoint.")
    except (ValidationError, client.StatusUnconfirmed, client.SubmissionUnknown) as exc:
        messages.error(request, str(exc))
    return redirect("hubtel_debt_authorize", mandate_id=mandate.pk)


@require_POST
def customer_cancel_action(request, mandate_id):
    """Customer can revoke a mandate with an SMS code sent to that same wallet."""
    mandate = _safe_mandate(mandate_id)
    try:
        action = request.POST.get("action")
        if action == "send":
            sms_service.send_otp(mandates.mandate_phone(mandate), "momo", request=request)
            messages.success(request, "Cancellation code sent to your registered wallet number.")
        elif action == "confirm":
            sms_service.verify_otp(
                mandates.mandate_phone(mandate),
                request.POST.get("code", ""), "momo",
            )
            mandates.request_cancel(mandate)
            messages.success(request, "All further debit attempts are blocked immediately. Cancellation at Hubtel is being confirmed.")
        else:
            raise ValidationError("Choose send or confirm cancellation.")
    except (ValidationError, client.StatusUnconfirmed) as exc:
        messages.error(request, str(exc))
    return redirect("hubtel_debt_authorize", mandate_id=mandate.pk)


@login_required
@require_POST
def staff_cancel(request, mandate_id):
    mandate = _safe_mandate(mandate_id)
    branch = branch_for(request)
    core_services.permit(request.user, branch, "operate_finance")
    if mandate.branch_id != branch.pk:
        raise ValidationError("Mandate belongs to another branch.")
    mandates.request_cancel(mandate)
    core_services.audit(request.user, branch, "hubtel_debt.revoke_requested", str(mandate.pk), {
        "customer": mandate.party_id, "by": request.user.pk,
    })
    messages.success(request, "Direct Debit disabled for this customer. Hubtel cancellation has been requested.")
    return redirect("/debts/?customer=" + str(mandate.party_id))


@csrf_exempt
@require_POST
def callback(request):
    """Hubtel webhook is advisory only; NEVER set APPROVED from inbound JSON."""
    # Intentionally ignore user-supplied statuses. Status polling on the
    # whitelisted web host verifies every consent and balance independently.
    from django.http import JsonResponse
    return JsonResponse({"accepted": True}, status=202)


def status_dashboard_rows(branch):
    return DebtMandate.objects.filter(branch=branch).select_related("party").order_by("-created_at")[:300]
