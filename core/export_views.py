import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from . import services as s
from .context import shell
from .exports import export
from .models import Audit, Closing, Document, Movement, Operation, Party, Product, QuarantineItem, Stock, SupplierReturn


DATASETS = {
    "customers": ("Customers", ("operate_sales", "operate_finance", "view_reports")),
    "suppliers": ("Suppliers", ("operate_inventory", "operate_finance", "view_reports")),
    "inventory": ("Inventory & stock", ("operate_inventory", "view_reports")),
    "sales": ("Sales transactions", ("operate_sales", "view_reports")),
    "purchases": ("Purchases", ("operate_inventory", "view_reports")),
    "expenses": ("Expenses", ("operate_finance", "view_reports")),
    "payments": ("Payments & collections", ("operate_finance", "view_reports")),
    "transactions": ("All transactions", ("view_reports",)),
    "movements": ("Stock movement ledger", ("operate_inventory", "view_reports")),
    "operations": ("Stock operations & transfers", ("operate_inventory", "approve_operations", "view_reports")),
    "supplier_returns": ("Supplier returns", ("operate_inventory", "operate_finance", "view_reports")),
    "quarantine": ("Damaged-stock quarantine", ("operate_inventory", "approve_operations", "view_reports")),
    "closings": ("Daily closings", ("operate_finance", "view_reports")),
    "losses": ("Inventory write-offs", ("operate_inventory", "operate_finance", "view_reports")),
    "audit": ("Audit trail", ("view_reports",)),
    "staff": ("Staff directory", ("manage_company",)),
}


def _branch(request):
    branch = shell(request)["current_branch"]
    if not branch:
        raise PermissionDenied("No active business location is assigned.")
    return branch


def _allowed(user, codes):
    return user.is_superuser or any(user.has_perm("core." + code) for code in codes)


def _dates(request):
    today = timezone.localdate()
    start = request.GET.get("start", today.replace(day=1).isoformat())
    end = request.GET.get("end", today.isoformat())
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise ValidationError("Enter a valid export date range.")
    if first > last:
        raise ValidationError("Export start date cannot be after the end date.")
    return first, last, start, end


def _rows(request, dataset, branch, first, last):
    if dataset == "customers" or dataset == "suppliers":
        kind = "customer" if dataset == "customers" else "supplier"
        rows = []
        for party in Party.objects.filter(branch=branch, kind=kind).order_by("name"):
            rows.append({
                "name": party.name,
                "phone": party.phone,
                "email": party.email,
                "address": party.address,
                "credit_limit": party.credit_limit,
                "outstanding": s.party_debt(party),
                "messages": "Allowed" if party.consent else "Not allowed",
            })
        return rows, [
            ("name", "Name"), ("phone", "Phone"), ("email", "Email"), ("address", "Address"),
            ("credit_limit", "Credit limit"), ("outstanding", "Outstanding"), ("messages", "Messaging consent"),
        ]

    if dataset == "inventory":
        balances = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
        quarantine = dict(QuarantineItem.objects.filter(branch=branch, status="held").values(
            "product_id").annotate(total=Sum("quantity")).values_list("product_id", "total"))
        rows = []
        for product in Product.objects.order_by("name"):
            quantity = balances.get(product.pk, 0)
            held = quarantine.get(product.pk, 0)
            rows.append({
                "sku": product.sku, "product": product.name, "category": product.category,
                "base_unit": product.base_unit, "pack": f"{product.pack_name} × {product.pack_size}",
                "quantity": quantity, "quarantine": held, "physical": quantity + held,
                "packs": quantity // product.pack_size, "loose": quantity % product.pack_size,
                "cost": product.cost, "value": product.cost * quantity,
                "quarantine_value": product.cost * held, "retail_unit": product.retail_unit or "",
                "retail_pack": product.retail_pack or "", "wholesale_unit": product.wholesale_unit or "",
                "wholesale_pack": product.wholesale_pack or "", "reorder": product.reorder_level,
                "status": "Active" if product.active else "Archived",
            })
        return rows, [
            ("sku", "SKU"), ("product", "Product"), ("category", "Category"), ("base_unit", "Base unit"),
            ("pack", "Pack structure"), ("quantity", "Sellable units"), ("quarantine", "Quarantined units"),
            ("physical", "Total physical units"), ("packs", "Sellable full packs"), ("loose", "Sellable loose units"),
            ("cost", "Unit cost"), ("value", "Sellable stock value"),
            ("quarantine_value", "Quarantine value"), ("retail_unit", "Retail unit"),
            ("retail_pack", "Retail pack"), ("wholesale_unit", "Wholesale unit"),
            ("wholesale_pack", "Wholesale pack"), ("reorder", "Reorder level"), ("status", "Status"),
        ]

    if dataset in {"sales", "purchases", "expenses", "payments", "transactions"}:
        period = Q(created_at__date__gte=first, created_at__date__lte=last)
        if dataset == "sales":
            scope = Q(kind__in=["sale", "return"])
        elif dataset == "purchases":
            scope = Q(kind__in=["purchase", "supplier_return"])
        elif dataset == "expenses":
            scope = Q(kind="expense") | Q(kind="reversal", original__kind="expense")
        elif dataset == "payments":
            scope = Q(kind__in=["collection", "supplier_payment"]) | Q(
                kind="reversal", original__kind__in=["collection", "supplier_payment"]
            )
        else:
            scope = Q(kind__in=list(dict(Document.KINDS)))
        docs = Document.objects.filter(
            Q(branch=branch) & period & scope
        ).select_related("party", "created_by").order_by("-created_at")
        rows = [{
            "reference": doc.reference,
            "date": timezone.localtime(doc.created_at).strftime("%Y-%m-%d %H:%M"),
            "type": doc.get_kind_display(),
            "contact": doc.party.name if doc.party else "Walk-in",
            "staff": doc.created_by.username,
            "total": doc.total,
            "paid": doc.paid,
            "balance": s.balance(doc) if doc.kind in ("sale", "purchase") else Decimal("0"),
            "note": doc.note,
        } for doc in docs]
        return rows, [
            ("reference", "Reference"), ("date", "Date"), ("type", "Type"), ("contact", "Contact"),
            ("staff", "Staff"), ("total", "Total"), ("paid", "Paid at posting"), ("balance", "Outstanding"),
            ("note", "Note"),
        ]

    if dataset == "movements":
        rows = [{
            "date": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
            "sku": row.product.sku, "product": row.product.name, "change": row.delta,
            "balance": row.balance, "reference": row.reference, "reason": row.reason, "staff": row.actor.username,
        } for row in Movement.objects.filter(
            branch=branch, created_at__date__gte=first, created_at__date__lte=last
        ).select_related("product", "actor").order_by("-created_at")]
        return rows, [
            ("date", "Date"), ("sku", "SKU"), ("product", "Product"), ("change", "Change"),
            ("balance", "Balance"), ("reference", "Source"), ("reason", "Reason"), ("staff", "Staff"),
        ]

    if dataset == "operations":
        ops = Operation.objects.filter(
            Q(branch=branch) | Q(destination=branch),
            created_at__date__gte=first, created_at__date__lte=last,
        ).select_related("product", "branch", "destination", "requested_by", "approved_by",
                         "receipt", "receipt__resolved_by", "receipt__loss_document").order_by("-created_at")
        rows = []
        for row in ops:
            receipt = getattr(row, "receipt", None)
            rows.append({
                "date": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
                "product": row.product.name,
                "type": row.get_kind_display(),
                "quantity": row.quantity,
                "route": f"{row.branch.name} → {row.destination.name}" if row.destination else row.branch.name,
                "status": row.status,
                "requested_by": row.requested_by.username,
                "approved_by": row.approved_by.username if row.approved_by else "",
                "received": receipt.quantity if receipt else "",
                "missing": receipt.missing if receipt else "",
                "resolution": receipt.resolution if receipt else "",
                "loss_document": receipt.loss_document.reference if receipt and receipt.loss_document_id else "",
                "reason": row.reason,
            })
        return rows, [
            ("date", "Date"), ("product", "Product"), ("type", "Operation"), ("quantity", "Quantity"),
            ("route", "Route"), ("status", "Status"), ("requested_by", "Requested by"),
            ("approved_by", "Approved by"), ("received", "Received"), ("missing", "Missing"),
            ("resolution", "Resolution"), ("loss_document", "Loss document"), ("reason", "Reason"),
        ]

    if dataset == "supplier_returns":
        rows = []
        for row in SupplierReturn.objects.filter(
            branch=branch, created_at__date__gte=first, created_at__date__lte=last
        ).select_related("source_line__document", "source_line__document__party", "source_line__product",
                         "requested_by", "reviewed_by", "posted").order_by("-created_at"):
            rows.append({
                "date": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
                "purchase": row.source_line.document.reference,
                "supplier": row.source_line.document.party.name if row.source_line.document.party else "",
                "product": row.source_line.product.name,
                "quantity": row.quantity,
                "amount": row.source_line.unit_price * row.quantity,
                "refund_method": row.get_refund_method_display(),
                "status": row.get_status_display(),
                "requested_by": row.requested_by.username,
                "reviewed_by": row.reviewed_by.username if row.reviewed_by else "",
                "posted": row.posted.reference if row.posted else "",
                "reason": row.reason,
            })
        return rows, [
            ("date", "Date"), ("purchase", "Original purchase"), ("supplier", "Supplier"),
            ("product", "Product"), ("quantity", "Quantity"), ("amount", "Return value"),
            ("refund_method", "Refund channel"), ("status", "Status"), ("requested_by", "Requested by"),
            ("reviewed_by", "Reviewed by"), ("posted", "Posted document"), ("reason", "Reason"),
        ]

    if dataset == "quarantine":
        rows = [{
            "date": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
            "sku": row.product.sku,
            "product": row.product.name,
            "quantity": row.quantity,
            "unit_cost": row.unit_cost,
            "value": row.value,
            "status": row.get_status_display(),
            "requested_by": row.requested_by.username,
            "reviewed_by": row.reviewed_by.username if row.reviewed_by else "",
            "resolved_by": row.resolved_by.username if row.resolved_by else "",
            "loss_document": row.loss_document.reference if row.loss_document else "",
            "reason": row.reason,
            "resolution_note": row.resolution_note,
        } for row in QuarantineItem.objects.filter(
            branch=branch, created_at__date__gte=first, created_at__date__lte=last
        ).select_related("product", "requested_by", "reviewed_by", "resolved_by", "loss_document").order_by("-created_at")]
        return rows, [
            ("date", "Date"), ("sku", "SKU"), ("product", "Product"), ("quantity", "Quantity"),
            ("unit_cost", "Unit cost"), ("value", "Value"), ("status", "Status"),
            ("requested_by", "Requested by"), ("reviewed_by", "Reviewed by"), ("resolved_by", "Resolved by"),
            ("loss_document", "Loss document"), ("reason", "Reason"), ("resolution_note", "Resolution note"),
        ]

    if dataset == "closings":
        rows = []
        for row in Closing.objects.filter(branch=branch, date__gte=first, date__lte=last).select_related(
            "submitted_by", "verified_by"
        ).order_by("-date"):
            values = {}
            for method in ("cash", "momo", "bank", "card"):
                expected = Decimal(str(row.expected.get(method, "0")))
                counted = Decimal(str(row.counted.get(method, "0")))
                values["expected_" + method] = expected
                values["counted_" + method] = counted
                values["variance_" + method] = counted - expected
            rows.append({
                "date": row.date,
                "submitted_by": row.submitted_by.username,
                "verified_by": row.verified_by.username if row.verified_by else "",
                "note": row.note,
                **values,
            })
        columns = [("date", "Date"), ("submitted_by", "Submitted by"), ("verified_by", "Verified by")]
        for method, label in (("cash", "Cash"), ("momo", "MoMo"), ("bank", "Bank"), ("card", "Card")):
            columns += [
                ("expected_" + method, f"{label} expected"),
                ("counted_" + method, f"{label} counted"),
                ("variance_" + method, f"{label} variance"),
            ]
        columns.append(("note", "Note"))
        return rows, columns

    if dataset == "losses":
        docs = Document.objects.filter(
            branch=branch, kind="inventory_writeoff",
            created_at__date__gte=first, created_at__date__lte=last,
        ).select_related("created_by").prefetch_related("lines").order_by("-created_at")
        rows = []
        for doc in docs:
            line = doc.lines.first()
            rows.append({
                "date": timezone.localtime(doc.created_at).strftime("%Y-%m-%d %H:%M"),
                "reference": doc.reference,
                "product": line.product.name if line else "",
                "quantity": line.quantity if line else "",
                "unit_cost": line.unit_cost if line else "",
                "value": doc.total,
                "staff": doc.created_by.username,
                "reason": doc.note,
            })
        return rows, [
            ("date", "Date"), ("reference", "Reference"), ("product", "Product"),
            ("quantity", "Quantity"), ("unit_cost", "Unit cost"), ("value", "Loss value"),
            ("staff", "Recorded by"), ("reason", "Reason"),
        ]

    if dataset == "audit":
        rows = [{
            "date": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M:%S"),
            "actor": row.actor.username if row.actor else "System", "event": row.action,
            "reference": row.reference, "evidence": json.dumps(row.detail, sort_keys=True, default=str),
        } for row in Audit.objects.filter(
            Q(branch=branch) | Q(branch__isnull=True),
            created_at__date__gte=first, created_at__date__lte=last,
        ).select_related("actor").order_by("-created_at")]
        return rows, [
            ("date", "Timestamp"), ("actor", "Actor"), ("event", "Event"),
            ("reference", "Reference"), ("evidence", "Evidence"),
        ]

    if dataset == "staff":
        from django.contrib.auth.models import User
        rows = []
        for user in User.objects.prefetch_related("groups").select_related("access").order_by("username"):
            role = "System administrator" if user.is_superuser else ", ".join(user.groups.values_list("name", flat=True)) or "No role"
            rows.append({
                "username": user.username,
                "name": user.get_full_name(),
                "role": role,
                "active": "Active" if user.is_active else "Disabled",
                "recovery_phone": user.access.recovery_phone,
                "last_login": timezone.localtime(user.last_login).strftime("%Y-%m-%d %H:%M") if user.last_login else "Never",
            })
        return rows, [
            ("username", "Username"), ("name", "Name"), ("role", "Role"), ("active", "Status"),
            ("recovery_phone", "Recovery phone"), ("last_login", "Last login"),
        ]

    raise ValidationError("Unknown export dataset.")


@login_required
def export_center(request):
    _branch(request)
    available = [
        {"key": key, "label": label}
        for key, (label, codes) in DATASETS.items()
        if _allowed(request.user, codes)
    ]
    return render(request, "export_center.html", {
        "title": "Downloads & exports",
        "datasets": available,
        "today": timezone.localdate().isoformat(),
        "month_start": timezone.localdate().replace(day=1).isoformat(),
    })


@login_required
def download(request, format):
    try:
        branch = _branch(request)
        dataset = request.GET.get("dataset", "")
        if dataset not in DATASETS:
            raise ValidationError("Choose a valid export.")
        label, codes = DATASETS[dataset]
        if not _allowed(request.user, codes):
            raise PermissionDenied("You do not have permission to export this information.")
        first, last, start, end = _dates(request)
        rows, columns = _rows(request, dataset, branch, first, last)
        s.audit(request.user, branch, "export.downloaded", dataset, {
            "format": format, "start": start, "end": end, "rows": len(rows),
        })
        date_suffix = "" if dataset in {"customers", "suppliers", "inventory", "staff"} else f" · {start} to {end}"
        company = shell(request)["company"]
        return export(
            rows, format, f"{label}{date_suffix}", company, columns,
            filename=f"kofad-{dataset}",
            sheet_name=label[:31],
        )
    except ValidationError as exc:
        return render(request, "error.html", {
            "title": "Check your export",
            "error": "; ".join(exc.messages),
        }, status=400)



@login_required
def statement_download(request, pk, format):
    branch = _branch(request)
    if not _allowed(request.user, ("operate_finance", "view_reports")):
        raise PermissionDenied("You do not have permission to export account statements.")
    party = get_object_or_404(Party, pk=pk, branch=branch)
    running = Decimal("0")
    rows = []
    for doc in Document.objects.filter(party=party).order_by("created_at"):
        change = doc.balance if doc.kind in ("sale", "purchase") else -sum(
            (allocation.amount for allocation in doc.allocations.all()), Decimal("0")
        )
        if doc.kind == "reversal" and doc.original_id and doc.original.kind in ("collection", "supplier_payment"):
            change = sum((allocation.amount for allocation in doc.original.allocations.all()), Decimal("0"))
        running += change
        rows.append({
            "date": timezone.localtime(doc.created_at).strftime("%Y-%m-%d %H:%M"),
            "reference": doc.reference,
            "type": doc.get_kind_display(),
            "change": change,
            "running": running,
            "note": doc.note,
        })
    s.audit(request.user, branch, "statement.exported", party.pk, {
        "format": format, "rows": len(rows), "balance": str(running),
    })
    return export(
        rows, format, f"Account statement · {party.name}", shell(request)["company"],
        [("date", "Date"), ("reference", "Reference"), ("type", "Type"),
         ("change", "Balance change"), ("running", "Running balance"), ("note", "Note")],
        filename=f"kofad-statement-{party.pk}",
        sheet_name="Statement",
    )
