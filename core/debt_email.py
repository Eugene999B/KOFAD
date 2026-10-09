"""Consent-aware receivables email, coupled to the existing debt policy.

KOFAD does not treat a cashier-entered address as marketing consent. Account
notices must be explicitly opted in; reminder drafts require staff approval.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import debts
from .email_models import EmailLetter, EmailMailbox
from .models import Branch, DebtSettings, Document, Party

logger = logging.getLogger(__name__)


def _policy():
    return DebtSettings.objects.first() or DebtSettings()


def customer_allowed(party, recipient=None):
    return bool(party and party.kind == "customer" and party.debt_email_opt_in
                and party.email and (recipient is None or party.email.casefold() == recipient.casefold()))


def sending_ready():
    return (settings.KOFAD_EMAIL_CENTER_ENABLED and settings.KOFAD_EMAIL_ENABLED
            and settings.KOFAD_EMAIL_PROVIDER == "brevo"
            and bool(settings.KOFAD_BREVO_API_KEY))


def _mailbox():
    return EmailMailbox.objects.filter(address="accounts@kofadimpex.com", active=True).first()


def _queue(party, *, reference, subject, body, actor=None, mode=None):
    policy = _policy()
    mode = mode or policy.email_delivery_mode
    if mode == "off" or not customer_allowed(party) or not sending_ready():
        return None
    mailbox = _mailbox()
    if not mailbox:
        return None
    with transaction.atomic():
        letter, _ = EmailLetter.objects.get_or_create(
            source_key=reference[:180],
            defaults={
                "mailbox": mailbox, "direction": "outbound",
                "status": "draft" if mode == "draft" else "queued",
                "from_address": mailbox.address, "to_address": party.email,
                "subject": subject[:255], "body_text": body[:32000],
                "created_by": actor, "next_attempt_at": timezone.now(),
            })
        return letter


def queue_credit_sale(document):
    policy = _policy()
    if policy.email_delivery_mode == "off" or document.kind != "sale" or document.paid >= document.total:
        return None
    party = document.party
    if not customer_allowed(party):
        return None
    company = __import__("core.models", fromlist=["Company"]).Company.objects.first()
    currency = company.currency if company else "GHS"
    balance = document.total - document.paid
    return _queue(
        party, reference=f"debtmail:{party.pk}:opened:{document.pk}",
        subject=f"KOFAD credit sale receipt · {document.reference}",
        body=(f"Hello {party.name},\n\n"
              f"Your KOFAD credit sale {document.reference} has been recorded.\n"
              f"Sale total: {currency} {document.total:.2f}\n"
              f"Paid at checkout: {currency} {document.paid:.2f}\n"
              f"Unpaid balance: {currency} {balance:.2f}\n"
              f"Due date: {document.due_date or 'Contact KOFAD'}\n\n"
              "Please keep this record. Contact KOFAD directly if any detail is incorrect. "
              "This message is not a demand for immediate payment."),
        actor=document.created_by, mode=policy.email_delivery_mode,
    )


def queue_debt_payment(document):
    policy = _policy()
    if policy.email_delivery_mode == "off" or document.kind != "collection":
        return None
    party = document.party
    if not customer_allowed(party):
        return None
    total = debts.customer_account_snapshot(party)["outstanding"]
    company = __import__("core.models", fromlist=["Company"]).Company.objects.first()
    currency = company.currency if company else "GHS"
    return _queue(
        party, reference=f"debtmail:{party.pk}:payment:{document.pk}",
        subject=f"KOFAD payment received · {document.reference}",
        body=(f"Hello {party.name},\n\n"
              f"We have recorded a payment of {currency} {document.total:.2f}.\n"
              f"Payment reference: {document.reference}\n"
              f"Remaining recorded balance: {currency} {total:.2f}\n\n"
              "If your records differ, contact KOFAD and quote the reference above."),
        actor=document.created_by, mode=policy.email_delivery_mode,
    )


def _frequency_allows(party, policy, now, stage):
    qs = EmailLetter.objects.filter(
        direction="outbound", source_key__startswith=f"debtmail:{party.pk}:reminder:",
    ).order_by("-created_at")
    recent = qs.first()
    if recent and now - recent.created_at < timedelta(hours=policy.minimum_hours_between_sms):
        return False
    if stage == "overdue" and qs.filter(
            source_key__startswith=f"debtmail:{party.pk}:reminder:overdue:"
        ).filter(created_at__gte=now - timedelta(days=policy.overdue_repeat_days)).exists():
        return False
    if qs.filter(created_at__gte=now - timedelta(days=7)).count() >= policy.max_sms_7_days:
        return False
    return qs.filter(created_at__gte=now - timedelta(days=30)).count() < policy.max_sms_30_days


def run_debt_email_reminders(now=None):
    policy = _policy()
    if policy.email_delivery_mode == "off" or not sending_ready():
        return 0
    now = now or timezone.localtime()
    today = timezone.localtime(now).date()
    if policy.skip_weekends and today.weekday() >= 5:
        return 0
    if timezone.localtime(now).time().replace(tzinfo=None) < policy.reminder_time:
        return 0
    mailbox = _mailbox()
    if not mailbox:
        return 0
    # On the free plan, preserve allowance for OTPs and payment receipts.
    if policy.email_delivery_mode == "send":
        from .brevo_email import usage_today
        budget = usage_today()
        if budget["remaining"] <= min(50, budget["limit"] // 5):
            return 0
    from .automations import _debt_stage, _debt_due_sentence
    queued = 0
    for party in Party.objects.filter(branch__active=True, kind="customer",
                                      debt_email_opt_in=True).exclude(email="").iterator(chunk_size=100):
        snapshot = debts.customer_account_snapshot(party)
        stage = _debt_stage(snapshot, policy, today)
        if not stage or not _frequency_allows(party, policy, now, stage):
            continue
        currency = (__import__("core.models", fromlist=["Company"]).Company.objects.first()
                    or __import__("core.models", fromlist=["Company"]).Company()).currency
        notice = _queue(
            party, reference=f"debtmail:{party.pk}:reminder:{stage}:{today.isoformat()}",
            subject=f"KOFAD account update · {stage.replace('_', ' ').title()}",
            body=(f"Hello {party.name},\n\n"
                  f"Your current KOFAD outstanding balance is {currency} {snapshot['outstanding']:.2f}.\n"
                  f"{_debt_due_sentence(snapshot, today)}\n"
                  f"Unpaid receipts: {snapshot['invoice_count']}\n\n"
                  "If you have recently paid or need to discuss your account, "
                  "please contact KOFAD Customer Care. We will review any discrepancy."),
            mode=policy.email_delivery_mode,
        )
        if notice:
            queued += 1
        if queued >= 75:
            break
    return queued


def email_still_allowed(source_key, recipient):
    """Evaluate stored source and current account preference before sending."""
    if not (source_key or "").startswith("debtmail:"):
        return True
    try:
        _, party_id, kind, *rest = source_key.split(":")
        party = Party.objects.filter(pk=int(party_id), kind="customer").first()
    except (ValueError, TypeError):
        return False
    if not customer_allowed(party, recipient):
        return False
    if kind == "reminder":
        if not rest:
            return False
        stage = rest[0]
        policy = _policy()
        if policy.email_delivery_mode == "off":
            return False
        snapshot = debts.customer_account_snapshot(party)
        from .automations import _debt_stage
        return _debt_stage(snapshot, policy, timezone.localdate()) == stage
    return kind in {"opened", "payment"}
