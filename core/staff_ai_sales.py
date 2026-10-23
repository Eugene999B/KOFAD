"""Posted sales intelligence for the KOFAD staff assistant.

Query finalized ledger documents, not closing summaries or AI-generated SQL.
Uses the same gross/returns/net definition as the staff dashboard.
"""
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from .staff_ai_tools import _window
from .telegram_intelligence import _branches, _role


def _money(value):
    return f"GHS {(value or Decimal('0.00')):,.2f}"


def posted_sales(user, question):
    """Report what the ledger actually records for authorized branches and dates.

    The figures are transaction records, not proof of physical fulfilment,
    provider settlement, verified closing, or recognized accounting profit.
    """
    if not _role(user)["management"]:
        return "Your KOFAD role does not grant company sales reporting.", "denied"
    branches = _branches(user)
    if not branches.exists():
        return "No authorized active branches are available.", "denied"

    first, last, start, end = _window(question)
    from .models import Closing, Document, Line

    docs = Document.objects.filter(
        branch__in=branches,
        created_at__gte=start, created_at__lt=end,
        finalized=True,
    )
    sales = docs.filter(kind="sale")
    returns = docs.filter(kind="return")
    gross = sales.aggregate(value=Sum("total"))["value"] or Decimal("0.00")
    refunds = returns.aggregate(value=Sum("total"))["value"] or Decimal("0.00")
    net = gross - refunds
    received_checkout = sales.aggregate(value=Sum("paid"))["value"] or Decimal("0.00")
    sale_count = sales.count()
    return_count = returns.count()

    lines = Line.objects.filter(document__in=sales)
    product_groups = (
        lines.values("product_id", "product__name")
        .annotate(qty=Sum("quantity"), amount=Sum("total"), lines=Count("pk"))
        .order_by("-amount", "product__name", "product_id")
    )
    product_count = product_groups.count()
    top = list(product_groups[:8])
    people = sales.exclude(party_id__isnull=True).values("party_id").distinct().count()
    unidentified = sales.filter(party_id__isnull=True).count()

    result = [
        f"Recorded sales · {first:%d %b %Y} to {last:%d %b %Y}",
        f"{sale_count} finalized sale(s) recorded across your authorized branches.",
        f"Gross recorded sales: {_money(gross)}.",
        f"Customer returns posted in this period: {return_count}, {_money(refunds)}.",
        f"Net recorded sales after returns: {_money(net)}.",
        f"Amount marked paid at sale checkout: {_money(received_checkout)} (not a bank settlement verification).",
    ]
    if sale_count:
        result.append(f"Products with sale lines: {product_count}; identifiable customer accounts: {people}.")
        if unidentified:
            result.append(f"{unidentified} sale(s) have no linked customer, so they cannot be counted as distinct people.")
        if top:
            result.append("Products recorded as sold (top by line value):")
            for item in top:
                result.append(
                    f"• {item['product__name'][:85]} — {item['qty']} sale-line selling unit(s), "
                    f"{_money(item['amount'])}"
                )
            if product_count > len(top):
                result.append(f"{product_count - len(top)} other product(s) omitted from this short view.")
        else:
            result.append("No itemized product lines are attached to these sales; item details cannot be verified here.")
    else:
        result.append(
            "No finalized sale documents were found for these dates in your accessible branches. "
            "This does not prove that no goods changed hands; check missing postings and branch selection."
        )

    verified = Closing.objects.filter(
        branch__in=branches, date__gte=first, date__lte=last, verified_by__isnull=False
    ).count()
    result.append(
        f"Verified daily closings in these branches and dates: {verified}. "
        + ("Reconciliation is not confirmed; compare these posted transactions with the closing records."
           if verified == 0 else
           "Closing summaries are independent of these transaction totals; reconcile any differences.")
    )
    result.append(
        "Scope: document creation timestamp in KOFAD's local timezone; returns are counted when posted. "
        "These numbers exclude unposted/off-system activity and are not proof of delivery, cash in bank, or profit."
    )
    return "\n".join(result), "sales_activity"


def posted_sales_trend(user, question):
    """Compare two equal posted-sale windows, without asserting a causal story."""
    from datetime import datetime, time, timedelta

    if not _role(user)["management"]:
        return "Your KOFAD role does not grant company sales reporting."
    branches = _branches(user)
    if not branches.exists():
        return "No authorized active branches are available."
    q = question.casefold()
    days = 30 if any(t in q for t in ("month", "monthly", "30 day")) else (
        1 if "today" in q else 7
    )
    today = timezone.localdate()
    current_start = today - timedelta(days=days - 1)
    previous_start = current_start - timedelta(days=days)
    left = timezone.make_aware(datetime.combine(previous_start, time.min))
    mid = timezone.make_aware(datetime.combine(current_start, time.min))
    right = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
    from .models import Document

    posted = Document.objects.filter(
        branch__in=branches, finalized=True,
        kind__in=("sale", "return"), created_at__gte=left,
        created_at__lt=right,
    )

    def summarize(rows):
        sales = rows.filter(kind="sale")
        returns = rows.filter(kind="return")
        gross = sales.aggregate(value=Sum("total"))["value"] or Decimal("0.00")
        refunds = returns.aggregate(value=Sum("total"))["value"] or Decimal("0.00")
        return sales.count(), gross, refunds, gross - refunds

    previous = summarize(posted.filter(created_at__lt=mid))
    current = summarize(posted.filter(created_at__gte=mid))
    delta = current[3] - previous[3]
    if previous[3] == 0:
        percent = "not calculable because the previous net amount was zero"
    else:
        percent = f"{delta / abs(previous[3]) * 100:+.1f}%"
    lines = [
        f"Recorded sales comparison — two {days}-day periods",
        f"Current ({current_start:%d %b}–{today:%d %b}): "
        f"{current[0]} sales; gross {_money(current[1])}, "
        f"returns {_money(current[2])}; net {_money(current[3])}.",
        f"Previous ({previous_start:%d %b}–"
        f"{current_start - timedelta(days=1):%d %b}): "
        f"{previous[0]} sales; gross {_money(previous[1])}, "
        f"returns {_money(previous[2])}; net {_money(previous[3])}.",
        f"Change in recorded net sales: {_money(delta)} ({percent}).",
    ]
    if current[0] == 0 and previous[0] == 0:
        lines.append("No finalized sales in either window; this cannot establish a demand trend.")
    lines.append(
        "These are finalized ledger postings, not cash or verified daily closings. "
        "The change is observed, but its cause is unverified. Check missing postings, "
        "stock-outs, returns, channel mix and seasonal effects before deciding why."
    )
    return "\n".join(lines)
