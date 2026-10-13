"""Consent-first customer debt mandates; no customer MoMo PIN is ever requested."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from core import services as core_services
from core.views import branch_for
from marketplace import services as sms_service
from . import client, debit_api, mandates, schedule
from .models import DebtMandate


@login_required
@require_POST
def staff_create_mandate(request):
    branch = branch_for(request)
    try:
        amount = request.POST.get("amount")
        ceiling = request.POST.get("maximum")
        mandate = mandates.create_plan(
            request.user, branch, party_id=request.POST.get("party"),
            phone=request.POST.get("phone"), channel=request.POST.get("channel"),
            frequency=request.POST.get("frequency"),
            first_due_date=request.POST.get("first_due_date"),
            amount_per_period=amount, maximum_total=ceiling,
            final_due_date=request.POST.get("final_due_date") or None,
            collection_hour=request.POST.get("collection_hour") or None,
            source_document_id=request.POST.get("source_document_id") or None,
            allow_one_partial_retry=request.POST.get("partial_retry") == "yes",
            minimum_partial=request.POST.get("minimum_partial") or "0.00",
        )
        messages.success(request, "Repayment schedule prepared. Send the customer their Hubtel approval link.")
        return redirect("hubtel_debt_staff_status", mandate_id=mandate.pk)
    except (ValidationError, ValueError, TypeError) as exc:
        messages.error(request, str(exc))
        return redirect("/debts/?customer=" + str(request.POST.get("party") or ""))



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
        )
        messages.success(
            request,
            "Draft terms updated. The wallet holder must approve the new schedule.",
        )
    except (ValidationError, ValueError, TypeError) as exc:
        messages.error(request, str(exc))
    return redirect("/debts/?customer=" + str(mandate.party_id))


def _staff_mandate(request, mandate_id):
    branch = branch_for(request)
    core_services.permit(
        request.user, branch,
        "operate_finance" if request.user.has_perm("core.operate_finance") else "operate_sales",
    )
    return get_object_or_404(
        DebtMandate.objects.select_related("branch", "party"),
        pk=mandate_id, branch=branch,
    )


@login_required
@require_GET
def staff_status(request, mandate_id):
    mandate = _staff_mandate(request, mandate_id)
    return render(request, "hubtel_direct/staff_debt_waiting.html", {
        "title": "Direct Debit authorization",
        "mandate": mandate,
        "schedule": schedule.for_mandate(mandate),
        "balance": mandates.outstanding(mandate.party),
        "authorization_url": request.build_absolute_uri(reverse(
            "hubtel_debt_authorize", kwargs={"mandate_id": mandate.pk},
        )),
        "hubtel_ready": bool(
            getattr(__import__("django.conf", fromlist=["settings"]).settings,
                    "HUBTEL_DEBT_PREAPPROVAL_ENABLED", False)
        ),
    })


@login_required
@require_GET
def staff_status_json(request, mandate_id):
    mandate = _staff_mandate(request, mandate_id)
    response = JsonResponse({
        "status": mandate.status, "label": mandate.get_status_display(),
        "verification_type": mandate.verification_type,
        "approved": mandate.status == "approved",
        "provider_checked": bool(mandate.last_provider_check_at),
    })
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def staff_check_approval(request, mandate_id):
    mandate = _staff_mandate(request, mandate_id)
    if mandate.status == "pending":
        try:
            mandates.refresh_preapproval(mandate)
            messages.success(request, "Checked directly with Hubtel.")
        except (ValidationError, client.StatusUnconfirmed) as exc:
            messages.error(request, str(exc))
    return redirect("hubtel_debt_staff_status", mandate_id=mandate_id)


@login_required
@require_POST
def staff_send_schedule(request, mandate_id):
    mandate = _staff_mandate(request, mandate_id)
    if mandate.status not in {"draft", "pending"}:
        messages.error(request, "Only agreements awaiting approval can be sent.")
        return redirect("hubtel_debt_staff_status", mandate_id=mandate_id)
    if not mandate.party.consent:
        messages.error(request, "Confirm customer permission to receive agreement details first.")
        return redirect("hubtel_debt_staff_status", mandate_id=mandate_id)
    entries = schedule.for_mandate(mandate)
    if not entries:
        messages.error(request, "The signed schedule is not available.")
        return redirect("hubtel_debt_staff_status", mandate_id=mandate_id)
    url = request.build_absolute_uri(reverse(
        "hubtel_debt_authorize", kwargs={"mandate_id": mandate.pk},
    ))
    body = (
        f"KOFAD repayment approval: GHS {mandate.maximum_total:.2f} "
        f"in {len(entries)} {mandate.frequency} payment(s), "
        f"first {entries[0]['date']:%d %b %Y}, "
        f"last {entries[-1]['date']:%d %b %Y}, "
        f"not before {entries[0]['hour']:02d}:00 Ghana time. "
        f"Installments GHS {mandate.amount_per_period:.2f}; "
        f"final GHS {entries[-1]['amount']:.2f}. "
        "Review dates and approve securely through Hubtel here: " + url
        + " Never share your MoMo PIN."
    )
    result = sms_service.send_transactional_sms(mandates.mandate_phone(mandate), body)
    if result in {"accepted", "delivered", "sending", "simulated"}:
        messages.success(request, "Agreement invitation sent to the customer's wallet number.")
    else:
        messages.warning(request, "SMS not confirmed. Copy the approval link and share it with the customer.")
    return redirect("hubtel_debt_staff_status", mandate_id=mandate_id)



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
        "schedule": schedule.for_mandate(mandate),
        "hubtel_ready": bool(getattr(
            __import__("django.conf", fromlist=["settings"]).settings,
            "HUBTEL_DEBT_PREAPPROVAL_ENABLED", False,
        )),
    
    })


@require_POST
def customer_authorize_action(request, mandate_id):
    mandate = _safe_mandate(mandate_id)
    action = request.POST.get("action")
    if action not in {"start", "send", "confirm", "hubtel_otp", "check"}:
        messages.error(request, "Choose an authorization action.")
        return redirect("hubtel_debt_authorize", mandate_id=mandate.pk)
    try:
        if action in {"start", "send", "confirm"} and mandate.status != "draft":
            raise ValidationError("This repayment agreement has already been submitted for approval.")
        if action in {"start", "confirm"}:
            if request.POST.get("accepted_terms") != "yes":
                raise ValidationError("Review and accept the exact repayment dates, amounts and cancellation terms.")
            if mandates.outstanding(mandate.party) <= 0:
                raise ValidationError("No unpaid debt remains to authorize.")
            # Hubtel itself verifies wallet ownership with a USSD prompt or
            # provider OTP. No KOFAD SMS code is misrepresented as approval.
            result = mandates.prepare_customer_authorization(mandate)
            if result["verification_type"] == "OTP":
                messages.success(request, "Hubtel sent a verification code to your wallet. Enter it below.")
            else:
                messages.success(request, "Hubtel has requested wallet approval. Check your phone's USSD prompt.")
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
