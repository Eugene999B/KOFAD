"""Evidence-backed, bounded cross-system questions for the staff chat.

Use KOFAD's existing ORM and approval boundaries. Never execute generated SQL,
publish email bodies, expose credentials, or contact external providers here.
"""
import re
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from .telegram_intelligence import _branches, _role


def _window(question):
    """Inclusive Ghana-local dates, bounded to avoid costly historical scans."""
    today = timezone.localdate()
    text = question.casefold()
    dates = []
    pattern = (
        r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)|"
        r"(?<!\d)\d{1,2}/\d{1,2}/\d{4}(?!\d)|"
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+"
        r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}\b"
    )
    formats = ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y")
    for match in re.finditer(pattern, text):
        raw = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", match.group())
        found = None
        for fmt in formats:
            try:
                found = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        if not found:
            raise ValueError("I couldn't recognize one of those dates. Try 2026-10-01 to 2026-10-23.")
        dates.append(found)
    if len(dates) > 2:
        raise ValueError("Please provide one date or one start and end date.")
    if len(dates) == 2:
        first, last = dates
    elif dates:
        first = last = dates[0]
    elif "yesterday" in text:
        first = last = today - timedelta(days=1)
    elif re.search(r"\btoday\b", text):
        first = last = today
    elif "last month" in text:
        last = today.replace(day=1) - timedelta(days=1)
        first = last.replace(day=1)
    elif "this month" in text:
        first, last = today.replace(day=1), today
    elif "last week" in text:
        last = today - timedelta(days=today.weekday() + 1)
        first = last - timedelta(days=6)
    elif "this week" in text:
        first, last = today - timedelta(days=today.weekday()), today
    else:
        days = re.search(r"\b(?:last|past|previous)\s+(\d{1,3})\s+days?\b", text)
        amount = int(days.group(1)) if days else 7
        first, last = today - timedelta(days=amount - 1), today
    if last < first:
        raise ValueError("The end date must be on or after the start date.")
    if (last - first).days > 365:
        raise ValueError("Choose a date range of 366 days or less for an accurate, fast report.")
    start = timezone.make_aware(datetime.combine(first, time.min), timezone.get_current_timezone())
    end = timezone.make_aware(datetime.combine(last + timedelta(days=1), time.min), timezone.get_current_timezone())
    return first, last, start, end


def _money(amount):
    return f"GHS {amount or Decimal('0.00'):,.2f}"


def _status_counts(rows, field="status"):
    return ", ".join(
        f"{row[field].replace('_', ' ')}: {row['n']}"
        for row in rows.values(field).annotate(n=Count("pk")).order_by("-n", field)[:12]
    ) or "no matching records"


def _momo(user, start, end):
    from core.models import Payment
    from marketplace.models import OnlineOrder

    branches = _branches(user)
    local = Payment.objects.filter(
        document__branch__in=branches, document__kind__in=["sale", "collection"],
        document__created_at__gte=start, document__created_at__lt=end, method="momo",
    ).exclude(document__correction__status="approved")
    paid = local.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    customers = local.filter(document__party__kind="customer").values("document__party_id").distinct().count()
    unattributed = local.filter(document__party__isnull=True).count()
    online = OnlineOrder.objects.filter(
        branch__in=branches, paid_at__gte=start, paid_at__lt=end,
        payment_status="paid",
    ).filter(Q(payment_channel__icontains="momo") | Q(payment_channel__icontains="mobile_money"))
    return (
        "MoMo activity\n"
        f"Recorded POS/debt payment entries: {local.count()} totaling {_money(paid)}.\n"
        f"Distinct linked customers using MoMo: {customers}."
        + (f" {unattributed} entry/entries have no linked customer identity." if unattributed else "")
        + f"\nSeparately, verified-paid online orders tagged MoMo: {online.count()}; "
          f"distinct online customer accounts: {online.values('customer_id').distinct().count()}.\n"
          "POS entries are dated by their parent document creation; online orders by paid_at. "
          "The sources may overlap, so do not add them together. "
          "An entry isn't proof that a provider settled to the bank."
    )


def _debts(user, start, end):
    from .models import Allocation, Document

    branches = _branches(user)
    new = Document.objects.filter(
        branch__in=branches, kind="sale", created_at__gte=start, created_at__lt=end
    )
    # Bound invoice-level reconciliation: never silently sum a truncated population.
    invoices = list(new.order_by("created_at").values(
        "id", "total", "paid", "party_id", "due_date"
    )[:5001])
    if len(invoices) > 5000:
        return ("More than 5,000 invoices were created in that period. "
                "Narrow the dates; no partial outstanding total is shown.")
    ids = [item["id"] for item in invoices]
    applied = dict(
        Allocation.objects.filter(invoice_id__in=ids)
        .exclude(payment_document__correction__status="approved")
        .values("invoice_id").annotate(value=Sum("amount"))
        .values_list("invoice_id", "value")
    ) if ids else {}
    outstanding = []
    for invoice in invoices:
        due = invoice["total"] - invoice["paid"] - (applied.get(invoice["id"]) or Decimal("0.00"))
        if due > 0:
            outstanding.append((invoice, due))
    count = len(outstanding)
    overdue = [(invoice, amt) for invoice, amt in outstanding if invoice["due_date"] and invoice["due_date"] < timezone.localdate()]
    collections = Document.objects.filter(
        branch__in=branches, kind="collection", created_at__gte=start, created_at__lt=end
    ).exclude(correction__status="approved")
    return (
        "Customer debt and collections\n"
        f"New sales invoices in the period: {len(invoices)}; distinct linked customers: "
        f"{len({x['party_id'] for x in invoices if x['party_id']})}.\n"
        f"Those invoices still owing now: {count}; current unpaid balance {_money(sum((a for _, a in outstanding), Decimal('0')))}.\n"
        f"Of those, {len(overdue)} passed their stated due date; current balance {_money(sum((a for _, a in overdue), Decimal('0')))}.\n"
        f"Collection documents created during the period: {collections.count()}, recorded amount "
        f"{_money(collections.aggregate(total=Sum('total'))['total'])}.\n"
        "The date range selects when invoices were created; outstanding and overdue are assessed NOW, "
        "not reconstructed historical balances. Missing due dates cannot be classified overdue; "
        "verify allocations and the configured grace policy in the Debt Desk."
    )


def _arkesel_balance():
    """Read-only provider balance, on request and at most once per five minutes."""
    import requests
    from django.conf import settings
    from django.core.cache import cache

    if getattr(settings, "SMS_PROVIDER", "") != "arkesel" or not settings.ARKESEL_API_KEY:
        return "not available (Arkesel account access is not configured)."
    key = "kofad:staff-ai:arkesel-sms-credit:v1"
    try:
        cached = cache.get(key)
        if isinstance(cached, int) and cached >= 0:
            return f"{cached:,} credits (provider reading cached for up to five minutes)."
    except (ConnectionError, ValueError, TypeError):
        pass
    try:
        response = requests.get(
            "https://sms.arkesel.com/api/v2/clients/balance-details",
            headers={"api-key": settings.ARKESEL_API_KEY, "Accept": "application/json"},
            timeout=(3, 5),
        )
        if response.status_code != 200:
            return "not available (Arkesel did not confirm the balance)."
        data = response.json()
        if not isinstance(data, dict) or data.get("status") != "success":
            return "not available (provider response was not a verified success)."
        details = data.get("data")
        if not isinstance(details, dict):
            return "not available (provider balance missing)."
        raw = details.get("sms_balance")
        if isinstance(raw, bool) or not str(raw).isdecimal():
            return "not available (provider returned an invalid credit count)."
        value = int(raw)
        if value < 0 or value > 10**12:
            return "not available (provider returned an out-of-range credit count)."
        try:
            cache.set(key, value, timeout=300)
        except (ConnectionError, ValueError, TypeError):
            pass
        return f"{value:,} credits (read-only Arkesel balance check just completed)."
    except (requests.RequestException, ValueError, TypeError):
        return "not available (Arkesel could not be reached or returned an invalid response)."


def _sms(user, start, end, question):
    from .models import Message, SmsAttempt

    q = question.casefold()
    balance_only = bool(re.search(r"\b(?:left|remaining|balance|credits?|quota)\b", q))
    activity_requested = bool(re.search(
        r"\b(?:sent|delivered|failed|received|how many messages|history|attempts|types|category)\b", q
    ))
    if balance_only and not activity_requested:
        return "Arkesel SMS credits available: " + _arkesel_balance()
    branches = _branches(user)
    messages = Message.objects.filter(
        branch__in=branches, channel="sms", created_at__gte=start, created_at__lt=end
    )
    attempts = SmsAttempt.objects.filter(
        message__branch__in=branches, message__channel="sms", started_at__gte=start, started_at__lt=end
    )
    submitted = attempts.filter(status__in=["accepted", "delivered", "undelivered", "expired"]).values("message_id").distinct().count()
    delivered = attempts.filter(status="delivered").values("message_id").distinct().count()
    failed = attempts.filter(status__in=["failed", "rejected", "undelivered", "expired"]).values("message_id").distinct().count()
    # Failure and delivery counters can overlap for messages with multiple retries.
    result = (
        "SMS activity\n"
        f"Messages drafted/created: {messages.count()}; statuses: {_status_counts(messages)}.\n"
        f"Unique messages with provider-submitted attempts in period: {submitted}; "
        f"with recorded delivery: {delivered}; with failed/undelivered attempt(s): {failed}.\n"
        f"Attempt statuses: {_status_counts(attempts)}.\n"
        "Submission is not delivery, and retried messages can appear in both success and failure groups."
    )
    if any(x in question.casefold() for x in ("left", "balance", "credit", "remaining", "quota")):
        result += "\nRemaining Arkesel SMS credits: " + _arkesel_balance()
    if any(x in question.casefold() for x in ("type", "kind", "category", "what messages")):
        debt = messages.filter(Q(source_key__startswith="debt:") | Q(source_key__startswith="auto:debt:")).count()
        manual = messages.filter(manual_override=True).count()
        result += (
            f"\nKnown classifications from stored metadata: debt-related {debt}; manual override {manual}. "
            "Other messages are not safely classifiable from these fields alone."
        )
    return result


def _email(user, start, end, question):
    from .email_center import visible_mailboxes
    from .email_models import EmailLetter

    visible = visible_mailboxes(user)
    if not visible.exists() and not user.is_superuser:
        return "No authorized email mailboxes are available to this staff account."
    q = question.casefold()
    incoming_requested = bool(re.search(
        r"\b(?:received|receive|incoming|inbox|inbound|got|gotten|arrived|new emails?|any emails?)\b", q
    )) and not bool(re.search(r"\b(?:outgoing|we sent|emails? sent by us|submitted to provider)\b", q))
    if incoming_requested:
        received = EmailLetter.objects.filter(
            mailbox__in=visible, direction="inbound",
            created_at__gte=start, created_at__lt=end,
        )
        count = received.count()
        result = (
            f"Yes — {count} incoming email(s) are recorded in your authorized KOFAD inboxes for that period."
            if count else
            "No incoming email is recorded in your authorized KOFAD inboxes for that period."
        )
        if count and re.search(r"\b(?:who|which|sender|address|subject|from|details?)\b", q):
            result += "\nMost recent received messages (metadata only):"
            for item in received.order_by("-created_at").values(
                "from_address", "subject", "mailbox__address"
            )[:5]:
                result += (
                    f"\n• {item['from_address']} → {item['mailbox__address']} | "
                    f"{item['subject'][:90] or '(no subject)'}"
                )
        result += "\nThis reflects KOFAD's received-email records, not a live provider inbox sync."
        return result
    letters = EmailLetter.objects.filter(mailbox__in=visible, direction="outbound")
    initiated = letters.filter(created_at__gte=start, created_at__lt=end)
    submitted = letters.filter(submitted_at__gte=start, submitted_at__lt=end)
    result = (
        "Email Centre activity (your authorized mailboxes)\n"
        f"Outgoing letters created in period: {initiated.count()}; statuses: {_status_counts(initiated)}.\n"
        f"Submitted to provider in period: {submitted.count()}; among these, "
        f"delivery confirmed: {submitted.filter(delivery_status='delivered').count()}; "
        f"delivery problems: {submitted.filter(delivery_status__in=['soft_bounce','hard_bounce','blocked','invalid_email','error']).count()}.\n"
        "Submission time and delivery status are separate facts; provider acceptance is not inbox delivery."
    )
    q = question.casefold()
    if any(x in q for x in ("address", "recipient", "subject", "type", "kind", "which mail", "what messages")):
        sample = list(initiated.order_by("-created_at").values(
            "to_address", "subject", "status", "mailbox__address"
        )[:8])
        if sample:
            result += "\nRecent message metadata (not email bodies):"
            for row in sample:
                result += (
                    f"\n• {row['mailbox__address']} → {row['to_address']} | "
                    f"{row['subject'][:90] or '(no subject)'} | {row['status']}"
                )
        else:
            result += "\nNo outgoing message metadata matched these dates."
    # The customer/transactional notice outboxes are separate from mail centre letters.
    if user.is_superuser:
        from .models import EmailNotice as InternalNotice
        from marketplace.models import EmailNotice as CustomerNotice

        internal = InternalNotice.objects.filter(created_at__gte=start, created_at__lt=end)
        customer = CustomerNotice.objects.filter(created_at__gte=start, created_at__lt=end)
        result += (
            f"\nOther system email outboxes (separate, do not add to mailbox totals): "
            f"internal notices {internal.count()} ({_status_counts(internal)}); "
            f"market notices {customer.count()} ({_status_counts(customer)})."
        )
        if any(x in q for x in ("address", "recipient", "subject", "type", "kind", "which mail", "what messages")):
            for row in internal.order_by("-created_at").values("recipient", "category", "subject", "status")[:5]:
                result += (
                    f"\n• Internal notice → {row['recipient']} | {row['category'][:40]} | "
                    f"{row['subject'][:80]} | {row['status']}"
                )
            for row in customer.order_by("-created_at").values("email", "subject", "status")[:5]:
                result += (
                    f"\n• Market notice → {row['email']} | {row['subject'][:80]} | {row['status']}"
                )
            result += "\nThese are message metadata only, not private email bodies."
    return result


def _diagnostics(user, start, end):
    if not user.is_superuser and not user.has_perm("core.manage_company"):
        return "Detailed infrastructure health is restricted to KOFAD system management."
    from .integration_health import catalog
    from .models import SmsAttempt
    from marketplace.models import MarketPaymentAttempt
    from .email_models import EmailLetter

    health = catalog()
    affected = [
        row for row in health["items"]
        if row["status"] == "issue" and not row.get("planned")
    ]
    uncertain = [
        row for row in health["items"]
        if row["status"] == "warning" and not row.get("planned")
    ]
    findings = [f"System diagnostics (local evidence; no live provider probe)\n"
                f"Configured integrations requiring attention: {len(affected)}; "
                f"unverified/warning integrations: {len(uncertain)}."]
    for row in affected[:6]:
        reason = "; ".join(row.get("issues") or [])[:190]
        findings.append(f"• {row['name']}: {reason or 'Review configuration and activity.'}")
    error_sms = SmsAttempt.objects.filter(
        started_at__gte=start, started_at__lt=end,
        status__in=["failed", "rejected", "undelivered"],
    ).count()
    error_mail = EmailLetter.objects.filter(
        created_at__gte=start, created_at__lt=end, direction="outbound",
        status__in=["failed", "uncertain"],
    ).count()
    error_payment = MarketPaymentAttempt.objects.filter(
        created_at__gte=start, created_at__lt=end,
        status__in=["failed", "rejected", "cancelled"],
    ).count()
    findings.append(
        f"Recorded exceptions in the period: SMS failed/undelivered attempts {error_sms}; "
        f"departmental emails failed/uncertain {error_mail}; online payment attempts marked failed/rejected/cancelled {error_payment}."
    )
    findings.append(
        "A local status or error counter is a lead for investigation, not proof of an outage. "
        "For definitive Railway deploy state, provider credits, and Cloudflare routing, "
        "inspect their authenticated consoles and relevant logs."
    )
    return "\n".join(findings)


def answer_operational_question(user, question, *, context=""):
    """Select multiple verified tools; report both scope and evidence limitations."""
    q = " ".join(question.casefold().split())
    followup = bool(re.search(r"\b(?:what about|how about|and last|and this|same for)\b", q))
    topics = []
    # Actual posted transaction questions should not fall through to a generic welcome
    # or a verified-closing-only summary. Keep causal questions with insight tools.
    about_sales = bool(re.search(r"\b(?:sell|sold|sales?|selling|revenue|turnover)\b", q))
    wants_explanation = bool(re.search(
        r"\b(?:why|forecast|predict|prediction|trend|compare|comparison|cause|"
        r"reason|growth|increase|decrease|changed|analysis|analyse|analyze)\b", q
    ))
    if about_sales and not wants_explanation and not re.search(
        r"\b(?:start|create|make|post|record|complete|refund|reverse)\s+(?:a\s+)?sale\b", q
    ):
        topics.append("sales_activity")
    if re.search(r"\b(?:momo|mobile money|mobile-money)\b", q):
        topics.append("momo")
    if re.search(r"\b(?:debt|debts|receivable|receivables|overdue|owing|owed|invoice|invoices)\b", q) and (
        re.search(r"\b(?:how many|count|number|between|from|last month|this month|last week|this week|past \d+ days)\b", q)
        or re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}", q)
    ):
        topics.append("debt_activity")
    if re.search(r"\b(?:sms|text messages?|arkesel)\b", q):
        topics.append("sms")
    if re.search(r"\b(?:email|emails|e-mails?|mails?|brevo|gmail)\b", q):
        topics.append("email")
    if re.search(r"\b(?:system health|diagnos\w*|outages?|what.s wrong|system errors?|system issues?|what is broken|connections? status)\b", q):
        topics.append("diagnostics")
    if not topics and followup and context in {"sales_activity", "momo", "debt_activity", "sms", "email", "diagnostics"}:
        topics.append(context)
    if not topics:
        return None
    # Let genuine send/write requests continue through existing safe review guidance.
    if re.search(r"\b(?:send|dispatch|delete|edit|transfer|charge|pay|approve)\b", q) and not re.search(
        r"\b(?:sent|sending|payment|payments|paid)\b", q
    ):
        return None
    try:
        first, last, start, end = _window(question)
    except ValueError as error:
        return str(error), "invalid"
    roles = _role(user)
    permitted = []
    blocked = []
    for topic in dict.fromkeys(topics):
        if topic in {"sales_activity", "momo", "debt_activity"} and not roles["management"]:
            blocked.append("financial reports")
            continue
        if topic == "sms" and not (
            user.is_superuser or user.has_perm("core.send_messages") or user.has_perm("core.manage_company")
        ):
            blocked.append("SMS operations")
            continue
        if topic == "diagnostics" and not (user.is_superuser or user.has_perm("core.manage_company")):
            blocked.append("system diagnostics")
            continue
        permitted.append(topic)
    if not permitted:
        return "Your current staff permissions don't allow " + ", ".join(dict.fromkeys(blocked)) + ".", "denied"
    results = [f"KOFAD record analysis · {first:%d %b %Y}–{last:%d %b %Y} · authorized scope only"]
    for topic in permitted[:5]:
        if topic == "sales_activity":
            from .staff_ai_sales import posted_sales
            response, _ = posted_sales(user, question)
            results.append(response)
        elif topic == "momo":
            results.append(_momo(user, start, end))
        elif topic == "debt_activity":
            results.append(_debts(user, start, end))
        elif topic == "sms":
            results.append(_sms(user, start, end, question))
        elif topic == "email":
            results.append(_email(user, start, end, question))
        elif topic == "diagnostics":
            results.append(_diagnostics(user, start, end))
    if blocked:
        results.append("Not included due to your account permissions: " + ", ".join(dict.fromkeys(blocked)) + ".")
    if len(permitted) == 1 and not blocked and permitted[0] in {"sms", "email"} and len(results[1]) < 450:
        return results[1], permitted[0]
    return "\n\n".join(results), permitted[0] if len(permitted) == 1 else "operations"
