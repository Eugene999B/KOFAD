"""Debt mandate lifecycle. Provider authority and customer consent are distinct.

IMPORTANT: direct-debit charge execution is permanently unavailable unless
its separate deployment switch is enabled after authorization and live UAT.
No customer-wallet balance API exists. Never probe it with shrinking debits.
"""
import hashlib
from datetime import timedelta
from decimal import Decimal

from calendar import monthrange
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core import debts as debt_service
from core import services as core_services
from core.models import Party
from . import client, debit_api
from .models import DebtMandate, DebtDebitAttempt, OnlineDebtSettings

ZERO = Decimal("0.00")
CHARGE_STATES = ("planned", "sending", "pending", "unknown", "finance_review", "settled", "failed")
UNRESOLVED_CHARGE_STATES = ("planned", "sending", "pending", "unknown", "finance_review")


def outstanding(party):
    return sum((amount for _, amount in debt_service.open_customer_invoices(party)), ZERO)


def _money(v):
    return client.money(v)


def _period_next(day, frequency):
    if frequency == "once":
        return None
    if frequency == "daily":
        return day + timedelta(days=1)
    if frequency == "weekly":
        return day + timedelta(days=7)
    if frequency in {"monthly", "quarterly", "yearly"}:
        months = {"monthly": 1, "quarterly": 3, "yearly": 12}[frequency]
        year = day.year + (day.month - 1 + months) // 12
        month = (day.month - 1 + months) % 12 + 1
        return day.replace(year=year, month=month,
                           day=min(day.day, monthrange(year, month)[1]))
    raise ValidationError("Choose a supported payment frequency.")


@transaction.atomic
def create_plan(user, branch, *, party_id, phone, channel, frequency,
                first_due_date, amount_per_period, maximum_total,
                allow_one_partial_retry=False, minimum_partial="0.00"):
    """Prepare an agreement; creating it alone never initiates Hubtel preapproval."""
    # A cashier may PROPOSE a plan when making a credit sale. Only the
    # wallet holder may grant authority, and finance controls real collections.
    core_services.permit(
        user, branch,
        "operate_finance" if user.has_perm("core.operate_finance") else "operate_sales",
    )
    # Serialize two customers trying to bind the same merchant wallet before
    # the database uniqueness constraint resolves any remaining race.
    core_services.lock_branch(branch)
    policy, _ = OnlineDebtSettings.objects.select_for_update().get_or_create(branch=branch)
    if not policy.plan_creation_enabled:
        raise ValidationError("Online Debt agreements are disabled in Debt Settings for this location.")
    if not policy.allows_channel(channel):
        raise ValidationError("This Mobile Money network is disabled in Online Debt Settings.")
    if not policy.allows_frequency(frequency):
        raise ValidationError("This repayment frequency is disabled in Online Debt Settings.")
    if allow_one_partial_retry:
        raise ValidationError("Automatic lower-amount retry is unavailable; choose a scheduled repayment without wallet probing.")
    party = Party.objects.select_for_update().filter(
        pk=party_id, branch=branch, kind="customer").first()
    if not party:
        raise ValidationError("Select a customer in this branch.")
    if channel not in debit_api.DEBIT_CHANNELS:
        raise ValidationError("Direct Debit is available only for MTN and Telecel.")
    if frequency not in debit_api.FREQUENCIES:
        raise ValidationError("Choose daily, weekly, monthly, quarterly, yearly, or once.")
    if isinstance(first_due_date, str):
        from datetime import date
        try:
            first_due_date = date.fromisoformat(first_due_date)
        except ValueError as exc:
            raise ValidationError("Enter a valid first payment date.") from exc
    if first_due_date < timezone.localdate():
        raise ValidationError("First payment date cannot be in the past.")
    delay = (first_due_date - timezone.localdate()).days
    if delay < policy.min_first_due_days or delay > min(policy.max_first_due_days, 366):
        raise ValidationError("The first due date is outside the location's Online Debt schedule limits.")
    due = outstanding(party)
    if due <= ZERO:
        raise ValidationError("This account has no unpaid customer invoices.")
    if not policy.min_open_balance <= due <= policy.max_open_balance:
        raise ValidationError("This customer's outstanding balance is outside Online Debt eligibility limits.")
    amt, maximum = _money(amount_per_period), _money(maximum_total)
    if maximum > due:
        raise ValidationError("The mandate ceiling cannot exceed recorded debt.")
    if not policy.min_installment <= amt <= policy.max_installment:
        raise ValidationError("Installment amount is outside the Online Debt policy limits.")
    if amt > maximum:
        raise ValidationError("Installment cannot exceed the agreed mandate maximum.")
    minimum = ZERO
    digits = debit_api.number(phone)
    fingerprint = hashlib.sha256((settings.SECRET_KEY + "|hubtel-debt-wallet|" + digits).encode()).hexdigest()
    if not party.consent:
        raise ValidationError("Record the customer's consent to payment communications first.")
    blocking = ["draft", "pending", "approved", "cancel_pending", "suspended"]
    if DebtMandate.objects.filter(party=party, status__in=blocking).exists():
        raise ValidationError("This customer already has an active, suspended or pending direct-debit arrangement.")
    if DebtMandate.objects.filter(phone_fingerprint=fingerprint, status__in=blocking).exists():
        raise ValidationError(
            "This wallet is still linked to an existing or suspended debt mandate. "
            "The previous agreement must be cancelled and reconciled first."
        )
    # An already-cancelled wallet authority can still have a charge in
    # flight. Do not authorize a replacement while the previous reference
    # may have moved money. All customers sharing this wallet are covered.
    if DebtDebitAttempt.objects.filter(
        mandate__phone_fingerprint=fingerprint,
        status__in=["sending", "pending", "unknown", "finance_review"],
    ).exists():
        raise ValidationError(
            "A previous Hubtel debit on this wallet is not reconciled. "
            "Finance must resolve its original payment reference first."
        )
    mandate = DebtMandate.objects.create(
        branch=branch, party=party, created_by=user,
        phone_encrypted=client.seal(digits), phone_fingerprint=fingerprint, phone_suffix=digits[-4:],
        channel=channel, client_reference=debit_api.reference(),
        frequency=frequency, first_due_date=first_due_date, next_due_date=first_due_date,
        amount_per_period=amt, maximum_total=maximum,
        lower_partial_payment=False, minimum_partial=minimum,
        maximum_attempts_per_period=1,
        terms_snapshot={
            "starting_outstanding": str(due), "amount": str(amt),
            "maximum_total": str(maximum), "frequency": frequency,
            "starts": first_due_date.isoformat(), "network": channel,
            "partial_retry_enabled": False,
            "minimum_partial": str(minimum),
            "rollover_unpaid": bool(policy.rollover_unpaid),
            "auto_collection_grace_days": policy.auto_collection_grace_days,
            "policy_version": policy.updated_at.isoformat(),
            "customer_can_cancel": True,
        },
    )
    core_services.audit(user, branch, "hubtel_debt.mandate_drafted", str(mandate.pk), {
        "party": party.pk, "ceiling": str(maximum), "schedule": frequency,
    })
    return mandate



@transaction.atomic
def update_draft_plan(user, branch, mandate_id, *, phone=None, channel=None,
                      frequency=None, first_due_date=None,
                      amount_per_period=None, maximum_total=None):
    """Revise UNSIGNED terms. Hubtel-approved/pending contracts are immutable.

    A cashier cannot silently change wallet authority: new terms on a signed
    mandate require provider cancellation and a completely fresh consent.
    """
    from datetime import date

    core_services.permit(user, branch, "operate_finance")
    core_services.lock_branch(branch)
    policy, _ = OnlineDebtSettings.objects.select_for_update().get_or_create(branch=branch)
    if not policy.plan_creation_enabled:
        raise ValidationError("New Direct Debit agreement setup is paused.")
    row = DebtMandate.objects.select_for_update().select_related("party").get(
        pk=mandate_id, branch=branch,
    )
    if (row.status != "draft" or row.hubtel_preapproval_id
            or row.provider_approved_at or row.collected_total != ZERO
            or row.attempts.exists()):
        raise ValidationError(
            "This agreement was already submitted for approval or collection. "
            "Cancel it and obtain fresh customer consent for different terms."
        )
    if not row.party.consent:
        raise ValidationError("The customer must consent to payment communications.")
    channel = channel or row.channel
    frequency = frequency or row.frequency
    if channel not in debit_api.DEBIT_CHANNELS or not policy.allows_channel(channel):
        raise ValidationError("Choose an allowed MTN or Telecel wallet network.")
    if frequency not in debit_api.FREQUENCIES or not policy.allows_frequency(frequency):
        raise ValidationError("Choose an available repayment interval.")
    start = first_due_date or row.first_due_date
    if isinstance(start, str):
        try:
            start = date.fromisoformat(start)
        except ValueError as exc:
            raise ValidationError("Enter a valid first payment date.") from exc
    if not isinstance(start, date) or start < timezone.localdate():
        raise ValidationError("First payment date cannot be in the past.")
    days = (start - timezone.localdate()).days
    if not policy.min_first_due_days <= days <= min(policy.max_first_due_days, 366):
        raise ValidationError("First due date is outside the configured date limit.")

    balance = outstanding(row.party)
    if not policy.min_open_balance <= balance <= policy.max_open_balance:
        raise ValidationError("The customer's outstanding balance is outside the repayment limits.")
    installment = _money(amount_per_period if amount_per_period is not None else row.amount_per_period)
    ceiling = _money(maximum_total if maximum_total is not None else row.maximum_total)
    if not policy.min_installment <= installment <= policy.max_installment:
        raise ValidationError("Installment amount is outside the configured repayment limits.")
    if ceiling <= ZERO or ceiling > balance or installment > ceiling:
        raise ValidationError("Installment and total authorization must fit the outstanding customer debt.")

    digits = debit_api.number(phone) if (phone or "").strip() else mandate_phone(row)
    fingerprint = hashlib.sha256(
        (settings.SECRET_KEY + "|hubtel-debt-wallet|" + digits).encode()
    ).hexdigest()
    blocking = ["draft", "pending", "approved", "cancel_pending", "suspended"]
    if DebtMandate.objects.filter(
        party=row.party, status__in=blocking,
    ).exclude(pk=row.pk).exists():
        raise ValidationError("Another agreement is already active for this customer.")
    if DebtMandate.objects.filter(
        phone_fingerprint=fingerprint, status__in=blocking,
    ).exclude(pk=row.pk).exists():
        raise ValidationError("This wallet is already linked to another active agreement.")
    if DebtDebitAttempt.objects.filter(
        mandate__phone_fingerprint=fingerprint,
        status__in=["sending", "pending", "unknown", "finance_review"],
    ).exists():
        raise ValidationError("Unresolved Hubtel wallet payments must be reconciled first.")

    previous = {
        "frequency": row.frequency, "due": row.first_due_date.isoformat(),
        "amount": str(row.amount_per_period), "ceiling": str(row.maximum_total),
        "wallet_suffix": row.phone_suffix,
    }
    row.phone_encrypted = client.seal(digits)
    row.phone_fingerprint = fingerprint
    row.phone_suffix = digits[-4:]
    row.channel = channel
    row.frequency = frequency
    row.first_due_date = start
    row.next_due_date = start
    row.amount_per_period = installment
    row.maximum_total = ceiling
    row.client_reference = debit_api.reference()
    row.terms_snapshot = {
        "starting_outstanding": str(balance),
        "amount": str(installment), "maximum_total": str(ceiling),
        "frequency": frequency, "starts": start.isoformat(), "network": channel,
        "partial_retry_enabled": False, "minimum_partial": "0.00",
        "rollover_unpaid": bool(policy.rollover_unpaid),
        "auto_collection_grace_days": policy.auto_collection_grace_days,
        "policy_version": policy.updated_at.isoformat(),
        "customer_can_cancel": True,
    }
    row.save()
    core_services.audit(
        user, branch, "hubtel_debt.draft_revised", str(row.pk),
        {"before": previous, "after": {
            "frequency": frequency, "due": start.isoformat(),
            "amount": str(installment), "ceiling": str(ceiling),
            "wallet_suffix": row.phone_suffix,
        }},
        category="finance", severity="high",
    )
    return row


def mandate_phone(mandate):
    return client.unseal(mandate.phone_encrypted)


def prepare_customer_authorization(mandate):
    """Only the wallet holder completing a one-time KOFAD SMS can call this."""
    if mandate.status != "draft":
        raise ValidationError("This agreement is no longer awaiting authorization.")
    # The customer has already seen and confirmed exact agreement terms.
    # Mark pending before provider POST so unknown outcomes are never repeated.
    debit_api.gate("preapproval")
    with transaction.atomic():
        locked = DebtMandate.objects.select_for_update().get(pk=mandate.pk)
        if locked.status != "draft":
            raise ValidationError("Authorization has already been requested.")
        locked.status = "pending"
        locked.save(update_fields=["status", "updated_at"])
    try:
        result = debit_api.initiate_preapproval(
            phone=mandate_phone(mandate), channel=mandate.channel,
            client_reference=mandate.client_reference,
        )
    except Exception:
        # The POST may have reached Hubtel. Leave pending, only query status.
        raise
    with transaction.atomic():
        locked = DebtMandate.objects.select_for_update().get(pk=mandate.pk)
        if locked.status == "pending":
            locked.hubtel_preapproval_id = result["provider_id"]
            locked.verification_type = result["verification_type"]
            locked.otp_prefix = result["otp_prefix"]
            locked.save(update_fields=[
                "hubtel_preapproval_id", "verification_type", "otp_prefix", "updated_at",
            ])
    return result


def refresh_preapproval(mandate):
    """Check Hubtel from the whitelisted web server; callbacks are advisory only."""
    if mandate.status not in {"pending", "approved", "suspended", "cancel_pending"}:
        return mandate
    state = debit_api.preapproval_status(
        mandate.client_reference, expected_phone=mandate_phone(mandate),
        expected_provider_id=mandate.hubtel_preapproval_id or None,
    )
    with transaction.atomic():
        row = DebtMandate.objects.select_for_update().get(pk=mandate.pk)
        row.last_provider_check_at = timezone.now()
        if state == "CANCELLED":
            # A customer may independently revoke the Hubtel-side approval.
            # Reflect that final cancellation instead of leaving a misleading
            # suspended/failed label or permitting automatic reactivation.
            row.status = "cancelled"
            row.cancelled_at = timezone.now()
        elif row.status == "pending":
            if state == "APPROVED":
                row.status = "approved"
                row.provider_approved_at = timezone.now()
            elif state in {"FAILED", "REJECTED", "EXPIRED"}:
                row.status = "failed"
        elif row.status == "approved" and state != "APPROVED":
            row.status = "suspended"
        # A cancellation request blocks all debits even if an old status
        # query still reports APPROVED.
        row.save(update_fields=["status", "provider_approved_at", "cancelled_at", "last_provider_check_at", "updated_at"])
        return row


def request_cancel(mandate):
    """Locally revoke ability to debit BEFORE contacting Hubtel for cancellation."""
    with transaction.atomic():
        # Serialize with dispatch_planned's existing branch -> mandate lock
        # order so a cancellation isn't lost during a simultaneous send claim.
        prelim = DebtMandate.objects.select_related("branch").get(pk=mandate.pk)
        core_services.lock_branch(prelim.branch)
        row = DebtMandate.objects.select_for_update().get(pk=mandate.pk)
        if row.status in {"cancelled", "settled", "failed"}:
            return row
        if row.status == "draft":
            # No preapproval ever started; calling Hubtel's wallet-wide
            # cancellation could affect another agreement for this wallet.
            row.status = "cancelled"
            row.cancelled_at = timezone.now()
            row.save(update_fields=["status", "cancelled_at", "updated_at"])
            return row
        if row.status == "cancel_pending":
            # The provider GET causes a revocation. Do not hammer it on
            # repeated button presses or an unconfirmed network response.
            if row.updated_at > timezone.now() - timedelta(minutes=15):
                return row
            row.save(update_fields=["updated_at"])
        else:
            row.status = "cancel_pending"
            row.cancellation_requested_at = timezone.now()
            row.save(update_fields=["status", "cancellation_requested_at", "updated_at"])
    try:
        debit_api.cancel_preapproval(mandate_phone(row))
    except (client.StatusUnconfirmed, ValidationError):
        # Authorization has already been revoked locally. An uncertain
        # provider response is never proof that cancellation succeeded.
        # The periodic web-server sweep will retry after the cooldown.
        return row
    with transaction.atomic():
        row = DebtMandate.objects.select_for_update().get(pk=mandate.pk)
        if row.status == "cancel_pending":
            row.status = "cancelled"
            row.cancelled_at = timezone.now()
            row.save(update_fields=["status", "cancelled_at", "updated_at"])
    return row


def payment_preview(mandate, today=None):
    """Never charge more than agreement ceiling, installment and real debt."""
    today = today or timezone.localdate()
    grace = int(mandate.terms_snapshot.get("auto_collection_grace_days", 0))
    if mandate.status != "approved" or mandate.next_due_date + timedelta(days=grace) > today:
        return ZERO
    debt = outstanding(mandate.party)
    remaining_cap = mandate.maximum_total - mandate.collected_total
    if debt <= ZERO or remaining_cap <= ZERO:
        return ZERO
    # Current installment plus previously unpaid installments, capped by debt.
    scheduled = mandate.amount_per_period
    next_date = mandate.first_due_date
    ticks = 0
    while next_date and next_date <= today and ticks < 400:
        if ticks:
            scheduled += mandate.amount_per_period
        next_date = _period_next(next_date, mandate.frequency)
        ticks += 1
    # If the signed plan disallows rollover, never bundle missed installments.
    if not mandate.terms_snapshot.get("rollover_unpaid", True):
        scheduled = mandate.amount_per_period + mandate.collected_total
    # Never exceed the signed ceiling, installment rules or ledger balance.
    return max(ZERO, min(debt, remaining_cap, scheduled - mandate.collected_total))


def plan_due_attempt(mandate):
    """Record a single deterministic due attempt. No provider POST here."""
    with transaction.atomic():
        row = DebtMandate.objects.select_for_update().select_related("party").get(pk=mandate.pk)
        if row.status != "approved" or row.next_due_date > timezone.localdate():
            return None
        grace = int(row.terms_snapshot.get("auto_collection_grace_days", 0))
        if row.next_due_date + timedelta(days=grace) > timezone.localdate():
            return None
        if DebtDebitAttempt.objects.filter(mandate=row, status__in=UNRESOLVED_CHARGE_STATES).exists():
            return None
        # No repeated probes: at most one charge attempt per period by default.
        attempts = DebtDebitAttempt.objects.filter(mandate=row, period_due_date=row.next_due_date)
        if attempts.exists():
            return None
        amount = payment_preview(row)
        if amount <= ZERO:
            return None
        return DebtDebitAttempt.objects.create(
            mandate=row, reference=debit_api.reference(),
            period_due_date=row.next_due_date, amount=amount,
            status="planned",
        )


def enqueue_due(limit=10):
    """Scheduler may queue; it must never submit money calls without gate."""
    debit_api.gate("charge")
    count = 0
    qs = DebtMandate.objects.filter(
        status="approved", next_due_date__lte=timezone.localdate(),
    ).order_by("next_due_date", "pk")[:limit]
    for mandate in qs:
        if plan_due_attempt(mandate):
            count += 1
    return count


def dispatch_planned(limit=8):
    """Exactly one provider POST per durable attempt; requires explicit debit gate."""
    from datetime import timedelta
    debit_api.gate("charge")
    posted = 0
    for attempt_id in list(DebtDebitAttempt.objects.filter(
        status="planned"
    ).order_by("created_at").values_list("pk", flat=True)[:limit]):
        with transaction.atomic():
            from core.models import Branch
            prelim = DebtDebitAttempt.objects.select_related("mandate").get(pk=attempt_id)
            core_services.lock_branch(prelim.mandate.branch)
            attempt = DebtDebitAttempt.objects.select_for_update().select_related("mandate", "mandate__party").get(pk=attempt_id)
            mandate = DebtMandate.objects.select_for_update().get(pk=attempt.mandate_id)
            if (attempt.status != "planned" or mandate.status != "approved"
                    or attempt.amount > outstanding(mandate.party)
                    or attempt.amount > mandate.maximum_total - mandate.collected_total
                    or attempt.period_due_date > timezone.localdate()
                    or DebtDebitAttempt.objects.filter(
                        mandate=mandate, status__in=UNRESOLVED_CHARGE_STATES,
                    ).exclude(pk=attempt.pk).exists()):
                if attempt.status == "planned":
                    attempt.status = "cancelled"
                    attempt.save(update_fields=["status", "updated_at"])
                continue
            # Repeat Hubtel preapproval check immediately before a debit.
            # This must not trust the previously cached approval flag.
        try:
            mandate = refresh_preapproval(mandate)
        except (client.StatusUnconfirmed, ValidationError):
            continue
        if mandate.status != "approved":
            continue
        with transaction.atomic():
            core_services.lock_branch(mandate.branch)
            attempt = DebtDebitAttempt.objects.select_for_update().select_related("mandate").get(pk=attempt_id)
            mandate = DebtMandate.objects.select_for_update().get(pk=attempt.mandate_id)
            if attempt.status != "planned" or mandate.status != "approved":
                continue
            # Provider-success and local-ledger-unposted amounts are money
            # in transit, not failed collections. Never start another debit.
            if DebtDebitAttempt.objects.filter(
                mandate=mandate, status__in=UNRESOLVED_CHARGE_STATES,
            ).exclude(pk=attempt.pk).exists():
                continue
            policy = OnlineDebtSettings.objects.select_for_update().filter(
                branch=mandate.branch,
            ).first()
            if not policy or policy.pause_auto_collection:
                continue
            local_now = timezone.localtime()
            today = timezone.localdate()
            if policy.skip_weekends and today.weekday() >= 5:
                continue
            if not policy.collection_from_hour <= local_now.hour < policy.collection_until_hour:
                continue
            grace = int(mandate.terms_snapshot.get("auto_collection_grace_days", 0))
            if attempt.period_due_date + timedelta(days=grace) > today:
                continue
            if (attempt.amount > outstanding(mandate.party)
                    or attempt.amount > mandate.maximum_total - mandate.collected_total):
                attempt.status = "cancelled"
                attempt.save(update_fields=["status", "updated_at"])
                continue
            # The branch row is locked by lock_branch: two concurrent workers
            # cannot both consume the same daily spending/attempt budget.
            sent_today = DebtDebitAttempt.objects.filter(
                mandate__branch=mandate.branch, sent_at__date=today,
            )
            totals = sent_today.aggregate(total=Sum("amount"))
            spent = totals["total"] or ZERO
            if (sent_today.count() >= policy.daily_attempt_limit
                    or spent + attempt.amount > policy.daily_collection_limit
                    or sent_today.filter(mandate=mandate).exists()):
                continue
            attempt.status = "sending"  # durable claim before irreversible POST
            attempt.sent_at = timezone.now()
            attempt.next_check_at = timezone.now() + timedelta(minutes=5)
            attempt.save(update_fields=["status", "sent_at", "next_check_at", "updated_at"])
        try:
            provider_id = debit_api.debit_charge(attempt)
        except (client.StatusUnconfirmed, client.SubmissionUnknown, ValidationError):
            # Network outcome may be unknown. Never POST again for this ref.
            DebtDebitAttempt.objects.filter(pk=attempt_id, status="sending").update(
                status="unknown",
            )
        else:
            DebtDebitAttempt.objects.filter(pk=attempt_id, status="sending").update(
                status="pending", hubtel_transaction_id=provider_id,
            )
        posted += 1
    return posted


def defer_unresolved_attempt(attempt, now=None):
    """Escalate stale provider uncertainty without inventing a payment failure.

    KOFAD keeps verifying the ORIGINAL payment reference. It never schedules a
    second charge while the original remains uncertain or paid but unposted.
    """
    now = now or timezone.now()
    policy = OnlineDebtSettings.objects.filter(branch=attempt.mandate.branch).first()
    review_hours = max(6, min(getattr(policy, "pending_review_hours", 72), 168))
    poll_hours = max(1, min(getattr(policy, "unresolved_poll_hours", 6), 24))
    first_submission = attempt.sent_at or attempt.created_at
    overdue = now >= first_submission + timedelta(hours=review_hours)
    with transaction.atomic():
        core_services.lock_branch(attempt.mandate.branch)
        row = DebtDebitAttempt.objects.select_for_update().get(pk=attempt.pk)
        if row.status not in {"sending", "pending", "unknown"}:
            return False
        row.next_check_at = now + (timedelta(hours=poll_hours) if overdue else timedelta(minutes=5))
        row.save(update_fields=["next_check_at", "updated_at"])
        if overdue:
            transitioned = DebtMandate.objects.filter(
                pk=row.mandate_id, status="approved",
            ).update(status="suspended")
            if transitioned:
                core_services.audit(
                    None, attempt.mandate.branch, "hubtel_debt.payment_unresolved_hold",
                    str(row.pk), {
                        "mandate": str(row.mandate_id),
                        "reference": row.reference,
                        "review_after_hours": review_hours,
                        "next_provider_check": row.next_check_at.isoformat(),
                    }, category="finance", severity="high",
                )
        return overdue


def settle_verified_collection(attempt_id):
    """Post an already verified paid Hubtel debit to KOFAD exactly once.

    There is NO Hubtel HTTP or charge operation here. Only a provider-paid
    marker written by independent, reference+amount+currency+ID verification
    can enter this function; callbacks and unknown statuses are excluded.
    """
    from django.db import DatabaseError
    from core import debts as ledger

    prelim = DebtDebitAttempt.objects.select_related("mandate__branch").get(pk=attempt_id)
    try:
        with transaction.atomic():
            # Same branch -> row -> mandate lock order as debit dispatch.
            core_services.lock_branch(prelim.mandate.branch)
            row = DebtDebitAttempt.objects.select_for_update().select_related(
                "mandate__party", "mandate__created_by",
            ).get(pk=attempt_id)
            if (row.status != "finance_review"
                    or row.collection_document_id
                    or not row.provider_verified_paid_at
                    or not row.hubtel_transaction_id):
                return False
            mandate = DebtMandate.objects.select_for_update().get(pk=row.mandate_id)
            doc = ledger.post_customer_payment(
                mandate.created_by, mandate.branch, {
                    "party": mandate.party_id, "amount": str(row.amount),
                    "method": "momo", "reference": row.reference,
                    "note": "Hubtel direct debit; paid status independently verified",
                }, key=str(row.pk),
            )
            row.collection_document = doc
            row.status = "settled"
            row.next_check_at = None
            row.save(update_fields=["collection_document", "status", "next_check_at", "updated_at"])
            mandate.collected_total += row.amount
            following = _period_next(row.period_due_date, mandate.frequency)
            if following and following > mandate.next_due_date:
                mandate.next_due_date = following
            mandate.save(update_fields=["collected_total", "next_due_date", "updated_at"])
    except (ValidationError, PermissionDenied, DatabaseError):
        # Do not discard paid evidence or attempt a second customer charge.
        # Insufficient remaining debt, deactivated operator, or closed books
        # need a finance adjustment; keep the amount under explicit review.
        DebtMandate.objects.filter(pk=prelim.mandate_id, status="approved").update(
            status="suspended",
        )
        return False

    # The provider-side wallet authority should be revoked as soon as the
    # final verified payment settles, even if the debit gate is now disabled.
    current = DebtMandate.objects.select_related("party").get(pk=prelim.mandate_id)
    if current.status in {"approved", "suspended"} and outstanding(current.party) == ZERO:
        try:
            request_cancel(current)
        except (client.StatusUnconfirmed, ValidationError):
            # The customer cannot be charged again if local cancellation has
            # begun. The normal cancellation reconciler retries with cooldown.
            pass
    return True


def post_verified_collections(limit=20):
    """Retry ONLY KOFAD ledger posting for independently paid debit receipts.

    Safe to run with every outgoing money feature disabled: never calls the
    Hubtel charge endpoint and never treats unknown payments as paid.
    """
    limit = max(1, min(int(limit), 100))
    ids = list(DebtDebitAttempt.objects.filter(
        status="finance_review", provider_verified_paid_at__isnull=False,
        collection_document__isnull=True,
    ).order_by("provider_verified_paid_at", "pk").values_list("pk", flat=True)[:limit])
    completed = 0
    for attempt_id in ids:
        if settle_verified_collection(attempt_id):
            completed += 1
    return completed


def reconcile_attempts(limit=15):
    """Read already-sent payment statuses even when NEW debit charges are OFF."""
    from types import SimpleNamespace
    from datetime import timedelta

    client.require_reconciliation_ready("receive")
    now = timezone.now()
    attempts = list(DebtDebitAttempt.objects.filter(
        status__in=["sending", "pending", "unknown"],
        next_check_at__lte=now,
    ).select_related("mandate")[:limit])
    completed = 0
    for attempt in attempts:
        if now < attempt.created_at + timedelta(minutes=5):
            continue
        tx = SimpleNamespace(
            kind="receive", client_reference=attempt.reference,
            amount=attempt.amount,
            hubtel_transaction_id=attempt.hubtel_transaction_id,
        )
        try:
            result, provider_id = client.check(tx)
        except (client.StatusUnconfirmed, ValidationError):
            defer_unresolved_attempt(attempt, now=now)
            continue
        if result == "pending":
            defer_unresolved_attempt(attempt, now=now)
            continue
        with transaction.atomic():
            core_services.lock_branch(attempt.mandate.branch)
            row = DebtDebitAttempt.objects.select_for_update().select_related(
                "mandate", "mandate__party",
            ).get(pk=attempt.pk)
            if row.status not in {"sending", "pending", "unknown"}:
                continue
            if result != "success":
                row.status = "failed" if result == "failed" else "finance_review"
                row.provider_code = result[:24]
                row.next_check_at = None
                row.save(update_fields=["status", "provider_code", "next_check_at", "updated_at"])
                if result == "failed":
                    # A provider-verified failed charge may roll into the next
                    # contractual period, never as a rapid smaller wallet probe.
                    mandate = DebtMandate.objects.select_for_update().get(pk=row.mandate_id)
                    if mandate.status == "approved" and mandate.next_due_date == row.period_due_date:
                        following = _period_next(row.period_due_date, mandate.frequency)
                        if following is None:
                            mandate.status = "suspended"
                            mandate.save(update_fields=["status", "updated_at"])
                        else:
                            mandate.next_due_date = following
                            mandate.save(update_fields=["next_due_date", "updated_at"])
                continue
            if not provider_id:
                # Defense in depth: client.check requires a provider ID; do
                # not record a paid marker without it even if mocked.
                row.status = "finance_review"
                row.provider_code = "missing_provider_id"
                row.next_check_at = None
                row.save(update_fields=["status", "provider_code", "next_check_at", "updated_at"])
                continue
            row.hubtel_transaction_id = provider_id
            row.provider_verified_paid_at = timezone.now()
            row.status = "finance_review"
            row.next_check_at = None
            row.save(update_fields=[
                "hubtel_transaction_id", "provider_verified_paid_at",
                "status", "next_check_at", "updated_at",
            ])
        # Provider receipt is now durably confirmed. All retries from here
        # operate on KOFAD's own ledger, NEVER another Hubtel debit POST.
        if settle_verified_collection(attempt.pk):
            completed += 1
    return completed


def cancel_fully_paid(limit=50):
    """Revoke settled debt authority and retry uncertain provider cancellations.

    All Hubtel requests run here on the whitelisted web process. A persistent
    local cancellation always blocks further charges, even when Hubtel is down.
    """
    from django.core.cache import cache

    limit = max(1, min(int(limit), 100))
    cooldown = timezone.now() - timedelta(minutes=15)
    confirmed = 0
    # Do not strand a cancelled wallet authorization merely because the
    # provider was unavailable when the customer first revoked it.
    pending = DebtMandate.objects.filter(
        status="cancel_pending", updated_at__lte=cooldown,
    ).order_by("updated_at", "pk")[:limit]
    for mandate in pending:
        if request_cancel(mandate).status == "cancelled":
            confirmed += 1

    # Round-robin scanning prevents the same first 25 unpaid customers from
    # starving later mandates that have already been settled manually.
    cursor_key = "hubtel-debt-approved-scan-cursor"
    cursor = cache.get(cursor_key)
    # Even suspended mandates must be revoked when the debt is fully paid:
    # the customer's provider-side preapproval may otherwise remain active.
    qs = DebtMandate.objects.filter(status__in=["approved", "suspended"]).select_related("party")
    if cursor:
        qs = qs.filter(pk__gt=cursor)
    rows = list(qs.order_by("pk")[:limit])
    if not rows:
        cache.delete(cursor_key)
        return confirmed
    cache.set(cursor_key, str(rows[-1].pk), timeout=86400)
    for mandate in rows:
        if outstanding(mandate.party) == ZERO:
            if request_cancel(mandate).status == "cancelled":
                confirmed += 1
    return confirmed
