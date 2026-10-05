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
from .models import Audit, Closing, CustomerReturnRequest, Document, Movement, Party, Product, QuarantineItem, Stock, SupplierReturn


DATASETS = {
    "customers": ("Customers", ("operate_sales", "operate_finance", "view_reports")),
    "debts": ("Customer debt summary", ("operate_sales", "operate_finance", "view_reports")),
    "creditors": ("Creditors & accounts payable", ("operate_finance", "view_reports")),
    "suppliers": ("Suppliers", ("operate_inventory", "operate_finance", "view_reports")),
    "inventory": ("Inventory & stock", ("operate_inventory", "view_reports")),
    "sales": ("Sales transactions", ("operate_sales", "view_reports")),
    "purchases": ("Purchases", ("operate_inventory", "view_reports")),
    "expenses": ("Expenses", ("operate_finance", "view_reports")),
    "payments": ("Payments & collections", ("operate_finance", "view_reports")),
    "transactions": ("All transactions", ("view_reports",)),
    "movements": ("Stock movement ledger", ("operate_inventory", "view_reports")),
    "customer_returns": ("Customer returns", ("operate_sales", "operate_finance", "view_reports")),
    "supplier_returns": ("Supplier returns", ("operate_inventory", "operate_finance", "view_reports")),
    "quarantine": ("Damaged-stock quarantine", ("operate_inventory", "approve_operations", "view_reports")),
    "closings": ("Daily closings", ("operate_finance", "view_reports")),
    "losses": ("Inventory write-offs", ("operate_inventory", "operate_finance", "view_reports")),
    "audit": ("Audit trail", ("view_reports",)),
    "staff": ("System user accounts", ("manage_company",)),
    "workers": ("Workforce register", ("manage_company",)),
    "payroll": ("Payroll register", ("operate_finance", "view_reports")),
    "online_orders": ("Online orders & fulfilment", ("operate_sales", "manage_company", "view_reports")),
    "market_customers": ("Market customer accounts", ("operate_sales", "manage_company", "view_reports")),
    "market_catalog": ("Published Market catalog", ("operate_sales", "operate_inventory", "manage_company", "view_reports")),
    "customer_support": ("Customer support conversations", ("operate_sales", "manage_company", "view_reports")),
    "online_returns": ("Online return requests", ("operate_sales", "operate_finance", "manage_company", "view_reports")),
    "delivery_tracking": ("Online delivery tracking", ("operate_sales", "manage_company", "view_reports")),
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

    if dataset == "debts":
        from . import debts as debt_service
        overview = debt_service.debt_overview(branch)
        rows = []
        for row in overview["rows"]:
            first_invoice = row["invoices"][0] if row["invoices"] else None
            rows.append({
                "customer": row["party"].name,
                "phone": row["party"].phone,
                "outstanding": row["outstanding"],
                "overdue": row["overdue"],
                "due_today": row["due_today"],
                "open_receipts": row["invoice_count"],
                "next_due": first_invoice[0].due_date if first_invoice else "",
                "next_reference": first_invoice[0].reference if first_invoice else "",
                "next_balance": first_invoice[1] if first_invoice else "",
                "lifetime_sales": row["total_sales"],
                "debt_payments": row["total_collections"],
            })
        return rows, [
            ("customer", "Customer"), ("phone", "Phone"), ("outstanding", "Outstanding"),
            ("overdue", "Overdue"), ("due_today", "Due today"), ("open_receipts", "Open receipts"),
            ("next_due", "Next / oldest due"), ("next_reference", "Receipt"),
            ("next_balance", "Receipt balance"), ("lifetime_sales", "Lifetime sales"),
            ("debt_payments", "Debt payments received"),
        ]

    if dataset == "creditors":
        from . import creditors as creditor_service
        overview = creditor_service.creditors_overview(branch, include_settled=True)
        rows = [{
            "creditor": row["party"].name,
            "phone": row["party"].phone,
            "email": row["party"].email,
            "outstanding": row["outstanding"],
            "overdue": row["overdue"],
            "due_7": row["due_7_days"],
            "open_bills": row["bill_count"],
            "next_due": row["next_due"] or "",
            "max_days_overdue": row["maximum_days_overdue"],
            "purchases": row["total_purchases"],
            "direct_bills": row["total_direct"],
            "payments": row["total_paid"],
        } for row in overview["rows"]]
        return rows, [
            ("creditor", "Creditor / supplier"), ("phone", "Phone"), ("email", "Email"),
            ("outstanding", "Outstanding"), ("overdue", "Overdue"),
            ("due_7", "Due next 7 days"), ("open_bills", "Open bills"),
            ("next_due", "Next due"), ("max_days_overdue", "Max days overdue"),
            ("purchases", "Purchase value"), ("direct_bills", "Direct bills"),
            ("payments", "Supplier payments"),
        ]

    if dataset == "market_customers":
        from marketplace.models import CustomerAccount
        rows = []
        for customer in CustomerAccount.objects.order_by("-created_at"):
            paid_spend = customer.orders.filter(
                payment_status="paid"
            ).aggregate(total=Sum("total"))["total"] or Decimal("0")
            rows.append({
                "name": customer.full_name,
                "phone": customer.phone,
                "email": customer.email,
                "verified": customer.verified_at,
                "created": customer.created_at,
                "last_login": customer.last_login_at,
                "orders": customer.orders.count(),
                "paid_spend": paid_spend,
                "support_threads": customer.conversations.count(),
                "addresses": customer.addresses.count(),
                "status": "Active" if customer.active else "Disabled",
            })
        return rows, [
            ("name", "Customer"), ("phone", "Phone"), ("email", "Email"),
            ("verified", "Verified at"), ("created", "Account created"), ("last_login", "Last login"),
            ("orders", "Orders"), ("paid_spend", "Paid online spend"),
            ("support_threads", "Support threads"), ("addresses", "Saved addresses"), ("status", "Status"),
        ]

    if dataset == "market_catalog":
        from marketplace.models import MarketListing
        rows = []
        for listing in MarketListing.objects.select_related("product").order_by(
            "-enabled", "-featured", "sort_order", "product__name"
        ):
            product = listing.product
            rows.append({
                "sku": product.sku,
                "product": product.name,
                "market_title": listing.display_name,
                "category": product.category,
                "published": "Published" if listing.enabled else "Hidden",
                "featured": "Yes" if listing.featured else "No",
                "price_source": listing.get_price_source_display(),
                "market_price": listing.market_price,
                "selling_unit": listing.selling_label,
                "sort_order": listing.sort_order,
                "photo": "Uploaded" if listing.image_data else ("Curated" if listing.image_url else "Missing"),
                "description": listing.description,
                "tags": listing.tags,
            })
        return rows, [
            ("sku", "SKU"), ("product", "KOFAD product"), ("market_title", "Market title"),
            ("category", "Category"), ("published", "Publication"), ("featured", "Featured"),
            ("price_source", "Price source"), ("market_price", "Market price"),
            ("selling_unit", "Selling unit"), ("sort_order", "Display order"),
            ("photo", "Photo"), ("description", "Customer description"), ("tags", "Search tags"),
        ]

    if dataset == "online_orders":
        from marketplace.models import OnlineOrder
        rows = []
        orders = OnlineOrder.objects.filter(
            branch=branch, created_at__date__gte=first, created_at__date__lte=last
        ).select_related("customer", "delivery_zone", "sale_document").prefetch_related("lines").order_by("-created_at")
        for order in orders:
            rows.append({
                "reference": order.public_reference,
                "created": order.created_at,
                "customer": order.customer.full_name,
                "phone": order.phone,
                "email": order.email,
                "items": order.lines.count(),
                "fulfilment": order.get_fulfilment_display(),
                "delivery_area": order.delivery_zone.name if order.delivery_zone else "",
                "status": order.get_status_display(),
                "payment_status": order.get_payment_status_display(),
                "payment_channel": order.payment_channel,
                "payment_reference": order.payment_reference,
                "subtotal": order.subtotal,
                "delivery_fee": order.delivery_fee,
                "total": order.total,
                "ledger": order.get_ledger_status_display(),
                "sale_document": order.sale_document.reference if order.sale_document else "",
                "completed": order.completed_at,
            })
        return rows, [
            ("reference", "Online order"), ("created", "Created"), ("customer", "Customer"),
            ("phone", "Phone"), ("email", "Email"), ("items", "Item lines"),
            ("fulfilment", "Fulfilment"), ("delivery_area", "Delivery area"), ("status", "Order status"),
            ("payment_status", "Payment"), ("payment_channel", "Payment channel"),
            ("payment_reference", "Provider reference"), ("subtotal", "Products"),
            ("delivery_fee", "Delivery"), ("total", "Total"), ("ledger", "Ledger status"),
            ("sale_document", "KOFAD sale"), ("completed", "Completed at"),
        ]

    if dataset == "online_returns":
        from marketplace.models import MarketReturnRequest
        rows = []
        items = MarketReturnRequest.objects.filter(
            order__branch=branch,
            created_at__date__gte=first,
            created_at__date__lte=last,
        ).select_related(
            "order", "customer", "reviewed_by", "core_return_request"
        ).prefetch_related("lines__order_line", "attachments").order_by("-created_at")
        for item in items:
            value = sum(
                (row.order_line.unit_price * row.quantity for row in item.lines.all()),
                Decimal("0"),
            )
            rows.append({
                "created": item.created_at,
                "order": item.order.public_reference,
                "customer": item.customer.full_name,
                "phone": item.customer.phone,
                "resolution": item.get_resolution_display(),
                "status": item.get_status_display(),
                "lines": item.lines.count(),
                "value": value,
                "attachments": item.attachments.count(),
                "reviewed_by": item.reviewed_by.username if item.reviewed_by else "",
                "reviewed_at": item.reviewed_at,
                "core_return": str(item.core_return_request_id or ""),
                "refund_amount": item.refund_amount,
                "paystack_refund_id": item.provider_refund_id,
                "paystack_refund_status": item.provider_refund_status,
                "refund_initiated": item.refund_initiated_at,
                "refund_processed": item.refund_processed_at,
                "reason": item.reason,
                "staff_note": item.staff_note,
            })
        return rows, [
            ("created", "Requested at"), ("order", "Online order"), ("customer", "Customer"),
            ("phone", "Phone"), ("resolution", "Requested resolution"), ("status", "Status"),
            ("lines", "Item lines"), ("value", "Requested value"), ("attachments", "Evidence files"),
            ("reviewed_by", "Reviewed by"), ("reviewed_at", "Reviewed at"),
            ("core_return", "KOFAD return request"), ("refund_amount", "Refund amount"),
            ("paystack_refund_id", "Paystack refund ID"), ("paystack_refund_status", "Paystack refund status"),
            ("refund_initiated", "Refund initiated"), ("refund_processed", "Refund processed"),
            ("reason", "Customer reason"), ("staff_note", "Staff note"),
        ]

    if dataset == "delivery_tracking":
        from marketplace.models import DeliveryTrackingUpdate
        rows = []
        updates = DeliveryTrackingUpdate.objects.filter(
            order__branch=branch,
            created_at__date__gte=first,
            created_at__date__lte=last,
        ).select_related("order", "order__customer", "actor").order_by("-created_at")
        for item in updates:
            rows.append({
                "created": item.created_at,
                "order": item.order.public_reference,
                "customer": item.order.customer.full_name,
                "driver": item.order.delivery_agent_name,
                "driver_phone": item.order.delivery_agent_phone,
                "eta": item.order.estimated_delivery_at,
                "status": item.status,
                "note": item.note,
                "latitude": item.latitude or "",
                "longitude": item.longitude or "",
                "customer_visible": "Yes" if item.customer_visible else "No",
                "staff": item.actor.username if item.actor else "",
            })
        return rows, [
            ("created", "Update time"), ("order", "Online order"), ("customer", "Customer"),
            ("driver", "Driver"), ("driver_phone", "Driver phone"), ("eta", "ETA"),
            ("status", "Tracking status"), ("note", "Tracking note"),
            ("latitude", "Latitude"), ("longitude", "Longitude"),
            ("customer_visible", "Customer visible"), ("staff", "Staff"),
        ]

    if dataset == "customer_support":
        from marketplace.models import Conversation
        rows = []
        threads = Conversation.objects.filter(
            updated_at__date__gte=first, updated_at__date__lte=last
        ).select_related("customer", "order", "assigned_to").prefetch_related(
            "messages", "messages__attachments"
        ).order_by("-updated_at")
        for thread in threads:
            messages = list(thread.messages.all())
            rows.append({
                "opened": thread.created_at,
                "updated": thread.updated_at,
                "customer": thread.customer.full_name if thread.customer else thread.public_name,
                "phone": thread.customer.phone if thread.customer else thread.public_phone,
                "subject": thread.subject,
                "order": thread.order.public_reference if thread.order else "",
                "status": thread.get_status_display(),
                "assigned_to": thread.assigned_to.username if thread.assigned_to else "",
                "messages": len(messages),
                "attachments": sum(message.attachments.count() for message in messages),
                "last_message": messages[-1].body if messages else "",
            })
        return rows, [
            ("opened", "Opened"), ("updated", "Last activity"), ("customer", "Customer"),
            ("phone", "Phone"), ("subject", "Subject"), ("order", "Online order"),
            ("status", "Status"), ("assigned_to", "Assigned staff"),
            ("messages", "Messages"), ("attachments", "Attachments"), ("last_message", "Last message"),
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
            "business_date": doc.document_date or doc.created_at.date(),
            "supplier_reference": doc.external_reference,
            "balance": s.balance(doc) if doc.kind in ("sale", "purchase", "creditor_charge") else Decimal("0"),
            "funding_source": doc.expense_funding_label if doc.kind == "expense" else "",
            "daily_closing": (
                "Deducts from Daily Closing" if doc.kind == "expense" and doc.expense_affects_daily_closing
                else "Accounting only" if doc.kind == "expense"
                else ""
            ),
            "funding_note": doc.expense_funding_note if doc.kind == "expense" else "",
            "note": doc.note,
        } for doc in docs]
        return rows, [
            ("reference", "Reference"), ("date", "Entered at"), ("business_date", "Business date"),
            ("type", "Type"), ("contact", "Contact"), ("supplier_reference", "Supplier reference"),
            ("staff", "Staff"), ("total", "Total"), ("paid", "Paid at posting"), ("balance", "Outstanding"),
            ("funding_source", "Expense funding source"), ("daily_closing", "Daily Closing treatment"),
            ("funding_note", "Funding note"), ("note", "Note"),
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

    if dataset == "customer_returns":
        rows = []
        for row in CustomerReturnRequest.objects.filter(
            branch=branch, created_at__date__gte=first, created_at__date__lte=last
        ).select_related("sale", "sale__party", "requested_by", "reviewed_by", "posted").prefetch_related(
            "lines", "lines__source_line", "lines__source_line__product"
        ).order_by("-created_at"):
            value = sum((line.source_line.unit_price * line.quantity for line in row.lines.all()), Decimal("0"))
            rows.append({
                "date": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
                "sale": row.sale.reference,
                "customer": row.sale.party.name if row.sale.party else "Walk-in customer",
                "items": row.lines.count(),
                "value": value,
                "refund_method": row.get_refund_method_display(),
                "status": row.get_status_display(),
                "authority": "Direct" if row.direct else "Approval controlled",
                "requested_by": row.requested_by.username,
                "reviewed_by": row.reviewed_by.username if row.reviewed_by else "",
                "posted": row.posted.reference if row.posted else "",
                "reason": row.reason,
            })
        return rows, [
            ("date", "Date"), ("sale", "Original sale"), ("customer", "Customer"),
            ("items", "Item lines"), ("value", "Return value"), ("refund_method", "Refund channel"),
            ("status", "Status"), ("authority", "Authority"), ("requested_by", "Requested by"),
            ("reviewed_by", "Reviewed by"), ("posted", "Posted document"), ("reason", "Reason"),
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
            summary = row.summary or {}
            rows.append({
                "date": row.date,
                "gross_sales": summary.get("sales_total", ""),
                "net_sales": summary.get("net_sales", ""),
                "credit_created": summary.get("credit_created", ""),
                "debt_collections": summary.get("debt_collections", ""),
                "returns": summary.get("returns_total", ""),
                "expenses": summary.get("expenses_total", ""),
                "purchases": summary.get("purchases_total", ""),
                "supplier_payments": summary.get("supplier_debt_payments", ""),
                "inventory_losses": summary.get("inventory_losses", ""),
                "opening_cash": row.opening_cash,
                "other_cash_in": row.cash_in,
                "other_cash_out": row.cash_out,
                "submitted_by": row.submitted_by.username,
                "verified_by": row.verified_by.username if row.verified_by else "",
                "note": row.note,
                **values,
            })
        columns = [
            ("date", "Date"), ("gross_sales", "Gross sales"), ("net_sales", "Net sales"),
            ("credit_created", "Credit created"), ("debt_collections", "Debt collections"),
            ("returns", "Returns"), ("expenses", "Expenses"), ("purchases", "Purchases"),
            ("supplier_payments", "Supplier debt payments"), ("inventory_losses", "Inventory losses"),
            ("opening_cash", "Opening cash"), ("other_cash_in", "Other cash in"),
            ("other_cash_out", "Other cash out"), ("submitted_by", "Submitted by"),
            ("verified_by", "Verified by"),
        ]
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
        rows = []
        for row in Audit.objects.filter(
            Q(branch=branch) | Q(branch__isnull=True),
            created_at__date__gte=first, created_at__date__lte=last,
        ).select_related("actor").order_by("-created_at"):
            integrity = ""
            if row.event_hash:
                predecessor_ok = not row.previous_hash or Audit.objects.filter(
                    branch=row.branch, event_hash=row.previous_hash
                ).exists()
                integrity = "Verified" if s.audit_hash_for(row) == row.event_hash and predecessor_ok else "Review required"
            else:
                integrity = "Legacy unsealed"
            rows.append({
                "date": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                "event_id": str(row.event_id),
                "actor": row.actor.username if row.actor else "System",
                "category": row.category,
                "severity": row.severity,
                "event": row.action,
                "reference": row.reference,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "integrity": integrity,
                "event_hash": row.event_hash,
                "previous_hash": row.previous_hash,
                "evidence": json.dumps(row.detail, sort_keys=True, default=str),
            })
        return rows, [
            ("date", "Timestamp"), ("event_id", "Event ID"), ("actor", "Actor"),
            ("category", "Category"), ("severity", "Severity"), ("event", "Event"),
            ("reference", "Reference"), ("entity_type", "Entity type"), ("entity_id", "Entity ID"),
            ("integrity", "Integrity"), ("event_hash", "Event hash"), ("previous_hash", "Previous hash"),
            ("evidence", "Structured evidence"),
        ]

    if dataset == "workers":
        from .models import Worker
        rows = [{
            "employee_code": worker.employee_code,
            "name": worker.full_name,
            "department": worker.department,
            "job_title": worker.job_title,
            "employment_type": worker.get_employment_type_display(),
            "status": worker.get_status_display(),
            "phone": worker.phone,
            "email": worker.email,
            "hire_date": worker.hire_date,
            "base_salary": worker.base_salary,
            "allowance": worker.recurring_allowance,
            "ssnit_number": worker.ssnit_number,
            "ghana_card": worker.ghana_card_number,
        } for worker in Worker.objects.filter(branch=branch).order_by("last_name", "first_name")]
        return rows, [
            ("employee_code", "Employee ID"), ("name", "Worker"), ("department", "Department"),
            ("job_title", "Job title"), ("employment_type", "Employment type"), ("status", "Status"),
            ("phone", "Phone"), ("email", "Email"), ("hire_date", "Hire date"),
            ("base_salary", "Base salary"), ("allowance", "Recurring allowance"),
            ("ssnit_number", "SSNIT number"), ("ghana_card", "Ghana Card"),
        ]

    if dataset == "payroll":
        from .models import PayrollEntry
        entries = PayrollEntry.objects.filter(
            period__branch=branch, period__end_date__range=(first, last)
        ).select_related("period", "worker")
        rows = [{
            "period": entry.period.label,
            "employee_code": entry.worker.employee_code,
            "worker": entry.worker.full_name,
            "department": entry.worker.department,
            "gross": entry.gross_pay,
            "employee_ssnit": entry.ssnit_employee,
            "tax": entry.paye_tax + entry.bonus_tax + entry.overtime_tax,
            "net": entry.net_pay,
            "paid": entry.paid_amount,
            "outstanding": entry.balance,
            "status": entry.period.get_status_display(),
            "flags": "; ".join(entry.validation_flags or []),
        } for entry in entries]
        return rows, [
            ("period", "Payroll period"), ("employee_code", "Employee ID"), ("worker", "Worker"),
            ("department", "Department"), ("gross", "Gross pay"), ("employee_ssnit", "Employee SSNIT"),
            ("tax", "Tax"), ("net", "Net pay"), ("paid", "Paid"),
            ("outstanding", "Outstanding"), ("status", "Period status"), ("flags", "Validation flags"),
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
        date_suffix = "" if dataset in {"customers", "suppliers", "creditors", "inventory", "staff", "workers", "market_customers", "market_catalog"} else f" · {start} to {end}"
        company = shell(request)["company"]
        return export(
            rows, format, f"{label}{date_suffix}", company, columns,
            filename=f"kofad-{dataset}",
            sheet_name=label[:31],
            metadata={
                "Location": branch.name,
                "Date range": "Current snapshot" if not date_suffix else f"{start} to {end}",
                "Generated": timezone.localtime().strftime("%d %b %Y %H:%M"),
            },
            summary={"Rows exported": len(rows)},
            notes=["Generated directly from KOFAD with the current user's permission and location scope."],
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
