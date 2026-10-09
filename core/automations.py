"""Business communication automations.

Automatic modes never bypass consent for customers. Management recipients are internal
contacts configured by an owner. Live "send" mode submits to Arkesel immediately; source
keys keep every scheduled/event message idempotent.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from . import debts as debt_service
from .models import (
    Audit, Branch, Closing, CommunicationSettings, Company, DebtSettings, Document, ManagementContact,
    Message, Party, Product, Stock,
)
from .sms.service import (
    create_automatic_customer_draft, create_internal_draft, send_automatic, send_messages_now,
)
from .sms.templates import render_for_document


ZERO = Decimal("0.00")


def _actor(preferred=None):
    if preferred and preferred.is_active:
        return preferred
    return User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()


def _company():
    return Company.objects.first() or Company()


def _communication_policy():
    return CommunicationSettings.objects.first() or CommunicationSettings.objects.create()


def _debt_policy():
    return DebtSettings.objects.first() or DebtSettings.objects.create()


def _business_phone(company=None):
    company = company or _company()
    values = [value.strip() for value in (company.phone, company.secondary_phone) if value and value.strip()]
    return " / ".join(values) or "our business"


def _location(branch, company=None):
    company = company or _company()
    return branch.address.strip() if branch.address else (company.address.strip() if company.address else branch.name)


def _render(body, data):
    def replace(match):
        key = match.group(1)
        return str(data.get(key, ""))
    return re.sub(r"\{([^{}]+)\}", replace, body)


def _apply_mode(message, mode, actor=None):
    if message and mode == "send":
        if message.channel == "whatsapp":
            from .whatsapp_delivery import configuration_error, queue_whatsapp
            if not configuration_error():
                try:
                    return queue_whatsapp(_actor(actor), message.branch, message.pk, automatic=True)
                except Exception as exc:
                    _record_automation_failure(message.branch, actor, "whatsapp", message.pk, exc)
                    message.last_error = str(exc)[:240]
                    message.save(update_fields=["last_error"])
        else:
            send_automatic(message, actor)
    return message


def prepare_document_automation(document, code, mode, actor=None, channel="sms"):
    if mode == "off" or not document.party_id or not document.party.consent:
        return None
    if code not in {"receipt", "payment"}:
        return None
    body = render_for_document(document, code)
    message = create_automatic_customer_draft(
        _actor(actor), document.branch, document.party, body,
        source_key=f"auto:{code}:{document.pk}" + (":whatsapp" if channel == "whatsapp" else ""),
        channel=channel,
    )
    return _apply_mode(message, mode, actor)


def prepare_sale_receipt(document, actor=None):
    if document.kind != "sale":
        return None
    policy = _communication_policy()
    prepare_document_automation(document, "receipt", policy.whatsapp_sale_receipt_mode, actor, "whatsapp")
    return prepare_document_automation(document, "receipt", policy.sale_receipt_mode, actor)


def prepare_payment_confirmation(document, actor=None):
    if document.kind != "collection":
        return None
    policy = _communication_policy()
    prepare_document_automation(document, "payment", policy.whatsapp_payment_confirmation_mode, actor, "whatsapp")
    return prepare_document_automation(document, "payment", policy.payment_confirmation_mode, actor)


def management_contacts(branch, field):
    filters = {"active": True, field: True}
    return ManagementContact.objects.filter(**filters).filter(
        Q(branch__isnull=True) | Q(branch=branch)
    )


def prepare_closing_notifications(closing, actor=None):
    # Queue authorized email notices in the same transaction as the closing.
    # Email failures must never prevent a finalized closing or SMS alerts.
    try:
        from .email_center import enqueue_closing_report
        enqueue_closing_report(closing)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Could not queue a closing email report')
    policy = _communication_policy()
    if policy.daily_closing_mode == "off" and policy.whatsapp_daily_closing_mode == "off":
        return []
    company = _company()
    summary = closing.summary or {}
    expected_cash = closing.expected.get("cash", "0.00")
    counted_cash = closing.counted.get("cash", "0.00")
    variance = Decimal(str(counted_cash)) - Decimal(str(expected_cash))
    data = {
        "company": company.name,
        "date": closing.date.isoformat(),
        "currency": company.currency,
        "sales_total": summary.get("sales_total", "0.00"),
        "expected_cash": expected_cash,
        "counted_cash": counted_cash,
        "cash_variance": str(variance),
        "debt_collections": summary.get("debt_collections", "0.00"),
        "expenses": summary.get("expenses_total", "0.00"),
        "staff": closing.submitted_by.get_full_name() or closing.submitted_by.username,
        "location": _location(closing.branch, company),
    }
    body = _render(policy.closing_template, data)
    sender = _actor(actor)
    created = []
    for contact in management_contacts(closing.branch, "receive_closing").distinct():
        for channel, mode in (("sms", policy.daily_closing_mode), ("whatsapp", policy.whatsapp_daily_closing_mode)):
            if mode == "off":
                continue
            message = create_internal_draft(
                sender, closing.branch, contact, body, channel=channel,
                source_key=f"auto:closing:{closing.pk}:{contact.pk}" + (":whatsapp" if channel == "whatsapp" else ""),
            )
            if channel == "whatsapp":
                _apply_mode(message, mode, sender)
            created.append(message)
    sms = [message.pk for message in created if message.channel == "sms"]
    if sms and policy.daily_closing_mode == "send" and settings.SMS_ENABLED:
        send_messages_now(sender, closing.branch, sms, automatic=True)
        for message in created:
            message.refresh_from_db()
    return created


def _debt_due_sentence(snapshot, today):
    overdue_rows = [row for row in snapshot["invoice_rows"] if row["days_overdue"] > 0]
    if overdue_rows:
        oldest = max(overdue_rows, key=lambda row: row["days_overdue"])
        return (
            f"Your oldest unpaid receipt is {oldest['days_overdue']} day(s) overdue "
            f"with {snapshot['overdue']:.2f} overdue."
        )
    if snapshot["due_today"] > 0:
        return f"{snapshot['due_today']:.2f} is due today."
    if snapshot["next_due"]:
        return f"Your next due date is {snapshot['next_due'].isoformat()}."
    return "Please review your account balance."


def _due_soon_match(snapshot, policy, today):
    configured = {int(value) for value in policy.due_soon_days.split(",") if value.strip().isdigit()}
    for row in snapshot["invoice_rows"]:
        due = row["invoice"].due_date
        if due and due >= today and (due - today).days in configured:
            return True
    return False


def _debt_stage(snapshot, policy, today):
    if snapshot["outstanding"] < policy.minimum_balance:
        return None
    if policy.overdue_enabled and snapshot["overdue"] > 0:
        return "overdue"
    if policy.due_today_enabled and snapshot["due_today"] > 0:
        return "due_today"
    if policy.due_soon_enabled and _due_soon_match(snapshot, policy, today):
        return "due_soon"
    return None


def _debt_eligible(snapshot, policy, today):
    return _debt_stage(snapshot, policy, today) is not None


def render_debt_account_message(party, today=None):
    policy = _debt_policy()
    today = today or timezone.localdate()
    snapshot = debt_service.customer_account_snapshot(party)
    if not _debt_eligible(snapshot, policy, today):
        return None
    company = _company()
    return _render(policy.message_template, {
        "company": company.name,
        "customer": party.name,
        "currency": company.currency,
        "balance": f"{snapshot['outstanding']:.2f}",
        "debt_count": snapshot["invoice_count"],
        "due_sentence": _debt_due_sentence(snapshot, today),
        "business_phone": _business_phone(company),
        "location": _location(party.branch, company),
    })


def _debt_frequency_allows(branch, party, policy, now, snapshot, channel="sms"):
    history = Message.objects.filter(
        branch=branch,
        party=party,
        channel=channel,
        source_key__startswith=f"auto:debt:{party.pk}:",
    ).order_by("-created_at")
    last = history.first()
    if last and now - last.created_at < timedelta(hours=policy.minimum_hours_between_sms):
        return False
    if snapshot["overdue"] > 0:
        last_overdue = history.filter(
            source_key__startswith=f"auto:debt:{party.pk}:overdue:"
        ).first()
        if last_overdue and now - last_overdue.created_at < timedelta(days=policy.overdue_repeat_days):
            return False
    if history.filter(created_at__gte=now - timedelta(days=7)).count() >= policy.max_sms_7_days:
        return False
    if history.filter(created_at__gte=now - timedelta(days=30)).count() >= policy.max_sms_30_days:
        return False
    return True


def run_debt_reminders(now=None):
    policy = _debt_policy()
    whatsapp_mode = _communication_policy().whatsapp_debt_reminder_mode
    if policy.delivery_mode == "off" and whatsapp_mode == "off":
        return 0
    now = now or timezone.localtime()
    today = now.date()
    if policy.skip_weekends and today.weekday() >= 5:
        return 0
    if now.time().replace(tzinfo=None) < policy.reminder_time:
        return 0

    actor = _actor()
    if not actor:
        return 0
    created = 0
    for branch in Branch.objects.filter(active=True):
        for party in Party.objects.filter(branch=branch, kind="customer", consent=True):
            snapshot = debt_service.customer_account_snapshot(party)
            if not _debt_eligible(snapshot, policy, today):
                continue
            body = render_debt_account_message(party, today)
            stage = _debt_stage(snapshot, policy, today)
            if not body or not stage:
                continue
            for channel, mode in (("sms", policy.delivery_mode), ("whatsapp", whatsapp_mode)):
                if mode == "off" or not _debt_frequency_allows(branch, party, policy, now, snapshot, channel):
                    continue
                source_key = f"auto:debt:{party.pk}:{stage}:{today.isoformat()}" + (":whatsapp" if channel == "whatsapp" else "")
                message = create_automatic_customer_draft(actor, branch, party, body, source_key=source_key, channel=channel)
                _apply_mode(message, mode, actor)
                created += 1
    return created


def run_low_stock_summary(now=None):
    policy = _communication_policy()
    if policy.low_stock_mode == "off" and policy.whatsapp_low_stock_mode == "off":
        return 0
    now = now or timezone.localtime()
    today = now.date()
    if now.time().replace(tzinfo=None) < policy.low_stock_time:
        return 0
    company = _company()
    actor = _actor()
    if not actor:
        return 0
    created = 0
    for branch in Branch.objects.filter(active=True):
        stock = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
        active = list(Product.objects.filter(active=True))
        out_count = sum(1 for product in active if stock.get(product.pk, 0) == 0)
        low_count = sum(
            1 for product in active
            if 0 < stock.get(product.pk, 0) <= product.reorder_level
        )
        if not (out_count or low_count):
            continue
        body = _render(policy.low_stock_template, {
            "company": company.name,
            "low_count": low_count,
            "out_count": out_count,
            "location": _location(branch, company),
        })
        messages_for_branch = []
        for contact in management_contacts(branch, "receive_low_stock").distinct():
            for channel, mode in (("sms", policy.low_stock_mode), ("whatsapp", policy.whatsapp_low_stock_mode)):
                if mode == "off":
                    continue
                source_key = f"auto:lowstock:{branch.pk}:{contact.pk}:{today.isoformat()}" + (":whatsapp" if channel == "whatsapp" else "")
                message = create_internal_draft(actor, branch, contact, body, source_key=source_key, channel=channel)
                if channel == "whatsapp":
                    _apply_mode(message, mode, actor)
                else:
                    messages_for_branch.append(message)
                created += 1
        if messages_for_branch and policy.low_stock_mode == "send" and settings.SMS_ENABLED:
            send_messages_now(actor, branch, [message.pk for message in messages_for_branch], automatic=True)
    return created


def run_scheduled_automations(now=None):
    return {
        "debt": run_debt_reminders(now),
        "low_stock": run_low_stock_summary(now),
    }


def _record_automation_failure(branch, actor, event, reference, exc):
    Audit.objects.create(
        branch=branch,
        actor=actor if actor and actor.is_active else None,
        action="communication.automation_failed",
        reference=str(reference)[:100],
        detail={"event": event, "error": str(exc)[:240]},
    )


def safe_prepare_sale_receipt(document_id, actor_id=None):
    document = Document.objects.select_related("branch", "party").filter(pk=document_id).first()
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    if not document:
        return None
    try:
        return prepare_sale_receipt(document, actor)
    except Exception as exc:
        _record_automation_failure(document.branch, actor, "sale_receipt", document.reference, exc)
        return None


def safe_prepare_payment_confirmation(document_id, actor_id=None):
    document = Document.objects.select_related("branch", "party").filter(pk=document_id).first()
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    if not document:
        return None
    try:
        return prepare_payment_confirmation(document, actor)
    except Exception as exc:
        _record_automation_failure(document.branch, actor, "payment_confirmation", document.reference, exc)
        return None


def safe_prepare_closing(closing_id, actor_id=None):
    closing = Closing.objects.select_related("branch", "submitted_by").filter(pk=closing_id).first()
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    if not closing:
        return []
    try:
        return prepare_closing_notifications(closing, actor)
    except Exception as exc:
        _record_automation_failure(closing.branch, actor, "daily_closing", closing.pk, exc)
        return []
