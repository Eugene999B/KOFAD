"""Small authenticated JSON interfaces. All money-changing methods are POST+CSRF."""
import hmac
import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from marketplace import services as market_services
from marketplace.models import OnlineOrder, VerifiedMomoPhone
from . import service
from .models import DirectTransaction


@csrf_exempt
@require_POST
def internal_reconcile(request):
    """Only a shared-secret worker call can trigger web-egress Hubtel GET checks.

    The Railway communications worker may NOT have the three whitelisted web
    static IPs; the web service alone must contact the Hubtel status endpoints.
    """
    expected = str(getattr(settings, "HUBTEL_DIRECT_RECONCILE_TOKEN", ""))
    supplied = str(request.headers.get("X-Kofad-Direct-Reconcile", ""))
    if len(expected) < 48 or not hmac.compare_digest(expected, supplied):
        response = JsonResponse({"error": "Forbidden"}, status=403)
        response["Cache-Control"] = "no-store"
        return response
    from .service import reconcile_due
    count = reconcile_due(limit=10)
    # Direct POS payments must also use this web service's whitelisted IPs,
    # never kofad-sms worker's unlisted egress.
    from core.pos_hubtel import reconcile_due as reconcile_pos_due
    pos_count = reconcile_pos_due(limit=10, include_direct=True)
    response = JsonResponse({"checked": count, "pos_checked": pos_count})
    response["Cache-Control"] = "no-store"
    return response


def _json(request):
    if request.content_type != "application/json" or len(request.body) > 4096:
        raise ValidationError("Send a small JSON object.")
    try:
        result = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("Invalid JSON.") from exc
    if not isinstance(result, dict):
        raise ValidationError("Expected a JSON object.")
    return result


def _answer(tx, code=200):
    response = JsonResponse({
        "reference": tx.client_reference, "kind": tx.kind, "status": tx.status,
        "amount": str(tx.amount), "currency": "GHS",
        "next_check_after_seconds": 300 if tx.status in service.ACTIVE else None,
    }, status=code)
    response["Cache-Control"] = "no-store"
    return response


def _error(exc, status=400):
    response = JsonResponse({"error": str(exc.messages[0] if isinstance(exc, ValidationError) else exc)}, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _customer(request, order_id):
    customer = market_services.customer_from_session(request)
    if not customer or not customer.active:
        raise PermissionDenied("Customer sign-in required.")
    order = get_object_or_404(OnlineOrder, pk=order_id, customer=customer)
    return customer, order


def _finance_staff(request, approval=False):
    user = request.user
    permission = "core.manage_company" if approval else "core.operate_finance"
    if not user.is_active or not user.has_perm(permission):
        raise PermissionDenied("Finance authorization is required.")
    if not user.is_superuser and not user.access.branches.filter(active=True).exists():
        raise PermissionDenied("No active company location is assigned.")
    # Explicit MFA for transfers even if legacy staff MFA enforcement is off.
    stamp = request.session.get("mfa_verified_at")
    try:
        valid = float(stamp) + settings.MFA_SESSION_SECONDS > timezone.now().timestamp()
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise PermissionDenied("Recent staff MFA is required for money transfers.")
    return user


@login_required
@require_POST
def staff_wallet_name(request):
    """Protected cashier name lookup; no payment is initiated by this endpoint."""
    try:
        from core import services as core_services
        from core.views import branch_for
        from . import verification
        branch = branch_for(request)
        core_services.permit(request.user, branch, "operate_sales")
        data = _json(request)
        if set(data) != {"phone", "channel", "customer_authorized"} or data["customer_authorized"] is not True:
            raise ValidationError("Confirm that the customer authorized verification of their wallet.")
        verification.staff_throttle(request.user.pk, data.get("phone"))
        result = verification.inquiry(data["phone"], data["channel"])
        proof = signing.dumps(
            {"user": request.user.pk, "branch": branch.pk, "phone": result["phone"],
             "channel": result["channel"], "name": result["wallet_name"]},
            salt="kofad-hubtel-staff-wallet-verify-v1",
        )
        core_services.audit(
            request.user, branch, "hubtel.wallet_verified", "wallet",
            {"phone_suffix": result["phone"][-4:], "channel": result["channel"]},
            category="finance",
        )
        response = JsonResponse({"name": result["wallet_name"], "network": result["channel"],
                                 "phone_suffix": result["phone"][-4:], "verification_token": proof})
        response["Cache-Control"] = "private, no-store"
        return response
    except PermissionDenied as exc:
        return _error(exc, 403)
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(exc)


@require_POST
def verify_registered_phone(request, order_id):
    """SMS OTP proves control of the customer's SAVED phone (never a typed number)."""
    try:
        customer, _ = _customer(request, order_id)
        if not customer.verified_at or not customer.phone or not settings.CUSTOMER_OTP_ENABLED:
            raise ValidationError("Verified account phone and enabled SMS verification are required.")
        body = _json(request)
        action = body.get("action")
        from core.identity import normalize_ghana_phone
        phone = normalize_ghana_phone(customer.phone)
        if action == "send" and set(body) == {"action"}:
            market_services.send_otp(phone, "momo", request=request)
            result = {"message": "Verification SMS sent to your registered account number."}
        elif action == "verify" and set(body) == {"action", "code"}:
            market_services.verify_otp(phone, body["code"], "momo")
            VerifiedMomoPhone.objects.get_or_create(customer=customer, phone=phone)
            result = {"verified": True}
        else:
            raise ValidationError("Invalid phone verification action.")
        response = JsonResponse(result)
        response["Cache-Control"] = "private, no-store"
        return response
    except PermissionDenied as exc:
        return _error(exc, 403)
    except ValidationError as exc:
        return _error(exc)


@require_POST
def collect(request, order_id):
    try:
        customer, order = _customer(request, order_id)
        body = _json(request)
        if set(body) != {"channel"}:
            raise ValidationError("Only a Mobile Money channel may be selected.")
        tx = service.create_collection(customer, order.pk, body["channel"])
        return _answer(tx, 202)
    except PermissionDenied as exc:
        return _error(exc, 403)
    except ValidationError as exc:
        return _error(exc)


@require_GET
def collect_status(request, order_id):
    try:
        _, order = _customer(request, order_id)
    except PermissionDenied:
        return _error(PermissionDenied("Customer sign-in required."), 403)
    tx = DirectTransaction.objects.filter(order=order, kind="receive").order_by("-created_at").first()
    if not tx:
        raise Http404("No direct payment for this order.")
    return _answer(tx)


@csrf_exempt
@require_POST
def callback(request):
    """An untrusted hint only. No callback may authorize a payout or mark paid."""
    if len(request.body) > 8192:
        return _error(ValidationError("Payload too large."), 413)
    try:
        data = _json(request)
    except ValidationError as exc:
        return _error(exc)
    service.note_callback(data)
    response = JsonResponse({"received": True})
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_GET
def finance_dashboard(request):
    from django.shortcuts import render
    try:
        manager = request.user.has_perm("core.manage_company")
        _finance_staff(request, approval=manager)
    except PermissionDenied as exc:
        return _error(exc, 403)
    qs = DirectTransaction.objects.filter(
        kind__in=["send_momo", "send_bank"]
    ).select_related("requested_by", "approved_by")
    if not manager:
        qs = qs.filter(requested_by=request.user)
    from .client import BANK_CODES, enabled
    response = render(request, "hubtel_direct/finance_dashboard.html", {
        "title": "Hubtel Direct transfers",
        "direct_transactions": qs.order_by("-created_at")[:80],
        "can_approve": manager,
        "wallet_enabled": enabled("send_momo"),
        "bank_enabled": enabled("send_bank"),
        "receive_enabled": enabled("receive"),
        "available_banks": sorted(BANK_CODES),
    })
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def payout(request):
    try:
        user = _finance_staff(request)
        body = _json(request)
        if set(body) - {"kind", "amount", "destination", "recipient_name", "channel", "bank_code", "purpose"}:
            raise ValidationError("Unsupported payout fields.")
        tx = service.create_payout(
            user, body.get("kind"), body.get("amount"), body.get("destination"),
            body.get("recipient_name"), body.get("channel", ""), body.get("bank_code", ""),
            idempotency_key=request.headers.get("Idempotency-Key"), purpose=body.get("purpose", ""),
        )
        return _answer(tx, 201)
    except PermissionDenied as exc:
        return _error(exc, 403)
    except (ValidationError, TypeError) as exc:
        return _error(exc)


@login_required
@require_POST
def payout_approve(request, tx_id):
    try:
        user = _finance_staff(request, approval=True)
        get_object_or_404(DirectTransaction, pk=tx_id, kind__in=["send_momo", "send_bank"])
        tx = service.approve_payout(tx_id, user)
        return _answer(tx, 202)
    except PermissionDenied as exc:
        return _error(exc, 403)
    except ValidationError as exc:
        return _error(exc)


@login_required
@require_GET
def payout_detail(request, tx_id):
    try:
        _finance_staff(request, approval=True)
    except PermissionDenied as exc:
        return _error(exc, 403)
    tx = get_object_or_404(DirectTransaction, pk=tx_id, kind__in=["send_momo", "send_bank"])
    return _answer(tx)
