"""Direct Hubtel cashier MoMo prompts, with legacy hosted-history reconciliation."""
import copy
import hashlib
import json
import logging
import re
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from django.core import signing
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
from hubtel_direct import client as direct_client
from marketplace.services import PaymentVerificationUnavailable

logger = logging.getLogger(__name__)
LABEL_PREFIX = "Hubtel MoMo "
PENDING = {"initializing", "submission_unknown", "pending", "not_confirmed", "processing"}
FIRST_STATUS_DELAY = 20
RECHECK_DELAY = 30


def reference_from_key(request_key):
    try:
        return uuid.UUID(str(request_key)).hex
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("A valid sale request key is required.") from exc


def held_for(reference, *, lock=False):
    if not re.fullmatch(r"[0-9a-f]{32}", str(reference or "")):
        raise ValidationError("Invalid Direct MoMo payment reference.")
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
        "provider_status": state.get("provider_status") or "not_checked",
        "provider_detail": str(state.get("provider_detail") or "")[:180],
        "last_checked_at": state.get("last_checked_at"),
        "next_check_at": state.get("next_check_at"),
        "callback_received": bool(state.get("callback_seen_at")),
        "provider": "hubtel",
        "status": state.get("status", "pending"),
        "message": state.get("message", ""),
        "network_response_code": str(state.get("provider_code") or "")[:20],
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


def start(user, branch, sale_payload, request_key, phone, network, email, wallet_proof=None, customer_authorized=False):
    services.permit(user, branch, "operate_sales")
    if not isinstance(sale_payload, dict) or sale_payload.get("kind", "sale") != "sale":
        raise ValidationError("Invalid sale request.")
    reference = reference_from_key(request_key)
    saved = held_for(reference)
    if saved:
        if saved.branch_id != branch.pk or saved.user_id != user.pk:
            raise ValidationError("This payment request could not be found.")
        return response_state(saved)
    if hubtel.selected_provider() != "hubtel":
        raise ValidationError("Direct MoMo is not enabled for this cashier.")
    direct_client.require_enabled("receive")
    if customer_authorized is not True:
        raise ValidationError("Confirm the customer agreed to this Mobile Money payment request.")

    payload = copy.deepcopy(sale_payload)
    # Full-pay walk-in sales do not require a named account: wallet
    # verification identifies the payer, without creating a Party.
    # A partial payment is still rejected by the exact-total check below.
    phone = normalize_ghana_phone(phone)
    network = {"mtn": "mtn-gh", "vod": "vodafone-gh", "atl": "tigo-gh"}.get(str(network), network)
    if network not in direct_client.CHANNELS:
        raise ValidationError("Select the correct MoMo network.")
    try:
        verified = signing.loads(
            wallet_proof, salt="kofad-hubtel-staff-wallet-verify-v1", max_age=180,
        )
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise ValidationError("Verify the Mobile Money wallet name before requesting payment.") from exc
    if (
        not isinstance(verified, dict) or verified.get("user") != user.pk
        or verified.get("branch") != branch.pk
        or verified.get("phone") != phone.lstrip("+")
        or verified.get("channel") != network
        or not verified.get("name")
    ):
        raise ValidationError("The wallet verification does not match this phone or network.")
    wallet_name = str(verified["name"])[:140]
    # Unlike Paystack's's charge API, Hubtel's hosted checkout does not require
    # a shopper email address. Preserve it when supplied, but do not block sales.
    email = str(email or "").strip().lower()
    if email:
        validate_email(email)
        payload["customer_email"] = email
    from marketplace.pricing import online_markup_percent
    markup = online_markup_percent()
    amount = pos_paystack._payment_amount(payload)
    total = pos_paystack._preview_total(user, branch, payload, online_markup_percent=markup)
    if total <= 0 or amount != total:
        raise ValidationError("The MoMo request must exactly match the full sale total.")
    now = timezone.now()
    state = {
        "provider": "hubtel", "flow": "direct", "reference": reference, "status": "initializing",
        "phone": phone, "network": str(network or ""), "email": email, "verified_wallet_name": wallet_name,
        "amount": str(total), "online_markup_percent": str(markup),
        "request_key": str(request_key),
        "authorization_url": "", "display_text": "",
        "message": "Sending a Direct MoMo payment request.",
        "created_at": now.timestamp(), "next_check_at": (now + timedelta(seconds=FIRST_STATUS_DELAY)).timestamp(),
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

    # The reference and HeldSale already exist BEFORE this irreversible POST.
    # Never retry after an unknown network result.
    tx = SimpleNamespace(
        kind="receive", channel=network, bank_code="", amount=total,
        client_reference=reference, recipient_name=wallet_name,
        destination_encrypted=direct_client.seal(phone.lstrip("+")),
    )
    try:
        response_code, transaction_id = direct_client.submit(tx)
    except direct_client.ProviderRejected as exc:
        state.update(
            status="failed", provider_code=exc.code,
            message="The payment network rejected this request (" + exc.code + "). No sale was recorded.",
            next_check_at=None,
        )
        save_state(held, state)
        return response_state(held)
    except (direct_client.SubmissionUnknown, ValidationError):
        state.update(
            status="submission_unknown",
            message="Request status unknown. Do not send another prompt; KOFAD will check it.",
            next_check_at=(timezone.now() + timedelta(seconds=FIRST_STATUS_DELAY)).timestamp(),
        )
        save_state(held, state)
        return response_state(held)
    state.update(
        status="pending", provider_code=response_code, provider_transaction_id=transaction_id,
        authorization_url="",
        message="The payment network accepted this request. Ask the customer to check their phone; prompt delivery and payment are not yet confirmed.",
        display_text="Waiting for payment confirmation. Do not charge again or ask for a MoMo PIN.",
        next_check_at=(timezone.now() + timedelta(seconds=FIRST_STATUS_DELAY)).timestamp(),
    )
    save_state(held, state)
    held.refresh_from_db()
    return response_state(held)


def note_callback(data):
    """An untrusted callback only accelerates independent status checking."""
    if not isinstance(data, dict):
        return False
    from hubtel_direct.client import _field
    item = _field(data, "Data")
    ref = _field(item, "ClientReference") if isinstance(item, dict) else ""
    if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{32}", ref):
        return False
    with transaction.atomic():
        held = held_for(ref, lock=True)
        if not held:
            return False
        state = state_of(held)
        if state.get("status") not in PENDING:
            return True
        now = timezone.now().timestamp()
        state["callback_seen_at"] = now
        state["next_check_at"] = min(
            float(state.get("next_check_at") or float("inf")),
            max(now, float(state.get("created_at") or now) + FIRST_STATUS_DELAY),
        )
        save_state(held, state)
        return True


def reconcile(reference, *, force=False):
    with transaction.atomic():
        held = held_for(reference, lock=True)
        if not held:
            raise ValidationError("Direct MoMo payment request not found.")
        state = state_of(held)
        if state.get("status") == "success":
            return response_state(held)
        if state.get("status") == "attention":
            raise ValidationError(state.get("message") or "Payment needs manager review.")
        now = timezone.now().timestamp()
        if state.get("flow") == "direct":
            # Read-only status GET starts after 20s; everyone shares a DB lease.
            if now - float(state.get("created_at") or now) < FIRST_STATUS_DELAY:
                return response_state(held)
            if float(state.get("next_check_at") or 0) > now:
                return response_state(held)
        elif not force and float(state.get("next_check_at") or 0) > now:
            return response_state(held)
        if float(state.get("lease_until") or 0) > now:
            return response_state(held)
        state["check_count"] = int(state.get("check_count") or 0) + 1
        state["lease_until"] = now + settings.HUBTEL_TIMEOUT_SECONDS + 5
        state["next_check_at"] = now + (
            RECHECK_DELAY if state.get("flow") == "direct" else 40
        )
        save_state(held, state)

    if state.get("flow") == "direct":
        tx = SimpleNamespace(
            kind="receive", channel=state["network"],
            client_reference=reference, amount=Decimal(str(state["amount"])),
            hubtel_transaction_id=state.get("provider_transaction_id") or "",
        )
        try:
            outcome, tid = direct_client.check(tx)
        except direct_client.StatusUnconfirmed as exc:
            with transaction.atomic():
                held = held_for(reference, lock=True)
                current = state_of(held)
                if current.get("status") in PENDING:
                    reason = str(exc)[:180]
                    missing = "no payment record" in reason.lower() or "no matching" in reason.lower()
                    current.update(
                        last_checked_at=timezone.now().timestamp(),
                        provider_status="not_found" if missing else "unavailable",
                        provider_detail=reason,
                        message=("Hubtel has no matching payment record at this check. "
                                 "The original request needs investigation; do not charge twice.")
                        if missing else
                        "The provider has not confirmed this payment. Do not charge twice.",
                    )
                    save_state(held, current)
            return response_state(held_for(reference))
        except ValidationError:
            mark_attention(reference, "Payment status did not match this sale. Manager review is required.")
            return response_state(held_for(reference))
        if outcome == "failed":
            with transaction.atomic():
                held = held_for(reference, lock=True)
                result = state_of(held)
                if result.get("status") in PENDING:
                    result.update(status="failed", provider_status="failed",
                                  provider_transaction_id=tid,
                                  last_checked_at=timezone.now().timestamp(),
                                  next_check_at=None,
                                  message="Hubtel independently confirmed this transaction failed.")
                    save_state(held, result)
            return response_state(held_for(reference))
        if outcome == "attention":
            mark_attention(reference, "A disputed payment needs manager review.")
            return response_state(held_for(reference))
        if outcome != "success":
            with transaction.atomic():
                held = held_for(reference, lock=True)
                current = state_of(held)
                if current.get("status") in PENDING:
                    current.update(
                        provider_status="unpaid",
                        last_checked_at=timezone.now().timestamp(),
                        message="Hubtel reports Unpaid at the last check. If the customer was debited, do not charge twice; checks continue.",
                    )
                    save_state(held, current)
            return response_state(held_for(reference))
        verified = {
            "status": "Paid", "amount": state["amount"], "currencyCode": "GHS",
            "paymentMethod": "mobilemoney", "transactionId": tid,
            "clientReference": reference,
        }
    else:
        # Historical Hubtel Online Checkout charges must still reconcile.
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
            raise ValidationError("Payment details do not match this cashier sale.")
    except (TypeError, ValueError, InvalidOperation, ValidationError) as exc:
        mark_attention(reference, "The amount or payment channel needs manager review.")
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
                online_markup_percent=Decimal(str(state.get("online_markup_percent", "0"))),
            )
            if doc.total != amount or doc.paid != doc.total:
                raise ValidationError("Sale total changed since payment was requested.")
            state.update(
                status="success", provider_status="paid",
                last_checked_at=timezone.now().timestamp(),
                document_id=str(doc.pk), verified_at=timezone.now().timestamp(),
                transaction_id=str(verified["transactionId"])[:80], next_check_at=None,
                message="Payment independently confirmed; sale recorded.",
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
    if not held or held.branch_id != branch.pk:
        raise ValidationError("This payment request could not be found.")
    if held.user_id != user.pk:
        # Managers may investigate another cashier's reference but the
        # branch boundary remains mandatory.
        services.permit(user, branch, "manage_company")
    try:
        reconcile(reference)
    except (PaymentVerificationUnavailable, ValidationError):
        pass
    held.refresh_from_db()
    return response_state(held)


def reconcile_due(limit=10, *, include_direct=False):
    if not hubtel.configured() and not direct_client.enabled("receive"):
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
        # The communications worker does NOT have the whitelisted web IPs.
        # Direct status checks run through the secured web egress relay only.
        if state.get("flow") == "direct" and not include_direct:
            continue
        if float(state.get("next_check_at") or 0) > now.timestamp():
            continue
        reference = state.get("reference")
        if now - held.created_at >= timedelta(hours=72):
            mark_attention(reference, "This payment is unresolved after 72 hours. Manager review is required.")
            continue
        try:
            reconcile(reference)
        except (PaymentVerificationUnavailable, ValidationError):
            pass
        count += 1
        if count >= limit:
            break
    return count
