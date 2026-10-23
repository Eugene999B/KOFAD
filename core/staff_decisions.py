"""Decision-grade, permission-scoped KOFAD operations intelligence.

This engine produces grounded *read-only* recommendations for staff web and
opted-in Telegram reports. Never sends messages, executes orders, generates
SQL from prompts, or sends private business records to an external provider.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import F
from django.utils import timezone

from .telegram_intelligence import _branches, _role


def _money(value):
    try:
        item = Decimal(str(value if value is not None else 0))
        return item if item.is_finite() else Decimal("0")
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _verified_sales_signal(branches, today):
    """Compare per-verified-closing amounts without mistaking missing days for a slump."""
    from .models import Closing

    start = today - timedelta(days=13)
    recent_start = today - timedelta(days=6)
    rows = Closing.objects.filter(
        branch__in=branches,
        verified_by__isnull=False,
        date__range=(start, today),
    ).values_list("date", "summary").order_by("date")[:4001]
    totals = [Decimal("0"), Decimal("0")]
    counts = [0, 0]
    capped = False
    for i, (day, summary) in enumerate(rows):
        if i >= 4000:
            capped = True
            break
        idx = 1 if day >= recent_start else 0
        totals[idx] += _money((summary or {}).get("sales_total"))
        counts[idx] += 1

    if capped:
        return "Sales data review: the 4,000-closing safety cap was reached. Verify the full report before drawing conclusions."
    if not counts[0] or not counts[1]:
        return "Sales trend: not enough verified closings in both seven-day windows for a reliable comparison."
    expected = 7 * branches.count()
    if counts[0] < expected or counts[1] < expected:
        completeness = (
            f"Verified closing coverage: {counts[1]}/{expected} recent and "
            f"{counts[0]}/{expected} earlier. Missing closings may distort totals."
        )
    else:
        completeness = "All expected daily closings are verified for both windows."
    old_avg = totals[0] / counts[0]
    new_avg = totals[1] / counts[1]
    if old_avg <= 0:
        return completeness + " Prior average is zero; a percentage trend would be misleading."
    movement = ((new_avg - old_avg) / old_avg * Decimal("100"))
    if abs(movement) < Decimal("10"):
        direction = "broadly similar"
    elif movement < 0:
        direction = "lower"
    else:
        direction = "higher"
    return (
        f"Sales per verified closing: GHS {new_avg:,.2f} recently versus "
        f"GHS {old_avg:,.2f} before ({movement:+.1f}%, {direction}). "
        + completeness
        + " This is not profit or a sales forecast. Check seasonality, stock gaps "
          "and unverified closings before deciding what caused a change."
    )


def decision_brief(user, *, now=None):
    """Return actionable, ranked evidence from only current authorized branches."""
    from .models import Closing, Stock
    from marketplace.models import OnlineOrder

    if not user or not user.is_active:
        return "Verified staff sign-in is required before showing business intelligence."
    branches = _branches(user)
    if not branches.exists():
        return "No active authorized KOFAD branch is assigned to this account."
    roles = _role(user)
    if not any(roles.values()):
        return "Your KOFAD role does not permit operational intelligence."

    today = timezone.localdate(now) if now else timezone.localdate()
    alerts = []
    notes = []
    if roles["management"]:
        pending = Closing.objects.filter(
            branch__in=branches,
            date__gte=today - timedelta(days=7),
            verified_by__isnull=True,
        ).count()
        if pending:
            alerts.append((1, f"{pending} recent closings still need independent verification",
                           "Have a reviewer check source receipts, counted cash and any variances."))
        notes.append(_verified_sales_signal(branches, today))
    if roles["inventory"]:
        low = Stock.objects.filter(
            branch__in=branches, product__active=True,
            quantity__lte=F("product__reorder_level"),
        )
        empty = low.filter(quantity=0).count()
        remaining = low.count() - empty
        if empty:
            alerts.append((1, f"{empty} recorded stock balances are at zero",
                           "Confirm the physical count and pending deliveries before promising stock."))
        if remaining:
            alerts.append((2, f"{remaining} recorded stock balances are at or below reorder level",
                           "Check demand, pack sizes, current deliveries and supplier lead time before restocking."))
        notes.append("Stock checks cover recorded balances only. Missing stock rows and incoming unposted stock are not counted.")
    if roles["sales"]:
        active = OnlineOrder.objects.filter(
            branch__in=branches,
            status__in=("paid", "confirmed", "preparing", "ready_pickup", "out_for_delivery"),
        )
        aged = active.filter(created_at__lt=timezone.now() - timedelta(hours=48)).count()
        if aged:
            alerts.append((1, f"{aged} active online orders were created over 48 hours ago",
                           "Check status and delivery evidence; age alone does not prove an order is late."))
        remaining = active.count() - aged
        if remaining:
            alerts.append((2, f"{remaining} more online orders are in active fulfilment",
                           "Check promised pickup or delivery terms and assign the next staff action."))
    alerts.sort(key=lambda x: (x[0], x[1]))
    lines = [
        "KOFAD VERIFIED OPERATIONS BRIEF",
        f"Scope: {branches.count()} authorized active location(s); no customer contacts exposed.",
    ]
    if not alerts:
        lines.append("No exception was found in the checked categories; this is not a guarantee that all operations are error-free.")
    else:
        lines.append("What to review first:")
        for idx, (_rank, evidence, action) in enumerate(alerts[:8], 1):
            lines.append(f"{idx}. {evidence}. Next: {action}")
    if notes:
        lines.append("\nEvidence and limitations:")
        lines.extend("• " + item for item in notes)
    lines.append(
        "\nThese are review recommendations, not automated decisions to transfer "
        "money, order stock or message a customer. Use /workspace/ and the "
        "approved communications tools for authorized follow-through."
    )
    return "\n".join(lines)


def suggested_communication(user):
    """Grounded communication advice; never silently choose recipients or send."""
    summary = decision_brief(user)
    if summary.startswith("No active") or "required" in summary or "does not permit" in summary:
        return summary
    return (
        "KOFAD COMMUNICATION PLAN — REVIEW BEFORE SENDING\n"
        + summary[:1150]
        + "\n\nIf a verified customer is affected, first check the actual order "
          "or account and the allowed contact channel. Suggested wording:\n"
          ""Hello, this is KOFAD. We're checking an update about your order. "
          "Please open your account for the latest status, or contact our team "
          "if you need a hand."\n"
          "Do not claim payment received, a new delivery date, a discount or "
          "stock availability without verified evidence. Personalise only "
          "within the authorized customer record. Routine transactional notices "
          "follow configured events; promotional messages require separate "
          "opt-in. The assistant does not send this draft."
    )
