"""Paystack direct Mobile Money requests for staff counter sales.

This module never marks a sale paid from a browser response or webhook payload alone.
Every successful sale is independently verified through Paystack's transaction verify API.
Pending requests are stored as system HeldSale records so background reconciliation survives
browser closure and app restarts without a new database table.
"""
import copy
import hashlib
import json
import logging
import re
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from . import services
from .payment_failure_guidance import explain_provider_error
from .identity import normalize_ghana_phone
from .models import Document, HeldSale, Idempotency, Message, Party

CHARGE_URL = "https://api.paystack.co/charge"
VERIFY_URL = "https://api.paystack.co/transaction/verify/"
LABEL_PREFIX = "Paystack MoMo "
PROVIDERS = {"mtn", "atl", "vod"}
TERMINAL_FAILURES = {"failed", "abandoned", "reversed"}
PENDING_STATES = {"initializing", "submission_unknown", "pending", "pay_offline", "processing", "ongoing", "not_confirmed"}

logger = logging.getLogger(__name__)


class ProviderPending(ValidationError):
    """The provider has not supplied a final answer yet."""


class _PreviewRollback(Exception):
    def __init__(self, total):
        self.total = total


def configured():
    return bool(settings.PAYSTACK_SECRET_KEY)


def ready():
    return configured() and settings.PAYSTACK_POS_MOMO_ENABLED


def _headers():
    if not configured():
        raise ValidationError("Paystack is not configured yet.")
    return {
        "Authorization": "Bearer " + settings.PAYSTACK_SECRET_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _label(reference):
    return LABEL_PREFIX + reference


def _reference_from_key(value):
    try:
        key = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("A valid sale request key is required.") from exc
    return "KFD-POS-" + key.hex[:20]


def _held(reference, *, lock=False):
    qs = HeldSale.objects.select_related("branch", "user")
    if lock:
        qs = qs.select_for_update()
    return qs.filter(label=_label(reference)).first()


def _state(held):
    cart = held.cart if isinstance(held.cart, dict) else {}
    state = cart.get("payment_request")
    return state if isinstance(state, dict) else {}


def _save_state(held, state):
    # A slow HTTP response must not overwrite a concurrent verified result.
    with transaction.atomic():
        current = HeldSale.objects.select_for_update().get(pk=held.pk)
        saved = _state(current)
        if saved.get("status") in {"success", "attention"} and state.get("status") != saved.get("status"):
            held.cart = current.cart
            return
        if int(saved.get("check_count") or 0) > int(state.get("check_count") or 0):
            held.cart = current.cart
            return
        cart = copy.deepcopy(current.cart if isinstance(current.cart, dict) else {})
        cart["payment_request"] = state
        current.cart = cart
        current.save(update_fields=["cart"])
        held.cart = cart


def _preview_total(user, branch, payload):
    """Run the real sale validation and roll it back before any money is requested."""
    preview_key = str(uuid.uuid4())
    try:
        with transaction.atomic():
            doc = services.post_trade(user, branch, copy.deepcopy(payload), preview_key, kind="sale")
            raise _PreviewRollback(doc.total)
    except _PreviewRollback as exc:
        return exc.total


def _payment_amount(payload):
    rows = payload.get("payments")
    if not isinstance(rows, list):
        raise ValidationError("Payment details are missing.")
    nonzero = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError("Invalid payment details.")
        try:
            amount = Decimal(str(row.get("amount", "0")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError("Invalid payment amount.") from exc
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValidationError("Enter a valid nonnegative payment amount with at most two decimal places.")
        if amount > 0:
            nonzero.append((str(row.get("method", "")), amount))
    if len(nonzero) != 1 or nonzero[0][0] != "momo":
        raise ValidationError("Direct MoMo requires one positive Mobile Money amount. Do not mix unverified payment methods.")
    return nonzero[0][1]


def _customer_email(branch, payload, supplied, phone):
    """Paystack requires an email, but many Ghana counter customers only have MoMo.

    Where no real customer email was given, use a stable *processor-only* alias.
    Never save this generated address to KOFAD's customer or ledger records,
    queue email notifications to it, or represent it as customer consent.
    """
    supplied = str(supplied or "").strip().lower()
    party = None
    if payload.get("party"):
        party = Party.objects.filter(pk=payload["party"], branch=branch, kind="customer").first()
        if not party:
            raise ValidationError("Choose a valid customer.")
    email = supplied or (party.email.strip().lower() if party and party.email else "") or str(
        payload.get("customer_email", "")
    ).strip().lower()
    if email:
        try:
            validate_email(email)
        except ValidationError as exc:
            raise ValidationError("Enter a valid customer email address, or leave the field blank.") from exc
        payload["customer_email"] = email
        return email
    # Paystack's integration guidance permits customer-specific merchant-domain
    # addresses when merchants collect a phone number but not an email.
    # Only use this in the Paystack API payload; the KOFAD sale keeps no email.
    unique_suffix = hashlib.sha256(("kofad-pos-momo:" + phone).encode()).hexdigest()[:24]
    return f"momo-{unique_suffix}@kofadimpex.com"


def _response_state(held):
    state = _state(held)
    document = None
    document_id = state.get("document_id")
    if document_id:
        document = Document.objects.select_related("party").filter(pk=document_id).first()
    result = {
        "reference": state.get("reference", ""),
        "provider": "paystack",
        "status": state.get("status", "pending"),
        "provider_status": state.get("provider_status", ""),
        "message": state.get("message", ""),
        "display_text": state.get("display_text", ""),
        "waiting": state.get("status") in PENDING_STATES,
        "paid": state.get("status") == "success",
        "failed": state.get("status") in TERMINAL_FAILURES,
        "attention": state.get("status") == "attention",
        "expires_at": state.get("expires_at"),
        "needs_otp": state.get("charge_status") == "send_otp" and state.get("status") in PENDING_STATES,
    }
    if document:
        result["sale"] = result_for_document(document)
    return result


def result_for_document(doc):
    party = doc.party
    message = None
    if party:
        message = Message.objects.filter(
            branch=doc.branch, party=party, channel="sms"
        ).filter(
            source_key__in=[f"auto:receipt:{doc.pk}", f"document:receipt:{doc.pk}"]
        ).order_by("-created_at").first()
    can_send_sms = bool(party and party.consent and settings.SMS_ENABLED)
    return {
        "url": f"/documents/{doc.pk}/",
        "document_id": str(doc.pk),
        "reference": doc.reference,
        "total": str(doc.total),
        "paid": str(doc.paid),
        "customer": (
            {"name": party.name, "phone": party.phone, "consent": party.consent}
            if party else None
        ),
        "can_send_sms": can_send_sms,
        "sms_requested": bool(message),
        "sms_status": message.status if message else "",
        "sms_message": message.last_error if message and message.last_error else "",
    }


def start(user, branch, sale_payload, request_key, phone, provider, email):
    if not isinstance(sale_payload, dict) or sale_payload.get("kind", "sale") != "sale":
        raise ValidationError("Invalid sale request.")
    provider = str(provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise ValidationError("Choose MTN, AT Money or Telecel.")
    phone = normalize_ghana_phone(phone)
    reference = _reference_from_key(request_key)

    services.permit(user, branch, "operate_sales")
    existing = _held(reference)
    if existing:
        if existing.branch_id != branch.pk or existing.user_id != user.pk:
            raise ValidationError("This payment request could not be found.")
        candidate = copy.deepcopy(sale_payload)
        _customer_email(branch, candidate, email, phone)
        if (existing.cart.get("sale_payload") != candidate or _state(existing).get("phone") != phone
                or _state(existing).get("network") != provider):
            raise ValidationError("Use the original sale details for this payment request.")
        return _response_state(existing)

    # Switching or temporarily disabling a gateway must not hide an existing
    # charged reference. New requests still require the POS gateway to be ready.
    if not ready():
        raise ValidationError("Paystack direct MoMo is awaiting activation.")
    payload = copy.deepcopy(sale_payload)
    email = _customer_email(branch, payload, email, phone)
    paid_amount = _payment_amount(payload)
    if paid_amount <= 0:
        raise ValidationError("The Mobile Money deposit must be greater than zero.")
    # Preview executes the real sale/credit-policy validation atomically and
    # rolls everything back. An unpaid remainder is never posted until Paystack
    # proves that this exact MoMo deposit was received.
    total = _preview_total(user, branch, payload)
    if total <= 0 or paid_amount > total:
        raise ValidationError("The MoMo payment cannot exceed the sale total.")
    if paid_amount < total and not (payload.get("party") or str(payload.get("customer_name") or "").strip()):
        raise ValidationError("Select a customer before recording the outstanding amount as debt.")

    party_name = ""
    if payload.get("party"):
        party = Party.objects.filter(pk=payload["party"], branch=branch, kind="customer").first()
        if party:
            party_name = party.name
    customer_name_at_request = (party_name or str(payload.get("customer_name") or "").strip() or "Walk-in customer")[:140]
    cashier_at_request = (user.get_full_name().strip() or user.get_username())[:140]

    now = timezone.now()
    state = {
        "provider": "paystack",
        "reference": reference,
        "status": "initializing",
        "provider_status": "",
        "message": "Creating Paystack MoMo request.",
        "display_text": "",
        "phone": phone,
        "network": provider,
        "customer_name": customer_name_at_request,
        "cashier_name": cashier_at_request,
        "email": email,
        "amount": str(paid_amount),
        "sale_total": str(total),
        "balance_due": str(total - paid_amount),
        "due_date": str(payload.get("due_date") or ""),
        "request_key": str(request_key),
        "created_at": now.timestamp(),
        "expires_at": (now + timedelta(seconds=180)).timestamp(),
        "next_check_at": (now + timedelta(seconds=8)).timestamp(),
        "check_count": 0,
        "document_id": "",
    }
    # Claim this payment reference with a separate unique database idempotency key.
    # This prevents two simultaneous cashier/browser requests from creating duplicate charges.
    claim_key = uuid.uuid5(uuid.NAMESPACE_URL, "kofad-pos-paystack:" + reference)
    fingerprint = hashlib.sha256(json.dumps({"sale": payload, "user": user.pk, "phone": phone, "network": provider}, sort_keys=True, default=str).encode()).hexdigest()
    with transaction.atomic():
        claim, created = Idempotency.objects.get_or_create(
            branch=branch,
            key=claim_key,
            defaults={"fingerprint": fingerprint},
        )
        if claim.fingerprint != fingerprint:
            raise ValidationError("This payment request conflicts with an existing request.")
        existing = _held(reference, lock=True)
        if existing:
            if existing.branch_id != branch.pk or existing.user_id != user.pk:
                raise ValidationError("This payment request could not be found.")
            return _response_state(existing)
        if not created:
            raise ProviderPending("This payment request is already being prepared. Please check its status.")
        held = HeldSale.objects.create(
            branch=branch,
            user=user,
            label=_label(reference),
            cart={"sale_payload": payload, "payment_request": state},
        )

    request_body = {
        "email": email,
        "amount": str(int(paid_amount * 100)),
        "currency": "GHS",
        "reference": reference,
        # Paystack's Ghana Mobile Money contract documents the local 0XXXXXXXXX format.
        "mobile_money": {"phone": "0" + phone[4:], "provider": provider},
        "metadata": {
            "source": "kofad_pos",
            "branch_id": branch.pk,
            "staff_id": user.pk,
        },
    }
    try:
        response = requests.post(
            CHARGE_URL,
            headers=_headers(),
            json=request_body,
            timeout=settings.PAYSTACK_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        state["status"] = "submission_unknown"
        state["provider_status"] = "unknown"
        state["message"] = "Paystack request outcome is unknown; KOFAD is verifying it automatically."
        state["next_check_at"] = (timezone.now() + timedelta(seconds=8)).timestamp()
        _save_state(held, state)
        logger.warning("Paystack POS charge outcome unknown ref_suffix=%s", reference[-6:])
        return _response_state(held)

    data = body.get("data") if isinstance(body, dict) else None
    provider_status = str(data.get("status", "")).strip().lower() if isinstance(data, dict) else ""
    response_reference = str(data.get("reference", "")).strip() if isinstance(data, dict) else ""
    if (
        not 200 <= response.status_code < 300
        or not isinstance(body, dict)
        or body.get("status") is not True
        or not isinstance(data, dict)
        or response_reference != reference
    ):
        state["status"] = "submission_unknown"
        state["provider_status"] = provider_status or "unknown"
        state["message"] = explain_provider_error(body.get("message") if isinstance(body, dict) else "")
        state["next_check_at"] = (timezone.now() + timedelta(seconds=20)).timestamp()
        _save_state(held, state)
        return _response_state(held)

    state["provider_status"] = provider_status
    state["charge_status"] = provider_status
    state["display_text"] = str(data.get("display_text", ""))[:240]
    state["message"] = state["display_text"] or "Approve the payment on the customer's phone."
    state["status"] = "pending" if provider_status not in TERMINAL_FAILURES else provider_status
    state["next_check_at"] = (timezone.now() + timedelta(seconds=8)).timestamp() if state["status"] == "pending" else None
    _save_state(held, state)

    if provider_status == "success":
        try:
            reconcile(reference, force=True)
        except ValidationError:
            pass
    held.refresh_from_db()
    return _response_state(held)


def verify(reference):
    if not re.fullmatch(r"KFD-POS-[0-9a-f]{20}", str(reference or "")):
        raise ValidationError("Invalid Paystack POS reference.")
    try:
        response = requests.get(
            VERIFY_URL + reference,
            headers=_headers(),
            timeout=settings.PAYSTACK_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ProviderPending("Paystack verification is temporarily unavailable.") from exc

    if not 200 <= response.status_code < 300 or not isinstance(body, dict) or body.get("status") is not True:
        raise ProviderPending("Paystack has not returned a final transaction yet.")
    data = body.get("data")
    if not isinstance(data, dict) or str(data.get("reference", "")).strip() != reference:
        raise ProviderPending("Paystack verification did not match the saved request.")
    return data


def _send_receipts(user_id, branch_id, document_id, send_sms, send_whatsapp):
    from django.contrib.auth.models import User
    from .models import Branch
    from .views import _receipt_sms_result, _receipt_whatsapp_result

    user = User.objects.filter(pk=user_id).first()
    branch = Branch.objects.filter(pk=branch_id).first()
    doc = Document.objects.select_related("party").filter(pk=document_id).first()
    if not user or not branch or not doc:
        return
    if send_sms:
        try:
            _receipt_sms_result(user, branch, doc)
        except Exception:
            logger.exception("POS Paystack receipt SMS failed document=%s", document_id)
    if send_whatsapp:
        try:
            _receipt_whatsapp_result(user, branch, doc)
        except Exception:
            logger.exception("POS Paystack WhatsApp receipt failed document=%s", document_id)


def _mark_attention(reference, message):
    with transaction.atomic():
        held = _held(reference, lock=True)
        if not held:
            return
        state = _state(held)
        if state.get("status") == "success":
            return
        state["status"] = "attention"
        state["message"] = str(message)[:240]
        state["next_check_at"] = None
        _save_state(held, state)


def finalize_verified(reference, verified):
    held = _held(reference)
    if not held:
        raise ValidationError("Unknown Paystack POS payment.")
    state = _state(held)
    if state.get("status") == "success" and state.get("document_id"):
        return Document.objects.select_related("party").get(pk=state["document_id"])

    provider_status = str(verified.get("status", "")).strip().lower()
    if provider_status != "success":
        raise ProviderPending("The MoMo payment is not confirmed yet.")
    try:
        provider_amount = Decimal(str(verified.get("amount")))
        expected_amount = int(Decimal(str(state.get("amount"))) * 100)
    except (TypeError, ValueError, InvalidOperation) as exc:
        _mark_attention(reference, "Paystack returned an invalid payment amount. Manager review is required.")
        raise ValidationError("Paystack returned an invalid payment amount.") from exc
    currency = str(verified.get("currency", "")).strip().upper()
    channel = str(verified.get("channel", "")).strip().lower()
    if ((settings.PAYSTACK_SECRET_KEY.startswith("sk_live_") and verified.get("domain") != "live")
            or not provider_amount.is_finite() or provider_amount != expected_amount
            or verified.get("reference") != reference or not verified.get("id")
            or currency != "GHS" or channel != "mobile_money"):
        message = "Verified Paystack details do not match this sale. Manager review is required."
        _mark_attention(reference, message)
        raise ValidationError(message)

    try:
        with transaction.atomic():
            held = _held(reference, lock=True)
            if not held:
                raise ValidationError("Unknown Paystack POS payment.")
            state = _state(held)
            if state.get("status") == "success" and state.get("document_id"):
                return Document.objects.select_related("party").get(pk=state["document_id"])

            cart = held.cart if isinstance(held.cart, dict) else {}
            payload = copy.deepcopy(cart.get("sale_payload"))
            if not isinstance(payload, dict):
                raise ValidationError("The saved sale payload is missing.")
            for payment in payload.get("payments", []):
                if isinstance(payment, dict) and payment.get("method") == "momo" and Decimal(str(payment.get("amount", "0"))) > 0:
                    payment["reference"] = reference

            doc = services.post_trade(
                held.user,
                held.branch,
                payload,
                state.get("request_key"),
                kind="sale",
            )
            expected_total = Decimal(str(state.get("sale_total", state["amount"])))
            expected_paid = Decimal(str(state["amount"]))
            if (doc.total != expected_total or doc.paid != expected_paid
                    or doc.total - doc.paid != expected_total - expected_paid):
                raise ValidationError("Sale total or paid amount changed after payment was requested.")
            if doc.paid < doc.total and (not doc.party_id or not doc.due_date):
                raise ValidationError("An unpaid balance must have a named debtor and a due date.")
            state["status"] = "success"
            state["provider_status"] = provider_status
            state["message"] = ("Deposit verified and sale posted with outstanding customer debt."
                                 if doc.paid < doc.total else
                                 "Payment verified by Paystack and sale posted.")
            state["verified_at"] = timezone.now().timestamp()
            state["next_check_at"] = None
            state["document_id"] = str(doc.pk)
            state["transaction_id"] = str(verified.get("id", ""))[:80]
            _save_state(held, state)

            send_sms = payload.get("send_sms", payload.get("customer_consent")) is True
            send_whatsapp = payload.get("send_whatsapp") is True
            transaction.on_commit(
                lambda: _send_receipts(
                    held.user_id, held.branch_id, doc.pk, send_sms, send_whatsapp
                )
            )
            services.audit(held.user, held.branch, "sale.paystack_momo_verified", doc.reference, {
                "payment_reference": reference,
                "amount": str(doc.paid),
                "sale_total": str(doc.total),
                "balance_due": str(doc.total - doc.paid),
                "network": state.get("network", ""),
            })
            return doc
    except ValidationError:
        _mark_attention(
            reference,
            "Payment was verified, but KOFAD could not post the sale automatically. Manager review is required.",
        )
        raise


def reconcile(reference, *, force=False):
    with transaction.atomic():
        held = _held(reference, lock=True)
        if not held:
            raise ValidationError("Unknown Paystack POS payment.")
        state = _state(held)
        if state.get("status") == "success" and state.get("document_id"):
            return Document.objects.select_related("party").get(pk=state["document_id"])
        if state.get("status") in TERMINAL_FAILURES or state.get("status") == "attention":
            raise ValidationError(state.get("message") or "This payment requires review.")
        now = timezone.now()
        next_check = state.get("next_check_at")
        if not force and isinstance(next_check, (int, float)) and next_check > now.timestamp():
            raise ProviderPending("Payment is already being checked.")
        if float(state.get("lease_until") or 0) > now.timestamp():
            raise ProviderPending("Payment is already being checked.")
        state["check_count"] = int(state.get("check_count") or 0) + 1
        state["lease_until"] = (now + timedelta(seconds=settings.PAYSTACK_TIMEOUT_SECONDS + 5)).timestamp()
        state["next_check_at"] = state["lease_until"]
        _save_state(held, state)

    try:
        from .paystack_challenges import charge_step, challenge_message
        if state.get("status") in PENDING_STATES:
            try:
                charge = charge_step(reference)
                state["charge_status"] = str(charge.get("status", ""))
                state["display_text"] = challenge_message(charge)
                _save_state(held, state)
            except ValidationError:
                pass
        verified = verify(reference)
    except ProviderPending:
        held.refresh_from_db()
        state = _state(held)
        state["status"] = "not_confirmed" if now.timestamp() >= float(state.get("expires_at") or 0) else "pending"
        state["message"] = (
            "Payment has not been confirmed yet. Do not request another payment while KOFAD is still verifying."
            if state["status"] == "not_confirmed"
            else "Waiting for the customer to approve the MoMo request."
        )
        state["next_check_at"] = (timezone.now() + timedelta(seconds=20 if state["status"] == "pending" else 60)).timestamp()
        _save_state(held, state)
        raise

    provider_status = str(verified.get("status", "")).strip().lower()
    held.refresh_from_db()
    state = _state(held)
    state["provider_status"] = provider_status
    if provider_status == "success":
        _save_state(held, state)
        return finalize_verified(reference, verified)
    if provider_status in TERMINAL_FAILURES and state.get("charge_status") in TERMINAL_FAILURES:
        state["status"] = provider_status
        state["message"] = str(verified.get("gateway_response") or verified.get("message") or "The MoMo request was not successful.")[:240]
        state["next_check_at"] = None
        _save_state(held, state)
        raise ValidationError(state["message"])

    state["status"] = "not_confirmed" if now.timestamp() >= float(state.get("expires_at") or 0) else "pending"
    state["message"] = (
        "Payment has not been confirmed yet. KOFAD will keep checking automatically."
        if state["status"] == "not_confirmed"
        else "Waiting for MoMo approval."
    )
    state["next_check_at"] = (timezone.now() + timedelta(seconds=20 if state["status"] == "pending" else 60)).timestamp()
    _save_state(held, state)
    raise ProviderPending(state["message"])


def status_for_staff(user, branch, reference):
    held = HeldSale.objects.filter(
        branch=branch, user=user, label=_label(reference)
    ).first()
    if not held:
        raise ValidationError("This payment request could not be found.")
    try:
        reconcile(reference)
    except ValidationError:
        pass
    held.refresh_from_db()
    return _response_state(held)


def reconcile_due(limit=20):
    # Reconcile previously initiated payments even when new Paystack POS charges
    # have been disabled in Settings.
    if not configured():
        return 0
    now_ts = timezone.now().timestamp()
    rows = HeldSale.objects.filter(
        label__startswith=LABEL_PREFIX, cart__payment_request__status__in=PENDING_STATES,
    ).order_by("created_at")[:100]
    due = []
    for held in rows:
        state = _state(held)
        if state.get("status") not in PENDING_STATES:
            continue
        next_check = state.get("next_check_at")
        if next_check is None or float(next_check) <= now_ts:
            due.append((held, state))
        if len(due) >= limit:
            break

    for held, state in due:
        reference = state.get("reference")
        if not reference:
            continue
        age = timezone.now() - held.created_at
        if age > timedelta(hours=24):
            state["status"] = "attention"
            state["message"] = "Paystack has not supplied a final status after 24 hours. Manager review is required."
            state["next_check_at"] = None
            _save_state(held, state)
            continue
        try:
            reconcile(reference, force=True)
        except ValidationError:
            pass
    return len(due)


def submit_otp(user, branch, reference, otp):
    from .paystack_challenges import charge_step, challenge_message
    services.permit(user, branch, "operate_sales")
    with transaction.atomic():
        held = HeldSale.objects.select_for_update().filter(
            branch=branch, user=user, label=_label(reference)).first()
        if not held:
            raise ValidationError("This payment request could not be found.")
        state = _state(held)
        if state.get("status") not in PENDING_STATES or state.get("charge_status") != "send_otp":
            raise ValidationError("This payment is not requesting a one-time code.")
        now = timezone.now().timestamp()
        if int(state.get("otp_attempts", 0)) >= 5 or float(state.get("otp_next_at", 0)) > now:
            raise ValidationError("Please wait before trying another code, or contact KOFAD.")
        state["otp_attempts"] = int(state.get("otp_attempts", 0)) + 1
        state["otp_next_at"] = now + 30
        _save_state(held, state)
    data = charge_step(reference, otp=otp)
    with transaction.atomic():
        held = _held(reference, lock=True)
        state = _state(held)
        if state.get("status") in PENDING_STATES:
            state["charge_status"] = str(data.get("status", ""))
            state["display_text"] = challenge_message(data)
            state["message"] = state["display_text"]
            state["next_check_at"] = (timezone.now() + timedelta(seconds=10)).timestamp()
            _save_state(held, state)
    return _response_state(held)
