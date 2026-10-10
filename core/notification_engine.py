"""Consent-aware business intelligence notifications.

No marketing to customers; this module covers explicitly subscribed staff only.
SMTP and SMS each have an independent production kill switch.
"""
import hashlib
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.html import escape

from .models import Access, Branch, Closing, Company, EmailNotice, ManagementContact
from .sms.service import create_direct_draft, normalize_phone, send_automatic

log = logging.getLogger(__name__)
REPORT_PERMISSIONS = ("core.manage_company", "core.view_reports", "core.operate_finance")
NOTICE_FLAGS = {
    "daily": "email_daily_closing",
    "weekly": "email_weekly_review",
    "monthly": "email_monthly_review",
    "critical": "email_critical_alerts",
}
ZERO = Decimal("0")


def _amount(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def _company():
    return Company.objects.first() or Company()


def _authorised(user, branch):
    return user.is_active and any(user.has_perm(p) for p in REPORT_PERMISSIONS) and (
        user.is_superuser or Access.objects.filter(user=user, branches=branch).exists()
    )


def _recipients(branch, flag):
    qs = User.objects.filter(is_active=True).exclude(email="").filter(
        Q(is_superuser=True) | Q(access__branches=branch)
    ).select_related("access").distinct().order_by("pk")
    for user in qs:
        access = getattr(user, "access", None)
        if not access or not getattr(access, flag, False) or not _authorised(user, branch):
            continue
        try:
            validate_email(user.email)
        except ValidationError:
            continue
        yield user


def _queue(user, branch, category, key, subject, body):
    notice, created = EmailNotice.objects.get_or_create(
        source_key=key,
        defaults={
            "recipient_user": user, "branch": branch, "recipient": user.email,
            "category": category, "subject": subject[:180], "body": body,
        },
    )
    return int(created)


def _closing_numbers(closing):
    summary = closing.summary or {}
    expected = _amount((closing.expected or {}).get("cash"))
    counted = _amount((closing.counted or {}).get("cash"))
    return {
        "sales": _amount(summary.get("sales_total")),
        "expenses": _amount(summary.get("expenses_total")),
        "collections": _amount(summary.get("debt_collections")),
        "expected_cash": expected,
        "counted_cash": counted,
        "variance": counted - expected,
    }


def _short_cash_notice(closing, numbers, severe=False):
    code = closing.branch.code.upper()[:12]
    prefix = "CRITICAL" if severe else "Close"
    return (
        f"KOFAD {prefix} {code} {closing.date:%d/%m}: "
        f"sales GHS {numbers['sales']:,.0f}; "
        f"expenses {numbers['expenses']:,.0f}; "
        f"cash variance {numbers['variance']:+,.0f}. Check dashboard."
    )


def _staff_sms(closing, users, numbers, severe):
    if not (getattr(settings, "SMS_STAFF_NOTICES_ENABLED", False) and settings.SMS_ENABLED):
        return 0
    from .sms.service import estimate
    sender = User.objects.filter(is_active=True, is_superuser=True).first()
    if sender is None:
        return 0
    body = _short_cash_notice(closing, numbers, severe=severe)
    _, segments = estimate(body)
    if segments != 1:
        log.warning("Skipping staff SMS that would require multiple credits")
        return 0

    current_contacts = ManagementContact.objects.filter(active=True, receive_closing=True).filter(
        Q(branch=closing.branch) | Q(branch__isnull=True)
    )
    known = set()
    for contact in current_contacts:
        try:
            known.add(normalize_phone(contact.phone))
        except ValidationError:
            pass

    made = 0
    recipient_limit = max(0, getattr(settings, "SMS_STAFF_DAILY_MAX_RECIPIENTS", 4))
    for user in users:
        if made >= recipient_limit:
            break
        access = getattr(user, "access", None)
        if not access:
            continue
        selected = (access.sms_critical_alerts or access.sms_daily_closing) if severe else access.sms_daily_closing
        if not selected:
            continue
        try:
            number = normalize_phone(access.recovery_phone)
        except ValidationError:
            continue
        # Existing management closing SMS must not be billed twice.
        if number in known:
            continue
        key = f"staff:{'critical' if severe else 'closing'}:{closing.pk}:{user.pk}"
        try:
            message = create_direct_draft(sender, closing.branch, body,
                phone=number, label=user.get_full_name() or user.username, source_key=key)
            if message.status == "draft":
                send_automatic(message, sender)
            made += 1
            known.add(number)
        except Exception:
            log.exception("Staff SMS suppressed after provider/validation failure")
    return made


def queue_closing_reports(closing):
    if not getattr(settings, "EMAIL_AUTOMATIONS_ENABLED", False) and not getattr(settings, "SMS_STAFF_NOTICES_ENABLED", False):
        return 0
    company = _company()
    n = _closing_numbers(closing)
    min_critical = max(
        _amount(company.closing_tolerance),
        _amount(getattr(settings, "NOTIFICATION_CRITICAL_VARIANCE_GHS", 500)),
    )
    severe = abs(n["variance"]) >= min_critical and abs(n["variance"]) > 0
    subject = f"KOFAD closing · {closing.branch.name} · {closing.date:%d %b %Y}"
    actions = []
    if severe:
        actions.append("URGENT: Verify the material cash variance; review receipts and obtain independent approval.")
    elif n["variance"] != 0:
        actions.append("Review the cash difference against the configured closing tolerance.")
    if n["expenses"] > n["sales"] and n["sales"] > 0:
        actions.append("Expenses exceeded recorded sales today; examine transaction categories.")
    if not actions:
        actions.append("No threshold-based exceptions identified in this closing.")
    body = (
        f"{company.name} | VERIFIED BUSINESS LOCATION REPORT\n"
        f"Location: {closing.branch.name}\nClosing: {closing.date:%d %B %Y}\n"
        f"Recorded sales: GHS {n['sales']:,.2f}\n"
        f"Expenses: GHS {n['expenses']:,.2f}\n"
        f"Debt collections: GHS {n['collections']:,.2f}\n"
        f"Expected cash: GHS {n['expected_cash']:,.2f}\n"
        f"Counted cash: GHS {n['counted_cash']:,.2f}\n"
        f"Cash variance: GHS {n['variance']:+,.2f}\n\n"
        + "Attention / next actions:\n" + "\n".join("- " + a for a in actions)
        + "\n\nSource: KOFAD daily closing records. Sales minus expenses is NOT net profit.\n"
        + "Sign in to KOFAD to investigate. Do not reply with account passwords."
    )
    created = 0
    if getattr(settings, "EMAIL_AUTOMATIONS_ENABLED", False):
        for user in _recipients(closing.branch, "email_daily_closing"):
            created += _queue(user, closing.branch, "daily",
                f"close:{closing.pk}:daily:{user.pk}", subject, body)
        if severe:
            for user in _recipients(closing.branch, "email_critical_alerts"):
                created += _queue(user, closing.branch, "critical",
                    f"close:{closing.pk}:critical:{user.pk}", "ACTION REQUIRED · " + subject, body)
    if getattr(settings, "SMS_STAFF_NOTICES_ENABLED", False):
        # One SMS per opted-in number: critical takes priority over routine closing.
        users = list(_recipients(closing.branch, "email_critical_alerts" if severe else "email_daily_closing"))
        # Independently include SMS-only subscribers, who need no email address.
        qs = User.objects.filter(is_active=True).filter(
            Q(is_superuser=True) | Q(access__branches=closing.branch)
        ).select_related("access").distinct()
        candidates = {u.pk: u for u in users}
        for user in qs:
            access = getattr(user, "access", None)
            flag = "sms_critical_alerts" if severe else "sms_daily_closing"
            selected = bool(access and getattr(access, flag, False))
            if severe and access:
                selected = selected or access.sms_daily_closing
            if selected and _authorised(user, closing.branch):
                candidates[user.pk] = user
        created += _staff_sms(closing, candidates.values(), n, severe)
    return created


def _window_values(branch, first, last):
    rows = list(Closing.objects.filter(branch=branch, date__gte=first, date__lte=last))
    sales = expenses = collections = cash_variance = ZERO
    flags = []
    for closing in rows:
        n = _closing_numbers(closing)
        sales += n["sales"]
        expenses += n["expenses"]
        collections += n["collections"]
        cash_variance += n["variance"]
        if abs(n["variance"]) > _amount(_company().closing_tolerance):
            flags.append(str(closing.date))
    return {
        "days": len(rows), "sales": sales, "expenses": expenses,
        "collections": collections, "cash_variance": cash_variance, "variance_dates": flags,
    }


def _period_report(branch, category, first, last):
    company = _company()
    now = _window_values(branch, first, last)
    days = (last - first).days + 1
    previous = _window_values(branch, first - timedelta(days=days), first - timedelta(days=1))
    comparison = "Previous period had no recorded sales."
    if previous["sales"] > 0:
        change = ((now["sales"] - previous["sales"]) / previous["sales"]) * 100
        comparison = f"Recorded sales change: {change:+.1f}% versus comparable preceding period."
    actions = []
    if not now["days"]:
        actions.append("No closings recorded for this period. Check whether reporting was completed.")
    if now["variance_dates"]:
        actions.append("Review cash variance on: " + ", ".join(now["variance_dates"][:12]))
    if previous["sales"] > 0 and now["sales"] < previous["sales"] * Decimal("0.8"):
        actions.append("Sales fell over 20%; investigate demand, availability and branch operations.")
    if not actions:
        actions.append("No automatic critical trend detected; review store-specific exceptions.")
    return (
        f"{company.name} | {category.upper()} BUSINESS ANALYSIS\n"
        f"Location: {branch.name}\nPeriod: {first:%d %b %Y} - {last:%d %b %Y}\n"
        f"Recorded daily closings: {now['days']}\n"
        f"Sales: GHS {now['sales']:,.2f}\nExpenses: GHS {now['expenses']:,.2f}\n"
        f"Debt collections: GHS {now['collections']:,.2f}\n"
        f"Net cumulative cash variance: GHS {now['cash_variance']:+,.2f}\n"
        f"{comparison}\n\nRecommended follow-ups:\n"
        + "\n".join("- " + item for item in actions)
        + "\n\nAnalysis uses submitted closings, not a full P&L. Cash variance offsets may conceal daily exceptions.\n"
        + "Sign in to KOFAD for details."
    )


def run_staff_scheduled_reports(now=None):
    if not getattr(settings, "EMAIL_AUTOMATIONS_ENABLED", False):
        return 0
    now = timezone.localtime(now or timezone.now())
    today = now.date()
    if now.hour < 7:
        return 0
    windows = []
    if today.weekday() == 0:
        windows.append(("weekly", today - timedelta(days=7), today - timedelta(days=1), "email_weekly_review"))
    if today.day == 1:
        first = today.replace(day=1)
        last = first - timedelta(days=1)
        windows.append(("monthly", last.replace(day=1), last, "email_monthly_review"))
    queued = 0
    for branch in Branch.objects.filter(active=True):
        for category, first, last, flag in windows:
            body = _period_report(branch, category, first, last)
            subject = f"KOFAD {category} analysis | {branch.name} | {last:%d %b %Y}"
            for user in _recipients(branch, flag):
                key = f"report:{category}:{branch.pk}:{first:%Y%m%d}:{user.pk}"
                queued += _queue(user, branch, category, key, subject, body)
    return queued


def _may_deliver(notice):
    if notice.category == "pos_transaction":
        from marketplace.models import CustomerAccount
        from .models import Document
        document = Document.objects.select_related("party", "branch").filter(
            pk=notice.recipient_ref, kind__in=("sale", "collection")
        ).first()
        if not document or not document.party or document.party.kind != "customer":
            return False
        try:
            phone = normalize_phone(document.party.phone)
        except ValidationError:
            return False
        customer = CustomerAccount.objects.filter(phone=phone, active=True).first()
        return bool(customer and customer.verified_at and customer.transactional_email_enabled
                    and customer.email.strip().lower() == notice.recipient.strip().lower()
                    and notice.branch_id == document.branch_id)
    if notice.category in ("order", "marketing_verify", "marketing"):
        from marketplace.models import CustomerAccount, OnlineOrder
        if notice.category == "order":
            order = OnlineOrder.objects.select_related("customer").filter(pk=notice.recipient_ref).first()
            if not order:
                return False
            event = notice.source_key.rsplit(":", 1)[-1]
            return bool(
                order.customer.active and order.customer.verified_at
                and order.customer.transactional_email_enabled
                and order.email.strip().lower() == notice.recipient.strip().lower()
                and order.customer.email.strip().lower() == notice.recipient.strip().lower()
                and order.payment_status == "paid"
                and (event == "paid" or order.status == event)
            )
        customer = CustomerAccount.objects.filter(pk=notice.recipient_ref).first()
        if not customer or not customer.active or not customer.verified_at:
            return False
        if customer.email.strip().lower() != notice.recipient.strip().lower():
            return False
        if notice.category == "marketing_verify":
            import hashlib
            challenge_id = hashlib.sha256(customer.marketing_email_challenge.encode()).hexdigest()[:12]
            return bool(customer.marketing_email_challenge and not customer.marketing_email_opt_in
                        and notice.source_key.endswith(":" + challenge_id))
        return customer.marketing_email_opt_in and customer.marketing_email_verified_at is not None
    if notice.recipient_user_id is None or notice.branch_id is None:
        return False
    user = notice.recipient_user
    access = getattr(user, "access", None)
    flag = NOTICE_FLAGS.get(notice.category)
    return bool(
        flag and access and getattr(access, flag, False)
        and user.email.strip().lower() == notice.recipient.strip().lower()
        and _authorised(user, notice.branch)
    )


def process_email_outbox(limit=15):
    if not getattr(settings, "EMAIL_DELIVERY_ENABLED", False):
        return 0
    now = timezone.now()
    ids = list(
        EmailNotice.objects.filter(status__in=("queued", "failed"), attempts__lt=3)
        .filter(Q(last_attempt_at__isnull=True) | Q(last_attempt_at__lt=now - timedelta(minutes=15)))
        .order_by("created_at").values_list("pk", flat=True)[:limit]
    )
    sent = 0
    for pk in ids:
        with transaction.atomic():
            notice = EmailNotice.objects.select_for_update().select_related(
                "recipient_user", "branch"
            ).filter(pk=pk, status__in=("queued", "failed"), attempts__lt=3).first()
            if notice is None:
                continue
            if now - notice.created_at > timedelta(days=7) or not _may_deliver(notice):
                notice.status = "cancelled"
                notice.save(update_fields=["status"])
                continue
            notice.status = "sending"
            notice.attempts += 1
            notice.last_attempt_at = now
            notice.save(update_fields=["status", "attempts", "last_attempt_at"])
        try:
            email = EmailMultiAlternatives(
                notice.subject, notice.body, settings.DEFAULT_FROM_EMAIL, [notice.recipient]
            )
            paragraphs = escape(notice.body).replace("\n", "<br>")
            email.attach_alternative(
                '<div style="background:#f1f5f9;padding:28px;font-family:Arial,sans-serif">'
                '<div style="max-width:620px;margin:auto;background:white;border-top:6px solid #e9ac32;'
                'padding:30px;border-radius:8px;color:#102b46">'
                '<h1 style="font-size:23px;margin-top:0">KOFAD IMPEX ENTERPRISE</h1>'
                '<div style="font-size:14px;line-height:1.65">' + paragraphs + '</div>'
                '<hr style="border:0;border-top:1px solid #ddd">'
                '<p style="font-size:11px;color:#64798b">Confidential business report. '
                'Never send your password in a reply.</p></div></div>',
                "text/html",
            )
            email.send(fail_silently=False)
            EmailNotice.objects.filter(pk=pk, status="sending").update(
                status="sent", sent_at=timezone.now(), last_error=""
            )
            sent += 1
        except Exception as exc:
            log.exception("Email delivery failed; outbox retained for controlled retry")
            EmailNotice.objects.filter(pk=pk, status="sending").update(
                status="failed", last_error=str(exc)[:240]
            )
    return sent


def queue_customer_order_email(order, event):
    """Non-sensitive order status email to an account-associated checkout address."""
    if not getattr(settings, "EMAIL_AUTOMATIONS_ENABLED", False):
        return 0
    customer = order.customer
    if not (customer.active and customer.verified_at and customer.transactional_email_enabled):
        return 0
    if not order.email or order.email.strip().lower() != customer.email.strip().lower():
        return 0
    try:
        validate_email(order.email)
    except ValidationError:
        return 0
    label = dict(order.STATUSES).get(event, event.replace("_", " ").title())
    subject = f"KOFAD order {order.customer_reference}: {label}"
    body = (
        f"Hello {customer.full_name},\n\n"
        f"Your order {order.customer_reference} has a new update: {label}.\n"
        f"Total recorded order amount: GHS {order.total:,.2f}.\n"
        "Sign in to your KOFAD Market account to confirm current status and delivery details.\n\n"
        "This is a transaction notification, not a promotional offer.\n"
        "For safety, we never ask for your password or payment PIN by email."
    )
    _, created = EmailNotice.objects.get_or_create(
        source_key=f"market-email:{order.pk}:{event}",
        defaults={
            "recipient": order.email, "recipient_ref": str(order.pk),
            "branch": order.branch, "category": "order",
            "subject": subject[:180], "body": body,
        },
    )
    return int(created)


def queue_customer_email_verification(customer, confirmation_url):
    if not getattr(settings, "EMAIL_AUTOMATIONS_ENABLED", False):
        return False
    if not customer.email or not customer.active or not customer.verified_at:
        return False
    try:
        validate_email(customer.email)
    except ValidationError:
        return False
    bucket = timezone.now().strftime("%Y%m%d")
    fingerprint = hashlib.sha256(customer.email.strip().lower().encode()).hexdigest()[:12]
    challenge_id = hashlib.sha256(customer.marketing_email_challenge.encode()).hexdigest()[:12]
    key = f"market-verify:{customer.pk}:{bucket}:{fingerprint}:{challenge_id}"
    _, created = EmailNotice.objects.get_or_create(
        source_key=key,
        defaults={
            "recipient": customer.email, "recipient_ref": str(customer.pk),
            "category": "marketing_verify",
            "subject": "Confirm your KOFAD Market email preferences",
            "body": (
                f"Hello {customer.full_name},\n\n"
                "Someone requested promotional updates for this address.\n"
                f"To confirm this choice, use the link below within seven days:\n{confirmation_url}\n\n"
                "If you did not request promotional messages, ignore this email.\n"
                "Order notifications are separate from promotional email."
            ),
        },
    )
    return created


def run_customer_personalised_promotions(now=None):
    """No speculative discount offers. Opted-in customers get saved-item reminders."""
    if not getattr(settings, "EMAIL_AUTOMATIONS_ENABLED", False):
        return 0
    now = timezone.localtime(now or timezone.now())
    if now.weekday() != 0 or now.hour != 9:
        return 0
    from marketplace.models import CustomerAccount, WishlistItem
    created = 0
    for customer in CustomerAccount.objects.filter(
        active=True, marketing_email_opt_in=True, marketing_email_verified_at__isnull=False
    ).exclude(email="").iterator():
        if EmailNotice.objects.filter(
            category="marketing", recipient_ref=str(customer.pk),
            created_at__gte=now - timedelta(days=21),
        ).exclude(status="cancelled").exists():
            continue
        item = WishlistItem.objects.filter(
            customer=customer, listing__enabled=True, listing__product__active=True
        ).select_related("listing", "listing__product").first()
        if item is None:
            continue
        product_name = item.listing.display_name
        source_key = f"promo:wishlist:{customer.pk}:{now:%Y%m%d}"
        _, added = EmailNotice.objects.get_or_create(
            source_key=source_key,
            defaults={
                "recipient": customer.email, "recipient_ref": str(customer.pk),
                "category": "marketing",
                "subject": "An item you saved at KOFAD Market",
                "body": (
                    f"Hello {customer.full_name},\n\n"
                    f"You saved '{product_name}' at KOFAD Market. If you are still interested, "
                    "sign in to check current availability and pricing.\n"
                    "No discount or stock availability is guaranteed.\n\n"
                    "Manage or stop promotional emails from My Account > Email preferences.\n"
                    "This message is sent only to customers who explicitly confirmed their marketing email."
                ),
            },
        )
        created += int(added)
    return created


def queue_pos_transaction_email(document):
    """Email an eligible in-store transaction to its phone-verified customer account."""
    if not getattr(settings, "EMAIL_AUTOMATIONS_ENABLED", False):
        return 0
    if document.kind not in ("sale", "collection") or not document.party_id:
        return 0
    if document.party.kind != "customer":
        return 0
    try:
        phone = normalize_phone(document.party.phone)
    except ValidationError:
        return 0
    from marketplace.models import CustomerAccount
    customer = CustomerAccount.objects.filter(phone=phone, active=True).first()
    if not customer or not customer.verified_at or not customer.transactional_email_enabled:
        return 0
    try:
        validate_email(customer.email)
    except ValidationError:
        return 0
    kind_label = "purchase receipt" if document.kind == "sale" else "payment confirmation"
    body = (
        f"Hello {customer.full_name},\n\n"
        f"Your in-store {kind_label} has been recorded at {document.branch.name}.\n"
        f"Reference: {document.reference}\n"
        f"Amount: GHS {document.total:,.2f}\n"
        f"Amount recorded as paid: GHS {document.paid:,.2f}\n\n"
        "For detailed receipt records, sign in to KOFAD or contact the shop.\n"
        "This is a transactional notice. We never ask for your PIN or password by email."
    )
    _, created = EmailNotice.objects.get_or_create(
        source_key=f"pos-email:{document.pk}:{document.kind}",
        defaults={
            "recipient": customer.email,
            "recipient_ref": str(document.pk),
            "branch": document.branch,
            "category": "pos_transaction",
            "subject": f"KOFAD {kind_label} · {document.reference}",
            "body": body,
        },
    )
    return int(created)
