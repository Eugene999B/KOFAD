"""Hubtel hosted MoMo checkout for cashier sales.

A verified Hubtel transaction, never a callback or browser report, posts the sale.
The merchant checkout link is shown to the cashier; customers enter their
own MoMo credentials only in Hubtel's secure flow.
"""
import copy
import hashlib
import json
import logging
import re
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from core import pos_paystack, services
from core.identity import normalize_ghana_phone
from core.models import Document, HeldSale, Idempotency
from marketplace import hubtel
from marketplace.services import PaymentVerificationUnavailable

logger = logging.getLogger(__name__)
LABEL_PREFIX = "Hubtel MoMo "
PENDING = {"initializing", "submission_unknown", "pending", "not_confirmed", "processing"}


def reference_from_key(request_key):
    try:
        return uuid.UUID(str(request_key)).hex
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("A valid sale request key is required.") from exc


def held_for(reference, *, lock=False):
    if not re.fullmatch(r"[0-9a-f]{32}", str(reference or "")):
        raise ValidationError("Invalid Hubtel POS reference.")
    rows = HeldSale.objects.select_related("branch", "user")
    if lock:
        rows = rows.select_for_update()
    return rows.filter(label=LABEL_PREFIX + reference).first()


def state_of(held):
    cart = held.cart if isinstance(held.cart, dict) else {}
    state = cart.get("payment_request")
    return state if isinstance(state, dict) else {}


def save_state(held, next_state):
    with transaction.atomic():
        current = HeldSale.objects.select_for_update().get(pk=held.pk)
        previous = state_of(current)
        if previous.get("status") in {"success", "attention"} and next_state.get("status") != previous.get("status"):
            return
        if int(previous.get("check_count") or 0) > int(next_state.get("check_count") or 0):
            return
        cart = copy.deepcopy(current.cart)
        cart["payment_request"] = next_state
        current.cart = cart
        current.save(update_fields=["cart"])
        held.cart = cart


def response_state(held):
    state = state_of(held)
    result = {
        "reference": state.get("reference", ""),
        "provider": "hubtel",
        "status": state.get("status", "pending"),
        "message": state.get("message", ""),
        "display_text": state.get("display_text", ""),
        "authorization_url": state.get("authorization_url", ""),
        "waiting": state.get("status") in PENDING,
        "paid": state.get("status") == "success",
        "failed": state.get("status") in {"failed", "reversed"},
        "attention": state.get("status") == "attention",
        "needs_otp": False,
    }
    if result["paid"] and state.get("document_id"):
        doc = Document.objects.select_related("party").filter(pk=state["document_id"]).first()
        if doc:
            result["sale"] = pos_paystack.result_for_document(doc)
    return result


def mark_attention(reference, message):
    with transaction.atomic():
        held = held_for(reference, lock=True)
        if not held:
            return
        state = state_of(held)
        if state.get("status") == "success":
            return
        state.update(status="attention", message=str(message)[:240], next_check_at=None)
        save_state(held, state)


def start(user, branch, sale_payload, request_key, phone, network, email):
    services.permit(user, branch, "operate_sales")
    if not isinstance(sale_payload, dict) or sale_payload.get("kind", "sale") != "sale":
        raise ValidationError("Invalid sale request.")
    reference = reference_from_key(request_key)
    saved = held_for(reference)
    if saved:
        if saved.branch_id != branch.pk or saved.user_id != user.pk:
            raise ValidationError("This payment request could not be found.")
        return response_state(saved)
    if hubtel.selected_provider() != "hubtel" or not hubtel.ready("hubtel"):
        raise ValidationError("Hubtel is not enabled for new cashier payments.")

    payload = copy.deepcopy(sale_payload)
    if not payload.get("party") and len(str(payload.get("customer_name", "")).strip()) < 2:
        raise ValidationError("Choose or enter the customer before requesting Mobile Money payment.")
    phone = normalize_ghana_phone(phone)
    # Unlike Paystack's charge API, Hubtel's hosted checkout does not require
    # a shopper email address. Preserve it when supplied, but do not block sales.
    email = str(email or "").strip().lower()
    if email:
        validate_email(email)
        payload["customer_email"] = email
    amount = pos_paystack._payment_amount(payload)
    total = pos_paystack._preview_total(user, branch, payload)
    if total <= 0 or amount != total:
        raise ValidationError("The MoMo request must exactly match the full sale total.")
    now = timezone.now()
    state = {
        "provider": "hubtel", "reference": reference, "status": "initializing",
        "phone": phone, "network": str(network or ""), "email": email,
        "amount": str(total), "request_key": str(request_key),
        "authorization_url": "", "display_text": "",
        "message": "Creating a secure Hubtel checkout.",
        "created_at": now.timestamp(), "next_check_at": (now + timedelta(seconds=40)).timestamp(),
        "check_count": 0, "document_id": "",
    }
    claim_key = uuid.uuid5(uuid.NAMESPACE_URL, "kofad-pos-hubtel:" + reference)
    fingerprint = hashlib.sha256(json.dumps(
        {"sale": payload, "user": user.pk, "phone": phone},
        sort_keys=True, default=str,
    ).encode()).hexdigest()
    with transaction.atomic():
        claim, created = Idempotency.objects.get_or_create(
            branch=branch, key=claim_key, defaults={"fingerprint": fingerprint},
        )
        if claim.fingerprint != fingerprint:
            raise ValidationError("This payment request conflicts with an existing request.")
        existing = held_for(reference, lock=True)
        if existing:
            if existing.branch_id != branch.pk or existing.user_id != user.pk:
                raise ValidationError("This payment request could not be found.")
            return response_state(existing)
        if not created:
            raise ValidationError("This checkout is already being prepared. Check its status.")
        held = HeldSale.objects.create(
            branch=branch, user=user, label=LABEL_PREFIX + reference,
            cart={"sale_payload": payload, "payment_request": state},
        )

    payload_request = {
        "totalAmount": float(total),
        "description": "KOFAD cashier sale",
        "merchantAccountNumber": settings.HUBTEL_COLLECTION_ACCOUNT,
        "clientReference": reference,
        "callbackUrl": hubtel.PUBLIC_ORIGIN + "/market/payments/hubtel/callback/",
        "returnUrl": "https://staff.kofadimpex.com/sales/new/",
        "cancellationUrl": "https://staff.kofadimpex.com/sales/new/",
    }
    try:
        response = requests.post(
            hubtel.INITIATE_URL, headers=hubtel.headers(), json=payload_request,
            timeout=settings.HUBTEL_TIMEOUT_SECONDS, allow_redirects=False,
        )
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        url = data.get("checkoutUrl") if isinstance(data, dict) else ""
        parsed = urlsplit(url) if isinstance(url, str) else urlsplit("")
        valid = (
            200 <= response.status_code < 300 and isinstance(body, dict)
            and body.get("responseCode") == "0000"
            and isinstance(data, dict) and data.get("clientReference") == reference
            and data.get("checkoutId") and parsed.scheme == "https"
            and parsed.netloc == "pay.hubtel.com" and bool(parsed.path.strip("/"))
            and len(url) <= 200
        )
    except (requests.RequestException, ValueError, TypeError):
        valid = False
    if not valid:
        state.update(
            status="submission_unknown",
            message="Hubtel checkout outcome is uncertain. Do not start another payment; KOFAD is checking.",
            next_check_at=(timezone.now() + timedelta(seconds=40)).timestamp(),
        )
        save_state(held, state)
        return response_state(held)

    state.update(
        status="pending", authorization_url=url,
        message="Open the secure Hubtel checkout and let the customer approve payment.",
        display_text="The customer must complete payment in Hubtel. Never collect their MoMo PIN.",
        next_check_at=(timezone.now() + timedelta(seconds=40)).timestamp(),
    )
    save_state(held, state)
    held.refresh_from_db()
    return response_state(held)


def reconcile(reference, *, force=False):
    with transaction.atomic():
        held = held_for(reference, lock=True)
        if not held:
            raise ValidationError("Unknown Hubtel POS payment.")
        state = state_of(held)
        if state.get("status") == "success":
            return response_state(held)
        if state.get("status") == "attention":
            raise ValidationError(state.get("message") or "Payment needs manager review.")
        now = timezone.now().timestamp()
        if not force and float(state.get("next_check_at") or 0) > now:
            return response_state(held)
        if float(state.get("lease_until") or 0) > now:
            return response_state(held)
        state["check_count"] = int(state.get("check_count") or 0) + 1
        state["lease_until"] = now + settings.HUBTEL_TIMEOUT_SECONDS + 5
        state["next_check_at"] = now + 40
        save_state(held, state)

    try:
        verified = hubtel.verify(reference)
    except PaymentVerificationUnavailable:
        return response_state(held_for(reference))
    if verified.get("status") != "Paid":
        return response_state(held_for(reference))
    try:
        amount = Decimal(str(verified.get("amount")))
        currency = str(verified.get("currencyCode") or "").strip().upper()
        channel = re.sub(r"[^a-z]", "", str(verified.get("paymentMethod", "")).lower())
        if (
            not amount.is_finite() or amount != Decimal(str(state["amount"]))
            or currency not in {"", "GHS"}
            or not verified.get("transactionId")
            or verified.get("clientReference") != reference
            or channel not in {"mobilemoney", "momo"}
        ):
            raise ValidationError("Hubtel transaction details do not match this cashier sale.")
    except (TypeError, ValueError, InvalidOperation, ValidationError) as exc:
        mark_attention(reference, "Hubtel confirmed a transaction but the amount or payment channel needs manager review.")
        raise ValidationError("Verified payment requires manager review.") from exc

    try:
        with transaction.atomic():
            held = held_for(reference, lock=True)
            state = state_of(held)
            if state.get("status") == "success":
                return response_state(held)
            if state.get("status") == "attention":
                raise ValidationError("Payment needs manager review.")
            payload = copy.deepcopy(held.cart.get("sale_payload"))
            if not isinstance(payload, dict):
                raise ValidationError("Saved sale details are unavailable.")
            for row in payload.get("payments", []):
                if isinstance(row, dict) and row.get("method") == "momo" and Decimal(str(row.get("amount", "0"))) > 0:
                    row["reference"] = reference
            doc = services.post_trade(
                held.user, held.branch, payload, state["request_key"], kind="sale",
            )
            if doc.total != amount or doc.paid != doc.total:
                raise ValidationError("Sale total changed since payment was requested.")
            state.update(
                status="success", document_id=str(doc.pk), verified_at=timezone.now().timestamp(),
                transaction_id=str(verified["transactionId"])[:80], next_check_at=None,
                message="Payment independently confirmed by Hubtel; sale posted.",
            )
            save_state(held, state)
            transaction.on_commit(lambda: pos_paystack._send_receipts(
                held.user_id, held.branch_id, doc.pk,
                payload.get("send_sms", payload.get("customer_consent")) is True,
                payload.get("send_whatsapp") is True,
            ))
            services.audit(held.user, held.branch, "sale.hubtel_momo_verified", doc.reference, {
                "reference": reference, "amount": str(amount),
            })
            return response_state(held)
    except ValidationError:
        mark_attention(reference, "Payment confirmed, but posting this sale needs manager review. Do not charge again.")
        raise


def status_for_staff(user, branch, reference):
    held = held_for(reference)
    if not held or held.user_id != user.pk or held.branch_id != branch.pk:
        raise ValidationError("This payment request could not be found.")
    try:
        reconcile(reference)
    except (PaymentVerificationUnavailable, ValidationError):
        pass
    held.refresh_from_db()
    return response_state(held)


def reconcile_due(limit=10):
    if not hubtel.configured():
        return 0
    now = timezone.now()
    rows = HeldSale.objects.filter(
        label__startswith=LABEL_PREFIX,
        cart__payment_request__provider="hubtel",
        cart__payment_request__status__in=list(PENDING),
    ).order_by("created_at")[:100]
    count = 0
    for held in rows:
        state = state_of(held)
        if float(state.get("next_check_at") or 0) > now.timestamp():
            continue
        reference = state.get("reference")
        if now - held.created_at >= timedelta(hours=72):
            mark_attention(reference, "Hubtel has not supplied a final answer after 72 hours. Manager review required.")
            continue
        try:
            reconcile(reference)
        except (PaymentVerificationUnavailable, ValidationError):
            pass
        count += 1
        if count >= limit:
            break
    return count
