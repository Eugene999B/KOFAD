"""Direct Debit V2: authorize a wallet BEFORE issuing a credit-sale receipt.

The intent is durable, quote-only and idempotent. It cannot move inventory,
send a purchase receipt or post a collection. Hubtel POST happens at most once
per reference. An independent provider APPROVED GET is required before the
actual sale and invoice-scoped agreement are atomically created.
"""
import hashlib
import json
import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core import services as sale_service
from core.views import branch_for
from . import client, debit_api, mandates, schedules
from .models import DebtMandate, DebtDebitAttempt, DirectDebitSaleIntent, OnlineDebtSettings

log = logging.getLogger(__name__)
ZERO = Decimal("0.00")
BLOCKING_MANDATES = ("draft", "pending", "approved", "cancel_pending", "suspended")


class _RollbackPreview(Exception):
    def __init__(self, total, paid, party_id):
        self.total, self.paid, self.party_id = total, paid, party_id


def _quote(user, branch, sale):
    """Run KOFAD's ACTUAL sale rules in a rolled-back savepoint.

    Reuses all inventory, override, customer/credit and accounting validations
    without creating stock movements, a final invoice, or on-commit messages.
    """
    try:
        with transaction.atomic():
            doc = sale_service.post_trade(user, branch, sale, uuid.uuid4(), kind="sale")
            raise _RollbackPreview(doc.total, doc.paid, doc.party_id)
    except _RollbackPreview as preview:
        return preview.total, preview.paid, preview.party_id


def _date(raw):
    try:
        return date.fromisoformat(str(raw))
    except (ValueError, TypeError) as exc:
        raise ValidationError("Choose valid first and final collection dates.") from exc


def _phone_hash(phone):
    return hashlib.sha256((settings.SECRET_KEY + "|hubtel-debt-wallet|" + phone).encode()).hexdigest()


def _validate_policy(branch, channel, frequency, first, amount, preview):
    policy, created = OnlineDebtSettings.objects.get_or_create(branch=branch)
    if created:
        policy.refresh_from_db()
    if not policy.plan_creation_enabled:
        raise ValidationError("Direct Debit agreements are disabled in Online Debt Settings.")
    if not policy.allows_channel(channel) or channel not in debit_api.DEBIT_CHANNELS:
        raise ValidationError("This wallet network is not enabled for Direct Debit.")
    if not policy.allows_frequency(frequency):
        raise ValidationError("This repayment frequency is disabled in Online Debt Settings.")
    delay = (first - timezone.localdate()).days
    if delay < policy.min_first_due_days or delay > min(policy.max_first_due_days, 366):
        raise ValidationError("First collection date is outside the policy's permitted range.")
    if not policy.min_open_balance <= amount <= policy.max_open_balance:
        raise ValidationError("This sale's unpaid amount is outside the permitted Direct Debit balance.")
    if not policy.min_installment <= preview["amount_per_period"] <= policy.max_installment:
        raise ValidationError("The calculated installment exceeds your branch's collection policy.")
    return policy


@transaction.atomic
def prepare_intent(user, branch, body, request_key):
    """No sale is posted here. Replaying identical request key returns one draft."""
    sale_service.permit(user, branch, "operate_sales")
    if not debit_api.preapproval_ready():
        raise ValidationError("Hubtel preapproval is not configured. No sale has been recorded.")
    if not isinstance(body, dict) or not isinstance(body.get("sale"), dict):
        raise ValidationError("Provide a sale and Direct Debit details.")
    agreement = body.get("direct_debit")
    if not isinstance(agreement, dict):
        raise ValidationError("The repayment agreement is missing.")
    if agreement.get("customer_requested_prompt") != "yes" or agreement.get("customer_reviewed_schedule") != "yes":
        raise ValidationError("The customer must review the complete schedule and request wallet preapproval.")
    try:
        key = uuid.UUID(str(request_key))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Checkout request key is missing or invalid.") from exc
    raw = json.dumps(body, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(raw.encode()).hexdigest()
    prior = DirectDebitSaleIntent.objects.select_for_update().filter(request_key=key).first()
    if prior:
        if prior.branch_id != branch.pk or prior.created_by_id != user.pk or prior.request_fingerprint != fingerprint:
            raise ValidationError("This request key is linked to different sale details.")
        return prior, False

    sale = dict(body["sale"])
    if sale.get("kind", "sale") != "sale":
        raise ValidationError("Only POS credit sales can request Direct Debit.")
    # Not a customer-paid MoMo checkout. Any verified cash/bank deposit is
    # recorded exactly once when the sale is finally issued.
    for payment in sale.get("payments") or []:
        if payment.get("method") == "momo" and Decimal(str(payment.get("amount") or "0")) > ZERO:
            raise ValidationError("Do not treat Mobile Money deposits as received before independent payment verification.")
    sale["send_sms"] = False
    sale["send_whatsapp"] = False
    sale["customer_consent"] = True
    sale["due_date"] = str(agreement.get("first_due_date") or "")
    phone = debit_api.number(agreement.get("phone"))
    channel = str(agreement.get("channel") or "")
    frequency = str(agreement.get("frequency") or "")
    first = _date(agreement.get("first_due_date"))
    final = _date(agreement.get("final_due_date"))
    if channel not in debit_api.DEBIT_CHANNELS:
        raise ValidationError("Choose MTN or Telecel Direct Debit.")

    branch = sale_service.lock_branch(branch)
    total, paid, preview_party_id = _quote(user, branch, sale)
    # When a NEW customer is validated in the rolled-back preview, their
    # temporary Party PK does not exist. Only persist an existing customer FK;
    # the new customer's permanent record is created on final issuance.
    party_id = sale.get("party") or None
    balance = total - paid
    if balance <= ZERO:
        raise ValidationError("The sale is already fully paid; Direct Debit cannot be created.")
    policy = OnlineDebtSettings.objects.get_or_create(branch=branch)[0]
    preview = schedules.preview_by_end_date(
        balance, frequency, first, final,
        from_hour=policy.collection_from_hour,
        until_hour=policy.collection_until_hour,
    )
    _validate_policy(branch, channel, frequency, first, balance, preview)
    fingerprint_wallet = _phone_hash(phone)
    if DebtMandate.objects.filter(phone_fingerprint=fingerprint_wallet, status__in=BLOCKING_MANDATES).exists():
        raise ValidationError(
            "This wallet has another active Direct Debit agreement. Review that agreement "
            "before requesting a separate approval; no sale was issued."
        )
    if DebtDebitAttempt.objects.filter(
        mandate__phone_fingerprint=fingerprint_wallet,
        status__in=["sending", "pending", "unknown", "finance_review"]
    ).exists():
        raise ValidationError("This wallet has an unresolved debit; finance must reconcile it first.")
    if DirectDebitSaleIntent.objects.filter(phone_fingerprint=fingerprint_wallet,
                                           status__in=["draft", "sending", "pending", "unknown", "approved", "attention"]).exists():
        raise ValidationError("This wallet already has a Direct Debit checkout awaiting resolution.")

    intent = DirectDebitSaleIntent.objects.create(
        request_key=key, request_fingerprint=fingerprint, branch=branch,
        created_by=user, party_id=party_id, checkout_payload=sale,
        phone_encrypted=client.seal(phone), phone_fingerprint=fingerprint_wallet,
        phone_suffix=phone[-4:], channel=channel, frequency=frequency,
        first_due_date=first, final_due_date=final,
        quoted_total=total, quoted_paid=paid, outstanding=balance,
        client_reference=debit_api.reference(),
        terms_snapshot={
            "source": "new_sale_before_issue", "total": str(total),
            "paid": str(paid), "outstanding": str(balance),
            "first_due_date": first.isoformat(), "final_due_date": final.isoformat(),
            "frequency": frequency, "channel": channel,
            "schedule": preview["schedule"],
            "installment_count": preview["installment_count"],
            "last_due_date": preview["end_date"],
            "last_installment": str(preview["last_amount"]),
            "amount_per_period": str(preview["amount_per_period"]),
            "collection_window": preview["collection_window"],
            "customer_requested_prompt": True, "customer_reviewed_schedule": True,
            "customer_can_cancel": True, "policy_version": policy.updated_at.isoformat(),
        },
    )
    sale_service.audit(user, branch, "hubtel_debt.v2_intent_prepared", str(intent.pk), {
        "wallet_suffix": phone[-4:], "amount": str(balance),
        "first": first.isoformat(), "last": preview["end_date"],
        "source": "not_issued",
    }, category="finance")
    return intent, True


def begin_preapproval(intent):
    """One real Hubtel request; record 'sending' before network I/O."""
    debit_api.gate("preapproval")
    with transaction.atomic():
        locked = DirectDebitSaleIntent.objects.select_for_update().get(pk=intent.pk)
        if locked.status != "draft":
            return locked
        locked.status = "sending"
        locked.request_sent_at = timezone.now()
        locked.next_provider_check_at = timezone.now() + timedelta(minutes=5)
        locked.save(update_fields=["status", "request_sent_at", "next_provider_check_at", "updated_at"])
    try:
        result = debit_api.initiate_preapproval(
            phone=client.unseal(intent.phone_encrypted),
            channel=intent.channel, client_reference=intent.client_reference,
            callback_url=debit_api.callback("sale_intent"),
        )
    except debit_api.ProviderDenied as exc:
        DirectDebitSaleIntent.objects.filter(pk=intent.pk, status="sending").update(
            status="rejected", last_error=str(exc)[:240], next_provider_check_at=None,
        )
        return DirectDebitSaleIntent.objects.get(pk=intent.pk)
    except Exception as exc:
        # Timeout may have reached Hubtel: NEVER repeat POST automatically.
        log.warning("V2 preapproval outcome unconfirmed for intent %s: %s", intent.pk, type(exc).__name__)
        DirectDebitSaleIntent.objects.filter(pk=intent.pk, status="sending").update(
            status="unknown", last_error="Hubtel response uncertain. Check original reference; do not send again.",
        )
        return DirectDebitSaleIntent.objects.get(pk=intent.pk)
    with transaction.atomic():
        locked = DirectDebitSaleIntent.objects.select_for_update().get(pk=intent.pk)
        if locked.status == "sending":
            locked.status = "pending"
            locked.hubtel_preapproval_id = result["provider_id"]
            locked.verification_type = result["verification_type"]
            locked.otp_prefix = result["otp_prefix"]
            locked.last_error = ""
            locked.save(update_fields=[
                "status", "hubtel_preapproval_id", "verification_type", "otp_prefix", "last_error", "updated_at",
            ])
    return DirectDebitSaleIntent.objects.get(pk=intent.pk)


# Direct Debit preapproval is only authority to request future instalments.
# It must NEVER call post_trade(), issue a sales receipt, or move stock.
# Issued invoices are created by the ordinary explicit POS Record Sale action.


def reconcile_one(intent, *, force=False):
    if intent.status not in {"pending", "unknown", "sending", "approved"}:
        return intent
    if intent.status == "approved":
        return intent
    if not force and intent.next_provider_check_at and intent.next_provider_check_at > timezone.now():
        return intent
    state = debit_api.preapproval_status(
        intent.client_reference,
        expected_phone=client.unseal(intent.phone_encrypted),
        expected_provider_id=intent.hubtel_preapproval_id or None,
    )
    with transaction.atomic():
        row = DirectDebitSaleIntent.objects.select_for_update().get(pk=intent.pk)
        if row.status not in {"pending", "unknown", "sending"}:
            return row
        now = timezone.now()
        row.last_provider_check_at = now
        row.next_provider_check_at = now + timedelta(minutes=5)
        if state == "APPROVED":
            row.status = "approved"
            row.provider_approved_at = now
            row.next_provider_check_at = None
            row.last_error = "Wallet preapproval verified. No sale, invoice, or payment was recorded; handle sales and debt agreements separately."
        elif state in {"REJECTED", "FAILED", "EXPIRED", "CANCELLED"}:
            row.status = "rejected"
            row.next_provider_check_at = None
            row.last_error = "Hubtel reported " + state + ". No sale was issued."
        row.save(update_fields=[
            "last_provider_check_at", "next_provider_check_at", "status",
            "provider_approved_at", "last_error", "updated_at",
        ])
    # Provider approval alone changes no sale, stock, invoice, or payment.
    # Staff may separately record a credit sale and establish invoice-scoped terms.
    return DirectDebitSaleIntent.objects.get(pk=intent.pk)


def reconcile_due(limit=10):
    now = timezone.now()
    rows = list(DirectDebitSaleIntent.objects.filter(
        status__in=["pending", "unknown", "sending"],
        next_provider_check_at__lte=now,
    ).order_by("next_provider_check_at").values_list("pk", flat=True)[:max(1, min(int(limit), 40))])
    done = 0
    for pk in rows:
        try:
            reconcile_one(DirectDebitSaleIntent.objects.get(pk=pk), force=False)
            done += 1
        except Exception:
            log.exception("Direct Debit V2 status reconciliation failed for %s", pk)
    return done
