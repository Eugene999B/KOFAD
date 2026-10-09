"""Staff MoMo payment tracking. Browser state never settles a transaction.

Paystack does not expose a documented Ghana MoMo payer-wallet name lookup.
The recipient review below shows the trusted KOFAD customer record, or the
cashier-entered name, clearly distinguishing it from a network-verified name.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.core.paginator import Paginator

from . import pos_paystack, services
from .models import HeldSale, Party


def _branch(request):
    from .views import branch_for
    return branch_for(request)


def _manager(request, branch):
    if not request.user.has_perm("core.manage_company"):
        return False
    services.permit(request.user, branch, "manage_company")
    return True


def _accessible(request, branch):
    if not _manager(request, branch):
        services.permit(request.user, branch, "operate_sales")


def _payment(request, branch, reference):
    _accessible(request, branch)
    if not reference.startswith("KFD-POS-"):
        raise Http404("Unknown Paystack payment.")
    queryset = HeldSale.objects.filter(branch=branch, label=pos_paystack.LABEL_PREFIX + reference)
    if not _manager(request, branch):
        queryset = queryset.filter(user=request.user)
    return get_object_or_404(queryset.select_related("user", "branch"))


def _snapshot(held):
    data = held.cart if isinstance(held.cart, dict) else {}
    state = data.get("payment_request") or {}
    if not isinstance(state, dict):
        state = {}
    customer = data.get("sale_payload") or {}
    if not isinstance(customer, dict):
        customer = {}
    party_name = ""
    if customer.get("party"):
        party = Party.objects.filter(pk=customer.get("party"), branch=held.branch, kind="customer").first()
        if party:
            party_name = party.name
    doc = None
    if state.get("status") == "success" and state.get("document_id"):
        doc = pos_paystack.Document.objects.filter(pk=state["document_id"], branch=held.branch).first()
    status = str(state.get("status") or "pending").lower()
    return {
        "reference": str(state.get("reference") or held.label[len(pos_paystack.LABEL_PREFIX):]),
        "status": status,
        "display_status": {
            "success": "Deposit verified — debt recorded" if (
                str(state.get("balance_due") or "0.00") not in {"0", "0.0", "0.00", ""}
            ) else "Paid and posted",
            "pending": "Awaiting approval",
            "initializing": "Preparing request",
            "submission_unknown": "Checking request",
            "not_confirmed": "Awaiting final verification",
            "attention": "Manager review needed",
            "failed": "Failed",
            "abandoned": "Abandoned",
            "reversed": "Reversed",
        }.get(status, status.replace("_", " ").title()),
        "message": str(state.get("message") or "")[:240],
        "amount": str(state.get("amount") or "0.00"),
        "sale_total": str(state.get("sale_total") or state.get("amount") or "0.00"),
        "balance_due": str(state.get("balance_due") or "0.00"),
        "due_date": str(state.get("due_date") or ""),
        "currency": "GHS",
        "phone": str(state.get("phone") or ""),
        "network": str(state.get("network") or "").upper(),
        "customer_name": str(state.get("customer_name") or party_name or customer.get("customer_name") or "Walk-in customer"),
        "cashier": str(state.get("cashier_name") or held.user.get_full_name() or held.user.get_username()),
        "created_at": held.created_at,
        "check_count": int(state.get("check_count") or 0),
        "provider_status": str(state.get("provider_status") or ""),
        "transaction_id": str(state.get("transaction_id") or ""),
        "verified_at": state.get("verified_at"),
        "paid": bool(doc and status == "success"),
        "pending": status in pos_paystack.PENDING_STATES,
        "attention": status == "attention",
        "needs_otp": bool(status in pos_paystack.PENDING_STATES and state.get("charge_status") == "send_otp"),
        "receipt": (f"/documents/{doc.pk}/" if doc else ""),
        "receipt_thermal": (f"/documents/{doc.pk}/pdf/thermal80/" if doc else ""),
        "receipt_a4": (f"/documents/{doc.pk}/pdf/a4/" if doc else ""),
    }


@login_required
@require_GET
def payments_history(request):
    branch = _branch(request)
    manager = _manager(request, branch)
    if not manager:
        services.permit(request.user, branch, "operate_sales")
    rows = HeldSale.objects.filter(branch=branch, label__startswith=pos_paystack.LABEL_PREFIX).select_related("user")
    if not manager:
        rows = rows.filter(user=request.user)
    term = (request.GET.get("q") or "").strip()[:80]
    status = (request.GET.get("status") or "").strip()[:24]
    if term:
        rows = rows.filter(Q(label__icontains=term) |
                           Q(cart__payment_request__phone__icontains=term) |
                           Q(cart__sale_payload__customer_name__icontains=term))
    if status == "paid":
        rows = rows.filter(cart__payment_request__status="success")
    elif status == "pending":
        rows = rows.filter(cart__payment_request__status__in=list(pos_paystack.PENDING_STATES))
    elif status == "review":
        rows = rows.filter(cart__payment_request__status="attention")
    elif status == "failed":
        rows = rows.filter(cart__payment_request__status__in=list(pos_paystack.TERMINAL_FAILURES))
    page = Paginator(rows.order_by("-created_at"), 25).get_page(request.GET.get("page"))
    page.object_list = [{"held": held, "payment": _snapshot(held)} for held in page.object_list]
    result = render(request, "pos_momo_history.html", {
        "title": "MoMo payments", "page_obj": page, "q": term,
        "selected_status": status, "manager": manager,
    })
    result["Cache-Control"] = "private, no-store"
    return result


@login_required
@require_GET
def payment_detail(request, reference):
    branch = _branch(request)
    held = _payment(request, branch, reference)
    snapshot = _snapshot(held)
    if request.GET.get("format") == "json":
        data = {k: v for k, v in snapshot.items() if k != "created_at"}
        response = JsonResponse(data)
    else:
        response = render(request, "pos_momo_detail.html", {
            "title": "MoMo payment status", "payment": snapshot, "held": held,
        })
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def manual_verify(request, reference):
    branch = _branch(request)
    held = _payment(request, branch, reference)
    state = pos_paystack._state(held)
    if state.get("status") in pos_paystack.PENDING_STATES:
        try:
            pos_paystack.reconcile(reference, force=True)
        except pos_paystack.ProviderPending:
            messages.info(request, "Paystack has not confirmed payment yet. The background worker will keep checking.")
        except ValidationError:
            messages.warning(request, "The payment was not confirmed. Check the current status below.")
    elif state.get("status") == "attention":
        messages.warning(request, "This payment requires management reconciliation; it cannot be approved manually.")
    elif state.get("status") in pos_paystack.TERMINAL_FAILURES:
        messages.warning(request, "Paystack reported an unsuccessful transaction; no sale was posted.")
    else:
        messages.info(request, "This payment has already been confirmed and posted.")
    return redirect("pos_momo_payment", reference=reference)
