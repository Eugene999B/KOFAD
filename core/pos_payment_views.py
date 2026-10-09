"""Staff MoMo payment tracking. Browser state never settles a transaction.

Paystack does not expose a documented Ghana MoMo payer-wallet name lookup.
The recipient review below shows the trusted KOFAD customer record, or the
cashier-entered name, clearly distinguishing it from a network-verified name.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.core.paginator import Paginator

from . import pos_paystack, services
from .identity import normalize_ghana_phone
from .models import HeldSale, Party

TOKEN_SALT = "kofad.pos.momo.recipient.v1"
TOKEN_AGE_SECONDS = 300


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
            "success": "Paid and posted",
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
        "currency": "GHS",
        "phone": str(state.get("phone") or ""),
        "network": str(state.get("network") or "").upper(),
        "customer_name": party_name or str(customer.get("customer_name") or "Walk-in customer"),
        "cashier": held.user.get_full_name() or held.user.get_username(),
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


def validate_review_token(token, *, user, branch, key, phone, provider, sale):
    """Bind approval to staff, sale, amount and recipient: never authorize changed details."""
    if not isinstance(sale, dict):
        raise ValidationError("Invalid sale request.")
    amount_pesewas = int(pos_paystack._payment_amount(sale) * 100)
    party_id = str(sale.get("party") or "")
    customer_name = "" if party_id else str(sale.get("customer_name") or "").strip()[:140]
    try:
        data = signing.loads(token or "", salt=TOKEN_SALT, max_age=TOKEN_AGE_SECONDS)
        if not isinstance(data, dict) or data != {
            "user": user.pk, "branch": branch.pk, "key": str(key),
            "phone": normalize_ghana_phone(phone), "network": str(provider or "").lower(),
            "amount_pesewas": amount_pesewas, "party": party_id,
            "customer_name": customer_name,
        }:
            raise ValidationError("Payment details changed. Review the recipient and amount again.")
    except (signing.BadSignature, ValueError, TypeError) as exc:
        raise ValidationError("Recipient review expired. Verify the customer details again.") from exc


@login_required
@require_POST
def recipient_review(request):
    """Confirmation of *customer-record details*, not a wallet-name lookup."""
    import json
    branch = _branch(request)
    services.permit(request.user, branch, "operate_sales")
    if len(request.body) > 2048:
        return JsonResponse({"error": "Invalid recipient review."}, status=400)
    try:
        body = json.loads(request.body or "{}")
        if not isinstance(body, dict):
            raise ValidationError("Invalid recipient details.")
        key = str(body.get("request_key") or "")
        pos_paystack._reference_from_key(key)
        phone = normalize_ghana_phone(body.get("phone"))
        network = str(body.get("provider") or "").lower()
        if network not in pos_paystack.PROVIDERS:
            raise ValidationError("Choose a supported MoMo network.")
        name = str(body.get("name") or "").strip()[:140]
        party_id = body.get("party")
        amount_pesewas = body.get("amount_pesewas")
        if type(amount_pesewas) is not int or not 0 < amount_pesewas <= 100000000000:
            raise ValidationError("Confirm a positive full-sale amount before reviewing payment.")
        party_phone = ""
        if party_id:
            party = Party.objects.filter(pk=party_id, branch=branch, kind="customer").first()
            if not party:
                raise ValidationError("Choose an existing customer in this branch.")
            name = party.name
            party_phone = party.phone or ""
        if len(name) < 2:
            raise ValidationError("Enter or choose the customer name first.")
        token = signing.dumps({
            "user": request.user.pk, "branch": branch.pk, "key": key,
            "phone": phone, "network": network,
            "amount_pesewas": amount_pesewas,
            "party": str(party_id or ""),
            "customer_name": "" if party_id else name,
        }, salt=TOKEN_SALT)
        response = JsonResponse({
            "customer_name": name, "phone": phone, "network": network.upper(),
            "amount": f"{amount_pesewas / 100:.2f}",
            "phone_matches_record": bool(party_phone and party_phone == phone) if party_id else None,
            "registered_wallet_name": None,
            "name_source": "KOFAD customer record" if party_id else "Cashier entry",
            "wallet_name_verified": False,
            "notice": "Paystack has not provided a registered MoMo-wallet name. Confirm these details directly with the customer before sending a prompt.",
            "review_token": token,
        })
        response["Cache-Control"] = "no-store, private"
        return response
    except (ValidationError, ValueError, TypeError) as exc:
        return JsonResponse({"error": "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Invalid review request."}, status=400)


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
