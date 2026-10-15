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
    # Historical hosted Hubtel transactions are STILL outstanding records.
    # Only this whitelisted web service may check their status; the SMS
    # worker has no permission to use its own outbound IP for Hubtel.
    legacy_count = 0
    try:
        from marketplace.hubtel import reconcile_due as reconcile_legacy_market
        legacy_count = reconcile_legacy_market(limit=10) or 0
    except Exception:
        # Keep other independent status checks running during provider outages.
        pass
    # Direct POS payments must also use this web service's whitelisted IPs,
    # never kofad-sms worker's unlisted egress.
    from core.pos_hubtel import reconcile_due as reconcile_pos_due
    pos_count = reconcile_pos_due(limit=10, include_direct=True)
    # Reconcile V2 PRE-SALE customer wallet approvals. GET-only provider
    # checks are safe with real money collection switches kept OFF.
    from . import sale_intents as v2_sale_intents
    v2_checked = v2_sale_intents.reconcile_due(limit=8)
    debt_checked = 0
    debit_dispatched = 0
    debit_paid = 0
    debt_cancelled = 0
    # ALL provider HTTP from the allowlisted WEB service only. The SMS
    # worker triggers this authenticated endpoint; never calls Hubtel.
    from . import mandates as debt_mandates
    # Read-only verification and cancellation remain available when new
    # customer authorizations or outgoing debits are disabled. A safety
    # shutdown MUST NOT strand in-flight payments or wallet revocation.
    from .client import require_reconciliation_ready
    try:
        require_reconciliation_ready("receive")
    except ValidationError:
        pass
    else:
        if getattr(settings, "HUBTEL_DEBT_PREAPPROVAL_ENABLED", False):
            for mandate in debt_mandates.DebtMandate.objects.filter(
                status="pending",
            ).order_by("created_at")[:10]:
                try:
                    debt_mandates.refresh_preapproval(mandate)
                    debt_checked += 1
                except (ValidationError, Exception):
                    # Provider outages cannot interrupt regular payments.
                    pass
        try:
            debt_cancelled = debt_mandates.cancel_fully_paid(limit=25)
        except (ValidationError, Exception):
            pass
        try:
            debit_paid = debt_mandates.reconcile_attempts(limit=10)
        except (ValidationError, Exception):
            pass
    # An already VERIFIED paid debit is an accounting obligation even when
    # Hubtel is unavailable or outbound collections have been switched off.
    # Never issue a provider POST from this ledger-only recovery path.
    debt_ledger_recovered = 0
    try:
        debt_ledger_recovered = debt_mandates.post_verified_collections(limit=15)
    except Exception:
        # Preserve finance review records for investigation.
        pass
    if getattr(settings, "HUBTEL_DEBT_AUTO_DEBIT_ENABLED", False):
        try:
            debt_mandates.enqueue_due(limit=10)
            debit_dispatched = debt_mandates.dispatch_planned(limit=8)
        except (ValidationError, Exception):
            # Stop new requests without suppressing the verification above.
            pass
    payroll_paid = 0
    if getattr(settings, "HUBTEL_PAYROLL_DISBURSEMENTS_ENABLED", False):
        from .payroll import settle_confirmed
        payroll_paid = settle_confirmed(limit=20)
    response = JsonResponse({
        "checked": count, "legacy_checked": legacy_count, "pos_checked": pos_count,
        "v2_intents_checked": v2_checked, "preapprovals_checked": debt_checked, "debts_cancelled": debt_cancelled,
        "debit_submitted": debit_dispatched, "debit_settled": debit_paid,
        "debit_ledger_recovered": debt_ledger_recovered,
        "payroll_settled": payroll_paid,
    })
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
        "id": str(tx.pk), "reference": tx.client_reference, "kind": tx.kind, "status": tx.status,
        "amount": str(tx.amount), "currency": "GHS",
        "message": (
            "Awaiting approval by another authorized person. No money has been sent."
            if tx.status == "approval_pending" else
            "Transfer request is processing. Do not submit another transfer."
            if tx.status in service.ACTIVE else
            "Transfer verified and completed." if tx.status == "success" else
            "Transfer needs review. Do not submit it again." if tx.status == "attention" else
            "Transfer not completed."
        ),
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
        if set(data) != {"phone", "channel"}:
            raise ValidationError("Enter a Mobile Money number and choose a network.")
        from core.identity import normalize_ghana_phone
        from . import client as hubtel_client
        phone = normalize_ghana_phone(data.get("phone")).lstrip("+")
        if data.get("channel") not in hubtel_client.CHANNELS:
            raise ValidationError("Select MTN, Telecel or AirtelTigo.")
        verification.staff_throttle(request.user.pk, phone)
        result = verification.inquiry(phone, data["channel"])
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
    """A one-time SMS only for a NEW MoMo wallet on this customer's account."""
    try:
        customer, order = _customer(request, order_id)
        if order.status != "awaiting_payment" or order.payment_status in {"paid", "refunded"}:
            raise ValidationError("This order cannot accept a new payment request.")
        body = _json(request)
        if set(body) not in ({"action", "phone"}, {"action", "phone", "code"}, {"action", "phone", "channel"}):
            raise ValidationError("Enter the Mobile Money number and verification action.")
        from core.identity import normalize_ghana_phone
        phone = normalize_ghana_phone(body.get("phone"))
        action = body.get("action")
        already_verified = VerifiedMomoPhone.objects.filter(customer=customer, phone=phone).exists()
        if action == "name" and set(body) == {"action", "phone", "channel"}:
            if not already_verified:
                raise ValidationError("Verify your wallet number by SMS first.")
            from . import verification
            record = verification.inquiry(phone, body.get("channel"))
            result = {
                "verified": True, "registered_name": record["wallet_name"],
                "phone_suffix": record["phone"][-4:],
            }
            from django.utils import timezone as django_timezone
            request.session["checkout_wallet_verified:" + str(order.pk)] = {
                "phone": phone, "channel": body["channel"],
                "at": django_timezone.now().timestamp(),
            }
            request.session.modified = True
        elif action == "check" and set(body) == {"action", "phone"}:
            result = {"verified": already_verified,
                      "message": "Wallet already verified for this account." if already_verified
                                 else "Verify this wallet once using an SMS code."}
        elif already_verified and action in ("send", "verify"):
            result = {"verified": True, "message": "Wallet already verified for your account."}
        elif not settings.CUSTOMER_OTP_ENABLED:
            raise ValidationError("First-time wallet SMS verification is temporarily unavailable.")
        elif action == "send" and set(body) == {"action", "phone"}:
            market_services.send_otp(phone, "momo", request=request)
            result = {"message": "Six-digit verification code sent to the MoMo number you entered."}
        elif action == "verify" and set(body) == {"action", "phone", "code"}:
            market_services.verify_otp(phone, body["code"], "momo")
            VerifiedMomoPhone.objects.get_or_create(customer=customer, phone=phone)
            result = {"verified": True, "message": "Number verified. You can now request payment."}
        else:
            raise ValidationError("Enter the six-digit SMS verification code for this wallet.")
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
        if set(body) != {"channel", "phone", "customer_authorized"} or body.get("customer_authorized") is not True:
            raise ValidationError("Enter the MoMo number, choose its network and confirm this payment.")
        # Enforce name inquiry for this exact order + wallet. Frontend
        # confirmation alone is not authorization and cannot be forged.
        from core.identity import normalize_ghana_phone
        from django.utils import timezone as django_timezone
        verified = request.session.get("checkout_wallet_verified:" + str(order.pk), {})
        try:
            age = django_timezone.now().timestamp() - float(verified.get("at", 0))
        except (TypeError, ValueError, AttributeError):
            age = 999999
        if (not isinstance(verified, dict) or not 0 <= age <= 900
                or verified.get("phone") != normalize_ghana_phone(body["phone"])
                or verified.get("channel") != body["channel"]):
            raise ValidationError("Verify and confirm the registered wallet name before requesting payment.")
        tx = service.create_collection(customer, order.pk, body["channel"], body["phone"])
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


@require_GET
def collection_verification(request, order_id):
    """Separate customer-facing Direct Receive status. NEVER submits a new debit."""
    customer, order = _customer(request, order_id)
    tx = DirectTransaction.objects.filter(
        order=order, customer=customer, kind="receive"
    ).order_by("-created_at").first()
    if not tx:
        raise Http404("No payment request for this order.")
    status = tx.status
    paid = status == "success" and order.payment_status == "paid"
    review = status == "attention" or (status == "success" and not paid)
    data = {
        "reference": tx.client_reference,
        "status": "attention" if review else status,
        "paid": paid,
        "failed": status == "failed",
        "attention": review,
        "message": (
            "Payment independently confirmed and recorded."
            if paid else "This payment needs KOFAD review. Do not send another request."
            if review else "Payment was not completed. No payment was recorded."
            if status == "failed" else "Approve the original Mobile Money request on your phone."
        ),
    }
    if request.GET.get("format") == "json":
        response = JsonResponse(data)
    else:
        from django.shortcuts import render
        response = render(request, "hubtel_direct/collection_verification.html", {
            "order": order, "payment": tx, "journey": data,
        })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


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
    from .models import HubtelRefundCase
    refunds = HubtelRefundCase.objects.select_related(
        "return_request__order", "created_by", "approved_by",
    )
    if not manager:
        refunds = refunds.filter(created_by=request.user)
    response = render(request, "hubtel_direct/finance_dashboard.html", {
        "title": "Send money",
        "direct_transactions": qs.order_by("-created_at")[:80],
        "hubtel_refund_cases": refunds.order_by("-created_at")[:60],
        "refund_enabled": getattr(settings, "HUBTEL_REFUNDS_ENABLED", False),
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
def recipient_name(request):
    """Server-only Hubtel lookup; no payment is initiated."""
    try:
        user = _finance_staff(request)
        from . import verification
        body = _json(request)
        kind = body.get("kind")
        if kind == "momo" and set(body) == {"kind", "phone", "channel"}:
            verification.staff_throttle(user.pk, body["phone"])
            result = verification.inquiry(body["phone"], body["channel"])
            data = {"registered_name": result["wallet_name"],
                    "destination_suffix": result["phone"][-4:],
                    "channel": result["channel"], "verified": True}
        elif kind == "bank" and set(body) == {"kind", "bank_code", "account"}:
            verification.staff_throttle(user.pk, body["account"])
            result = verification.bank_inquiry(body["bank_code"], body["account"])
            data = {"registered_name": result["name"],
                    "destination_suffix": result["account_suffix"],
                    "bank_code": result["bank_code"], "verified": True}
        else:
            raise ValidationError("Choose MoMo network and wallet number, or a bank and account number.")
        # Trust the provider-backed result, not the client-supplied display name.
        # Proof is session-bound to this exact destination/network for 15 min.
        from django.utils import timezone as django_timezone
        from core.identity import normalize_ghana_phone
        request.session["finance_verified_recipient"] = {
            "kind": kind,
            "destination": normalize_ghana_phone(body["phone"]) if kind == "momo" else str(body["account"]).strip(),
            "network": body["channel"] if kind == "momo" else body["bank_code"],
            "registered_name": data["registered_name"],
            "at": django_timezone.now().timestamp(),
        }
        request.session.modified = True
        response = JsonResponse(data)
        response["Cache-Control"] = "private, no-store"
        return response
    except PermissionDenied as exc:
        return _error(exc, 403)
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(exc)


@login_required
@require_POST
def payout(request):
    try:
        user = _finance_staff(request)
        body = _json(request)
        if set(body) - {"kind", "amount", "destination", "recipient_name", "channel", "bank_code", "purpose"}:
            raise ValidationError("Unsupported payout fields.")
        # Refuse client-only "verified" flags or fabricated recipient names.
        # The server must have looked up the SAME account recently.
        from django.utils import timezone as django_timezone
        from core.identity import normalize_ghana_phone
        checked = request.session.get("finance_verified_recipient")
        if not isinstance(checked, dict):
            raise ValidationError("Verify the recipient's registered name before submitting.")
        kind = body.get("kind")
        expected_kind = "momo" if kind == "send_momo" else "bank" if kind == "send_bank" else ""
        destination = (
            normalize_ghana_phone(body.get("destination"))
            if expected_kind == "momo" else str(body.get("destination") or "").strip()
        )
        network = body.get("channel") if expected_kind == "momo" else body.get("bank_code")
        try:
            age = django_timezone.now().timestamp() - float(checked.get("at", 0))
        except (TypeError, ValueError):
            age = 999999
        if (not expected_kind or not 0 <= age <= 900
                or checked.get("kind") != expected_kind
                or checked.get("destination") != destination
                or checked.get("network") != network
                or str(checked.get("registered_name") or "").strip().casefold()
                   != str(body.get("recipient_name") or "").strip().casefold()):
            raise ValidationError("The verified recipient no longer matches this transfer. Verify the name again.")
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
        user = _finance_staff(request)
    except PermissionDenied as exc:
        return _error(exc, 403)
    tx = get_object_or_404(DirectTransaction, pk=tx_id, kind__in=["send_momo", "send_bank"])
    if tx.requested_by_id != user.pk and not user.has_perm("core.manage_company"):
        return _error(PermissionDenied("This transfer is not accessible."), 403)
    if request.GET.get("format") == "json":
        return _answer(tx)
    from django.shortcuts import render
    response = render(request, "hubtel_direct/transfer_verification.html", {
        "transfer": tx, "transfer_kind": "Mobile Money" if tx.kind == "send_momo" else "Bank transfer",
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
