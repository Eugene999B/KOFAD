"""Business communication automations.

Automatic modes never bypass consent for customers. Management recipients are internal
contacts configured by an owner. Source keys make every scheduled/event message idempotent.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Sum
from django.utils import timezone

from . import debts as debt_service
from .models import (
    Audit, Branch, Closing, CommunicationSettings, Company, DebtSettings, Document, ManagementContact,
    Message, Party, Product, Stock,
)
from .sms.service import (
    create_automatic_customer_draft, create_internal_draft, queue_automatic,
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
    if message and mode == "queue":
        queue_automatic(message, actor)
    return message


def prepare_document_automation(document, code, mode, actor=None):
    if mode == "off" or not document.party_id or not document.party.consent:
        return None
    if code not in {"receipt", "payment"}:
        return None
    body = render_for_document(document, code)
    message = create_automatic_customer_draft(
        _actor(actor), document.branch, document.party, body,
        source_key=f"auto:{code}:{document.pk}",
    )
    return _apply_mode(message, mode, actor)


def prepare_sale_receipt(document, actor=None):
    if document.kind != "sale":
        return None
    return prepare_document_automation(
        document, "receipt", _communication_policy().sale_receipt_mode, actor
    )


def prepare_payment_confirmation(document, actor=None):
    if document.kind != "collection":
        return None
    return prepare_document_automation(
        document, "payment", _communication_policy().payment_confirmation_mode, actor
    )


def management_contacts(branch, field):
    filters = {
        "active": True,
        field: True,
    }
    return ManagementContact.objects.filter(**filters).filter(
        branch__isnull=True
    ) | ManagementContact.objects.filter(**filters, branch=branch)


def prepare_closing_notifications(closing, actor=None):
    policy = _communication_policy()
    if policy.daily_closing_mode == "off":
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
    created = []
    for contact in management_contacts(closing.branch, "receive_closing").distinct():
        message = create_internal_draft(
            _actor(actor), closing.branch, contact, body,
            source_key=f"auto:closing:{closing.pk}:{contact.pk}",
        )
        created.append(_apply_mode(message, policy.daily_closing_mode, actor))
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


def _debt_eligible(snapshot, policy, today):
    if snapshot["outstanding"] < policy.minimum_balance:
        return False
    if policy.overdue_enabled and snapshot["overdue"] > 0:
        return True
    if policy.due_today_enabled and snapshot["due_today"] > 0:
        return True
    if policy.due_soon_enabled and _due_soon_match(snapshot, policy, today):
        return True
    return False


def _debt_frequency_allows(branch, party, policy, now):
    history = Message.objects.filter(
        branch=branch,
        party=party,
        source_key__startswith=f"auto:debt:{party.pk}:",
    ).order_by("-created_at")
    last = history.first()
    if last:
        minimum = timedelta(hours=policy.minimum_hours_between_sms)
        if now - last.created_at < minimum:
            return False
    if history.filter(created_at__gte=now - timedelta(days=7)).count() >= policy.max_sms_7_days:
        return False
    if history.filter(created_at__gte=now - timedelta(days=30)).count() >= policy.max_sms_30_days:
        return False
    return True


def run_debt_reminders(now=None):
    policy = _debt_policy()
    if policy.delivery_mode == "off":
        return 0
    now = now or timezone.localtime()
    today = now.date()
    if policy.skip_weekends and today.weekday() >= 5:
        return 0
    if now.time().replace(tzinfo=None) < policy.reminder_time:
        return 0

    company = _company()
    actor = _actor()
    if not actor:
        return 0
    created = 0
    for branch in Branch.objects.filter(active=True):
        for party in Party.objects.filter(branch=branch, kind="customer", consent=True):
            snapshot = debt_service.customer_account_snapshot(party)
            if not _debt_eligible(snapshot, policy, today):
                continue
            if not _debt_frequency_allows(branch, party, policy, now):
                continue
            data = {
                "company": company.name,
                "customer": party.name,
                "currency": company.currency,
                "balance": f"{snapshot['outstanding']:.2f}",
                "debt_count": snapshot["invoice_count"],
                "due_sentence": _debt_due_sentence(snapshot, today),
                "business_phone": _business_phone(company),
                "location": _location(branch, company),
            }
            body = _render(policy.message_template, data)
            source_key = f"auto:debt:{party.pk}:{today.isoformat()}"
            message = create_automatic_customer_draft(actor, branch, party, body, source_key=source_key)
            _apply_mode(message, policy.delivery_mode, actor)
            created += 1
    return created


def run_low_stock_summary(now=None):
    policy = _communication_policy()
    if policy.low_stock_mode == "off":
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
        for contact in management_contacts(branch, "receive_low_stock").distinct():
            source_key = f"auto:lowstock:{branch.pk}:{contact.pk}:{today.isoformat()}"
            message = create_internal_draft(actor, branch, contact, body, source_key=source_key)
            _apply_mode(message, policy.low_stock_mode, actor)
            created += 1
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
