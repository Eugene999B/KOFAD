from collections import Counter, defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from . import counts
from . import inventory_exceptions as inventory_controls
from . import payroll_engine
from . import returns as return_controls
from . import services as s
from .accounting_engine import review_manual_journal
from .models import (
    Closing, Correction, CustomerReturnRequest, ManualJournal, PayrollPeriod,
    QuarantineItem, ReturnPrivilege, StockCount, SupplierReturn,
)
from .views import branch_for, problem


def _owner(user):
    return bool(user.is_superuser or user.has_perm("core.manage_company"))


def _approver(user):
    return bool(_owner(user) or user.has_perm("core.approve_operations"))


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _approval_items(user, branch, include_history=False):
    can_review = _approver(user)
    own = {} if can_review else {"requested_by": user}
    items = []

    customer_qs = CustomerReturnRequest.objects.filter(branch=branch).select_related(
        "sale", "sale__party", "requested_by", "reviewed_by", "posted"
    ).prefetch_related("lines", "lines__source_line")
    supplier_qs = SupplierReturn.objects.filter(branch=branch).select_related(
        "source_line__document", "source_line__product", "requested_by", "reviewed_by", "posted"
    )
    correction_qs = Correction.objects.filter(original__branch=branch).select_related(
        "original", "requested_by", "reviewed_by", "posted"
    )
    count_qs = StockCount.objects.filter(branch=branch).select_related("created_by", "reviewed_by").prefetch_related("lines", "lines__product")
    quarantine_qs = QuarantineItem.objects.filter(branch=branch).select_related("product", "requested_by", "reviewed_by")
    payroll_qs = PayrollPeriod.objects.filter(branch=branch).select_related("prepared_by", "approved_by")
    journal_qs = ManualJournal.objects.filter(branch=branch).select_related("requested_by", "reviewed_by").prefetch_related("lines")
    closing_qs = Closing.objects.filter(branch=branch).select_related("submitted_by", "verified_by")

    if include_history:
        customer_qs = customer_qs[:80]
        supplier_qs = supplier_qs[:80]
        correction_qs = correction_qs[:80]
        count_qs = count_qs[:80]
        quarantine_qs = quarantine_qs[:80]
        payroll_qs = payroll_qs[:80]
        journal_qs = journal_qs[:80]
        closing_qs = closing_qs[:80]
    else:
        customer_qs = customer_qs.filter(status="requested", **own)
        supplier_qs = supplier_qs.filter(status="requested", **own)
        correction_qs = correction_qs.filter(status="requested", **own)
        count_qs = count_qs.filter(status="submitted", **({"created_by": user} if not can_review else {}))
        quarantine_qs = quarantine_qs.filter(status="requested", **own)
        payroll_qs = payroll_qs.filter(status="prepared", **({"prepared_by": user} if not can_review else {}))
        journal_qs = journal_qs.filter(status="requested", **own)
        closing_qs = closing_qs.filter(verified_by__isnull=True, **({"submitted_by": user} if not can_review else {}))

    for row in customer_qs:
        amount = sum((_money(line.source_line.unit_price) * line.quantity for line in row.lines.all()), Decimal("0"))
        items.append({
            "type": "customer_return", "category": "Returns", "id": str(row.pk),
            "title": f"Customer return · {row.sale.reference}",
            "detail": f"{row.sale.party.name if row.sale.party else 'Walk-in customer'} · {row.lines.count()} item line(s)",
            "amount": amount, "status": row.status, "requested_by": row.requested_by,
            "reviewed_by": row.reviewed_by, "created_at": row.created_at,
            "href": f"/returns/?sale={row.sale_id}", "severity": "high" if amount >= 1000 else "medium",
            "can_reject": row.status == "requested",
        })

    for row in supplier_qs:
        amount = _money(row.source_line.unit_price) * row.quantity
        items.append({
            "type": "supplier_return", "category": "Returns", "id": str(row.pk),
            "title": f"Supplier return · {row.source_line.document.reference}",
            "detail": f"{row.source_line.product.name} · {row.quantity} selling unit(s)",
            "amount": amount, "status": row.status, "requested_by": row.requested_by,
            "reviewed_by": row.reviewed_by, "created_at": row.created_at,
            "href": "/supplier-returns/", "severity": "high" if amount >= 1000 else "medium",
            "can_reject": row.status == "requested",
        })

    for row in correction_qs:
        items.append({
            "type": "correction", "category": "Corrections", "id": str(row.pk),
            "title": f"Correction · {row.original.reference}",
            "detail": row.reason[:160], "amount": row.original.total, "status": row.status,
            "requested_by": row.requested_by, "reviewed_by": row.reviewed_by,
            "created_at": row.created_at, "href": "/corrections/", "severity": "high",
            "can_reject": row.status == "requested",
        })

    for row in count_qs:
        value = sum((abs((line.counted or 0) - line.expected) * line.product.cost for line in row.lines.all()), Decimal("0"))
        exceptions = sum(1 for line in row.lines.all() if line.counted is not None and line.counted != line.expected)
        items.append({
            "type": "stock_count", "category": "Inventory verification", "id": str(row.pk),
            "title": f"Inventory verification · {row.scope or 'All active products'}",
            "detail": f"{exceptions} variance line(s) · variance exposure {value:.2f}",
            "amount": value, "status": row.status, "requested_by": row.created_by,
            "reviewed_by": row.reviewed_by, "created_at": row.created_at,
            "href": f"/stock-counts/{row.pk}/", "severity": "high" if value >= 1000 else ("medium" if value else "low"),
            "can_reject": row.status == "submitted",
        })

    for row in quarantine_qs:
        items.append({
            "type": "quarantine", "category": "Inventory control", "id": str(row.pk),
            "title": f"Damaged stock hold · {row.product.name}", "detail": row.reason[:160],
            "amount": row.quantity * row.unit_cost, "status": row.status,
            "requested_by": row.requested_by, "reviewed_by": row.reviewed_by,
            "created_at": row.created_at, "href": "/quarantine/", "severity": "high",
            "can_reject": row.status == "requested",
        })

    for row in payroll_qs:
        total = sum((entry.net_pay for entry in row.entries.all()), Decimal("0"))
        items.append({
            "type": "payroll", "category": "Payroll", "id": str(row.pk),
            "title": f"Payroll · {row.year}-{row.month:02d}", "detail": f"{row.entries.count()} worker(s) prepared",
            "amount": total, "status": row.status, "requested_by": row.prepared_by,
            "reviewed_by": row.approved_by, "created_at": row.prepared_at or row.created_at,
            "href": f"/payroll/{row.pk}/", "severity": "high", "can_reject": row.status == "prepared",
        })

    for row in journal_qs:
        total = sum((line.debit for line in row.lines.all()), Decimal("0"))
        items.append({
            "type": "manual_journal", "category": "Accounting", "id": str(row.pk),
            "title": f"Manual journal · {row.reference}", "detail": row.memo[:160],
            "amount": total, "status": row.status, "requested_by": row.requested_by,
            "reviewed_by": row.reviewed_by, "created_at": row.created_at,
            "href": "/accounting/?view=journals", "severity": "high", "can_reject": row.status == "requested",
        })

    for row in closing_qs:
        items.append({
            "type": "closing", "category": "Daily control", "id": str(row.pk),
            "title": f"Daily closing · {row.date}", "detail": row.note[:160] or "Daily cash/channel reconciliation",
            "amount": None, "status": "verified" if row.verified_by_id else "pending",
            "requested_by": row.submitted_by, "reviewed_by": row.verified_by,
            "created_at": row.created_at, "href": f"/closings/?date={row.date}",
            "severity": "medium", "can_reject": False,
        })

    items.sort(key=lambda item: (item["status"] not in {"requested", "submitted", "prepared", "pending"}, -item["created_at"].timestamp()))
    return items


@login_required
def approval_summary(request):
    branch = branch_for(request)
    items = _approval_items(request.user, branch)
    counts = Counter(item["category"] for item in items)
    actionable = len(items) if _approver(request.user) else 0
    return JsonResponse({
        "pending": len(items),
        "actionable": actionable,
        "categories": dict(counts),
    })


@login_required
def approval_center(request):
    branch = branch_for(request)
    category = request.GET.get("category", "").strip()[:40]
    history = request.GET.get("history") == "1"
    items = _approval_items(request.user, branch, include_history=history)
    if category:
        items = [item for item in items if item["category"] == category]
    grouped = defaultdict(list)
    for item in items:
        grouped[item["category"]].append(item)
    return render(request, "approvals.html", {
        "title": "Approval Center",
        "items": items,
        "groups": dict(grouped),
        "categories": sorted({item["category"] for item in _approval_items(request.user, branch, include_history=history)}),
        "selected_category": category,
        "history": history,
        "can_approve": _approver(request.user),
        "pending_count": len(_approval_items(request.user, branch)),
    })


@login_required
def approval_action(request):
    if request.method != "POST":
        return redirect("approval_center")
    branch = branch_for(request)
    if not _approver(request.user):
        raise PermissionDenied("Approval authority is required.")
    kind = request.POST.get("type", "")
    item_id = request.POST.get("id", "")
    action = request.POST.get("action", "")
    note = request.POST.get("note", "").strip() or "Reviewed from KOFAD Approval Center."
    if action not in {"approve", "reject"}:
        messages.error(request, "Choose approve or reject.")
        return redirect("approval_center")
    approve = action == "approve"
    owner_direct = _owner(request.user)
    try:
        if kind == "customer_return":
            if approve:
                return_controls.execute_customer_return(request.user, branch, item_id)
            else:
                return_controls.reject_customer_return(request.user, branch, item_id, note)
        elif kind == "supplier_return":
            inventory_controls.review_supplier_return(request.user, branch, item_id, approve, direct=owner_direct)
        elif kind == "correction":
            s.review_correction(request.user, branch, item_id, approve, owner_direct=owner_direct)
        elif kind == "stock_count":
            counts.review_count(request.user, branch, item_id, "approve" if approve else "reject", note, owner_direct=owner_direct)
        elif kind == "quarantine":
            inventory_controls.review_quarantine(request.user, branch, item_id, approve, direct=owner_direct)
        elif kind == "payroll":
            period = PayrollPeriod.objects.get(pk=item_id, branch=branch)
            if approve:
                payroll_engine.approve_period(request.user, period, owner_direct=owner_direct)
            else:
                payroll_engine.return_to_draft(period)
        elif kind == "manual_journal":
            review_manual_journal(request.user, branch, item_id, approve)
        elif kind == "closing":
            if not approve:
                raise ValidationError("Daily closings are verified, not rejected. Correct the underlying records before verification.")
            closing = Closing.objects.get(pk=item_id, branch=branch)
            s.verify_closing(request.user, closing, owner_direct=owner_direct)
        else:
            raise ValidationError("Unknown approval request type.")
        messages.success(request, "Approval decision recorded.")
    except (ValidationError, PermissionDenied, ValueError) as exc:
        messages.error(request, problem(exc))
    return redirect("approval_center")


@login_required
def return_privileges(request):
    branch = branch_for(request)
    if not _owner(request.user):
        raise PermissionDenied("Only the owner / company administrator can manage direct-return privileges.")
    users = list(User.objects.filter(is_active=True).distinct().order_by("username"))
    if request.method == "POST":
        for user in users:
            if _owner(user):
                continue
            customer = request.POST.get(f"customer_{user.pk}") == "on"
            supplier = request.POST.get(f"supplier_{user.pk}") == "on"
            privilege, _ = ReturnPrivilege.objects.get_or_create(branch=branch, user=user)
            changed = privilege.customer_returns != customer or privilege.supplier_returns != supplier
            privilege.customer_returns = customer
            privilege.supplier_returns = supplier
            privilege.granted_by = request.user
            privilege.save()
            if changed:
                s.audit(request.user, branch, "return_privilege.updated", user.pk, {
                    "username": user.username, "customer_returns": customer, "supplier_returns": supplier,
                }, category="administration", severity="high", entity_type="user", entity_id=str(user.pk))
        messages.success(request, "Direct-return privileges updated.")
        return redirect("return_privileges")

    privileges = {p.user_id: p for p in ReturnPrivilege.objects.filter(branch=branch, user__in=users)}
    rows = []
    for user in users:
        privilege = privileges.get(user.pk)
        rows.append({
            "user": user,
            "owner": _owner(user),
            "customer": True if _owner(user) else bool(privilege and privilege.customer_returns),
            "supplier": True if _owner(user) else bool(privilege and privilege.supplier_returns),
        })
    return render(request, "return_privileges.html", {
        "title": "Return privileges", "rows": rows,
    })
