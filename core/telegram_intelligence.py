"""Deterministic, source-backed, role-specific Telegram operations intelligence.

Never infer KOFAD permissions from Telegram username or a contact number.
The user's current KOFAD access and explicit subscription govern every view.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import F
from django.utils import timezone

from .models import Access, Branch
from .telegram_integration import linked_identity, queue_notice


REPORT = ("manage_company", "view_reports", "operate_finance")
INVENTORY = ("manage_company", "operate_inventory")
SALES = ("manage_company", "operate_sales")


def _role(user):
    return {
        "management": user.is_superuser or any(user.has_perm("core." + x) for x in REPORT),
        "inventory": user.is_superuser or any(user.has_perm("core." + x) for x in INVENTORY),
        "sales": user.is_superuser or any(user.has_perm("core." + x) for x in SALES),
    }


def _branches(user):
    access = Access.objects.filter(user=user).first()
    if not user.is_active or not access or access.must_change_password or access.force_password_change:
        return Branch.objects.none()
    return (Branch.objects.filter(active=True) if user.is_superuser
            else access.branches.filter(active=True))


def staff_menu(user):
    role = _role(user)
    options = ["/status — verified KOFAD staff identity",
               "/myalerts — subscribed notifications",
               "/tasks — responsibilities needing attention",
               "/branches — locations in your access scope",
               "/alerts_on — enable permitted operational alerts",
               "/alerts_off — pause optional notices"]
    if role["management"]:
        options += ["/today — verified closing intelligence for today",
                    "/week — last seven days", "/month — last 30 days"]
    if role["inventory"]:
        options.append("/inventory — stock exceptions in your assigned locations")
    if role["sales"]:
        options.append("/orders_due — active online orders in your assigned locations")
    return "KOFAD Staff Assistant — your authorized tools\n\n" + "\n".join(options)


def staff_welcome(user):
    """Current role-scoped welcome text, rechecked immediately before sending."""
    branches = _branches(user)
    role = _role(user)
    areas = []
    if role["management"]:
        areas.append("verified closing reports")
    if role["inventory"]:
        areas.append("inventory exceptions")
    if role["sales"]:
        areas.append("open sales orders")
    areas.append("your assigned work and alerts")
    return (
        "KOFAD STAFF ACCOUNT CONNECTED\n"
        "Your Telegram assistant is linked to your verified staff login.\n"
        f"Active assigned locations: {branches.count()}\n"
        "Authorized tools: " + ", ".join(areas) + ".\n"
        "Send /menu for your role-specific commands, /tasks for current "
        "priorities, or /myalerts to review notifications.\n"
        "Messages never authorize transactions or override KOFAD permissions. "
        "You can pause optional notifications with /alerts_off."
    )


def account_tasks(user):
    """Plain-language facts from KOFAD records, without leaking customer details."""
    if not user or not user.is_active:
        return ""
    branches = _branches(user)
    if not branches.exists():
        return "No active location is assigned to your KOFAD account."
    role = _role(user)
    lines = []
    if role["management"]:
        from .models import Closing
        pending = Closing.objects.filter(
            branch__in=branches, date__gte=timezone.localdate() - timedelta(days=7),
            verified_by__isnull=True,
        ).count()
        if pending:
            lines.append(f"{pending} recent closings awaiting independent verification")
    if role["inventory"]:
        from .models import Stock
        short = Stock.objects.filter(
            branch__in=branches, product__active=True,
            quantity__lte=F("product__reorder_level"),
        ).count()
        if short:
            lines.append(f"{short} recorded stock balances at/below reorder level")
    if role["sales"]:
        from marketplace.models import OnlineOrder
        outstanding = OnlineOrder.objects.filter(
            branch__in=branches,
            status__in=("paid", "confirmed", "preparing", "ready_pickup", "out_for_delivery"),
        ).count()
        if outstanding:
            lines.append(f"{outstanding} open online orders needing operational follow-through")
    if not lines:
        return "No role-scoped exception identified in your assigned locations. Open the staff portal for live tasks."
    return (
        "KOFAD OPERATIONAL PRIORITIES\n"
        + "\n".join("• " + line for line in lines)
        + "\nThese are actionable counts, not authorization to approve a payment or change stock.\n"
        "Review and act through your signed-in KOFAD staff portal."
    )


def inventory_brief(user):
    """Current low-stock lines in active assigned branches, without write access."""
    from .models import Stock

    branches = _branches(user)
    if not _role(user)["inventory"]:
        return "Your KOFAD role does not permit inventory reporting."
    if not branches.exists():
        return "No active branch is assigned to your KOFAD account."
    low = Stock.objects.filter(
        branch__in=branches, product__active=True,
        quantity__lte=F("product__reorder_level"),
    ).select_related("branch", "product").order_by(
        "branch__name", "product__name"
    )
    total = low.count()
    lines = [
        f"• {row.branch.name}: {row.product.name} — {row.quantity} recorded "
        f"(reorder level {row.product.reorder_level})"
        for row in low[:8]
    ]
    if not lines:
        return ("No low-stock records were identified in your assigned branches. "
                "Items without stock records are not included. Confirm in the inventory portal.")
    return (
        f"KOFAD LOW STOCK — {total} recorded exception(s)\n"
        + "\n".join(lines)
        + ("\nShowing the first 8 exceptions." if total > 8 else "")
        + "\nReview stock movements and restocking in your staff portal. "
        "This message cannot place an order or modify stock."
    )


def sales_work_brief(user):
    """Scoped order-work summary; no customer names, addresses or payments."""
    from django.db.models import Count
    from marketplace.models import OnlineOrder

    branches = _branches(user)
    if not _role(user)["sales"]:
        return "Your KOFAD role does not permit sales operations reporting."
    if not branches.exists():
        return "No active branch is assigned to your KOFAD account."
    statuses = ("paid", "confirmed", "preparing", "ready_pickup", "out_for_delivery")
    rows = OnlineOrder.objects.filter(
        branch__in=branches, status__in=statuses
    ).values("status").annotate(total=Count("pk")).order_by("status")
    counts = list(rows)
    if not counts:
        return ("No outstanding online orders in your assigned branches "
                "currently match the active fulfillment states.")
    return (
        "KOFAD ACTIVE SALES WORK\n"
        + "\n".join(f"• {x['status'].replace('_', ' ').title()}: {x['total']}" for x in counts)
        + "\nOpen your authorized staff order workspace for individual tasks. "
        "No customer information or payment authority is provided here."
    )


def queue_role_briefings(now=None):
    """Once-daily opted-in Telegram role brief; no message without a verified link."""
    from django.conf import settings
    if not settings.TELEGRAM_NOTIFICATIONS_ENABLED:
        return 0
    now = timezone.localtime(now or timezone.now())
    if now.hour != 8:
        return 0
    created = 0
    for user in User.objects.filter(is_active=True).iterator(chunk_size=100):
        identity = linked_identity("staff", user.pk, bot_kind="staff")
        if not identity or not identity.notifications_enabled:
            continue
        summary = account_tasks(user)
        if not summary.startswith("KOFAD OPERATIONAL PRIORITIES"):
            continue
        created += int(queue_notice(
            identity, "task", f"tg:role-brief:{now.date().isoformat()}:{user.pk}",
            summary,
        ))
    return created
