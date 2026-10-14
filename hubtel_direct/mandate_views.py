"""Consent-first customer debt mandates; no customer MoMo PIN is ever requested."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
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
        amount = request.POST.get("amount") if count is None else None
        ceiling = request.POST.get("maximum") if count is None else None
        mandate = mandates.create_plan(
            request.user, branch, party_id=request.POST.get("party"),
            phone=request.POST.get("phone"), channel=request.POST.get("channel"),
            frequency=request.POST.get("frequency"),
            first_due_date=request.POST.get("first_due_date"),
            amount_per_period=amount, maximum_total=ceiling,
            installment_count=count,
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
    # Random UUID is a bearer access link, without showing the whole phone
    # or banking credentials. SMS ownership still required for any action.
    return render(request, "hubtel_direct/debt_authorization.html", {
        "mandate": mandate,
        "debt_balance": mandates.outstanding(mandate.party),
        "payment_channel": "MTN MoMo" if mandate.channel == "mtn-gh-direct-debit" else "Telecel Cash",
    })


@require_POST
def customer_authorize_action(request, mandate_id):
    mandate = _safe_mandate(mandate_id)
    action = request.POST.get("action")
    if action not in {"send", "confirm", "hubtel_otp", "check"}:
        messages.error(request, "Choose an authorization action.")
        return redirect("hubtel_debt_authorize", mandate_id=mandate.pk)
    try:
        if action in {"send", "confirm"} and mandate.status != "draft":
            raise ValidationError("This repayment agreement has already been submitted for approval.")
        if action == "send":
            if not mandates.outstanding(mandate.party) > 0:
                raise ValidationError("No debt remains to authorize.")
            sms_service.send_otp(mandates.mandate_phone(mandate), "momo", request=request)
            messages.success(request, "A six-digit ownership code was sent to the wallet being authorized.")
        elif action == "confirm":
            if request.POST.get("accepted_terms") != "yes":
                raise ValidationError("Read and accept the displayed payment schedule, limits and cancellation terms.")
            sms_service.verify_otp(
                mandates.mandate_phone(mandate), request.POST.get("code", ""),
                "momo",
            )
            result = mandates.prepare_customer_authorization(mandate)
            if result["verification_type"] == "OTP":
                messages.success(request, "Ownership confirmed. Enter the separate Hubtel preapproval OTP shown below.")
            else:
                messages.success(request, "Ownership confirmed. Approve Hubtel's preapproval on your mobile phone.")
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
