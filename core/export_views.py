import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from . import services as s
from .context import shell
from .exports import export
from .models import Audit, Document, Movement, Party, Product, Stock


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
        balances = {stock.product_id: stock for stock in Stock.objects.filter(branch=branch).select_related("product")}
        rows = []
        for product in Product.objects.order_by("name"):
            stock = balances.get(product.pk)
            quantity = stock.quantity if stock else 0
            rows.append({
                "sku": product.sku, "product": product.name, "category": product.category,
                "base_unit": product.base_unit, "pack": f"{product.pack_name} × {product.pack_size}",
                "quantity": quantity, "packs": quantity // product.pack_size,
                "loose": quantity % product.pack_size, "cost": product.cost,
                "value": product.cost * quantity, "retail_unit": product.retail_unit or "",
                "retail_pack": product.retail_pack or "", "wholesale_unit": product.wholesale_unit or "",
                "wholesale_pack": product.wholesale_pack or "", "reorder": product.reorder_level,
                "status": "Active" if product.active else "Archived",
            })
        return rows, [
            ("sku", "SKU"), ("product", "Product"), ("category", "Category"), ("base_unit", "Base unit"),
            ("pack", "Pack structure"), ("quantity", "Base units"), ("packs", "Full packs"), ("loose", "Loose units"),
            ("cost", "Unit cost"), ("value", "Stock value"), ("retail_unit", "Retail unit"),
            ("retail_pack", "Retail pack"), ("wholesale_unit", "Wholesale unit"),
            ("wholesale_pack", "Wholesale pack"), ("reorder", "Reorder level"), ("status", "Status"),
        ]

    if dataset in {"sales", "purchases", "expenses", "payments", "transactions"}:
        period = Q(created_at__date__gte=first, created_at__date__lte=last)
        if dataset == "sales":
            scope = Q(kind__in=["sale", "return"])
        elif dataset == "purchases":
            scope = Q(kind="purchase")
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
    branch = _branch(request)
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
