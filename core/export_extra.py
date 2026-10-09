"""Detailed, permission-aware operational registers for the KOFAD export centre.

Do not export payment secrets, handover/OTP codes, message bodies or raw provider
payloads. The caller enforces dataset permissions and the active branch.
"""
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from . import services
from .models import Document, Line, Message, Payment, Product, Stock


EXTRA_DATASETS = {
    "sales_lines": ("Detailed sale items & profit", ("view_reports",)),
    "online_order_lines": ("Online order items", ("operate_sales", "view_reports")),
    "payment_ledger": ("Payment channels & receipt entries", ("operate_finance", "view_reports")),
    "gateway_attempts": ("Online gateway verification history", ("operate_finance", "manage_company")),
    "debt_invoices": ("Outstanding customer invoices", ("operate_finance", "view_reports")),
    "message_delivery": ("SMS & WhatsApp delivery register", ("send_messages", "manage_company")),
    "email_delivery": ("Business email delivery register", ("__owner_only__",)),
    "stock_alerts": ("Low stock & reorder priorities", ("operate_inventory", "view_reports")),
}

EXTRA_COLUMNS = {
    "sales_lines": ["reference", "date", "kind", "customer", "sku", "product", "mode",
                    "quantity", "base_units", "unit_price", "discount", "total", "cost", "gross_profit"],
    "online_order_lines": ["order", "created", "customer", "sku", "product", "mode",
                           "quantity", "base_units", "unit_price", "total", "payment_status", "fulfilment"],
    "payment_ledger": ["date", "reference", "document_type", "contact", "channel",
                       "amount", "direction", "provider_evidence", "staff", "provider_reference"],
    "gateway_attempts": ["created", "order", "provider", "reference", "amount", "currency",
                         "status", "verified", "checks", "order_status", "payment_status", "provider_message"],
    "debt_invoices": ["reference", "customer", "phone", "issued", "due",
                      "days_overdue", "total", "paid_at_sale", "balance", "status"],
    "message_delivery": ["created", "channel", "recipient", "recipient_name", "status",
                         "provider", "sandbox", "attempts", "segments", "source", "last_error"],
    "email_delivery": ["created", "mailbox", "direction", "recipient", "subject",
                       "status", "attempts", "submitted", "last_error"],
    "stock_alerts": ["sku", "product", "category", "sellable", "reorder_level",
                     "shortfall", "unit", "unit_cost", "stock_value", "priority"],
}


def registration_rows(user, branch):
    """Owners see the whole customer register; other authorized managers see
    only customers with an order in their current business location.
    """
    from marketplace.models import CustomerAccount, EmailIdentity, GoogleIdentity, OnlineOrder

    orders = OnlineOrder.objects.all() if user.is_superuser else OnlineOrder.objects.filter(branch=branch)
    customers = CustomerAccount.objects.all()
    if not user.is_superuser:
        customers = customers.filter(pk__in=orders.values("customer_id"))
    customers = list(customers.order_by("-created_at", "-pk")[:10001])
    customer_ids = [customer.pk for customer in customers]
    stats = {
        item["customer_id"]: item for item in orders.filter(customer_id__in=customer_ids)
        .values("customer_id").annotate(
            orders=Count("id"), paid_orders=Count("id", filter=Q(payment_status="paid")),
            paid_spend=Sum("total", filter=Q(payment_status="paid")),
            last_order=Max("created_at"),
        )
    }
    aliases = {
        item.owner_id: item for item in EmailIdentity.objects.filter(
            kind="customer", owner_id__in=customer_ids
        )
    }
    google_ids = set(GoogleIdentity.objects.filter(
        kind="customer", owner_id__in=customer_ids
    ).values_list("owner_id", flat=True))
    rows = []
    for customer in customers:
        stat = stats.get(customer.pk, {})
        alias = aliases.get(customer.pk)
        verified_email = (alias.email if alias and alias.verified_at else "") or ""
        same_email = bool(verified_email and verified_email.casefold() == (customer.email or "").casefold())
        has_phone = bool(customer.phone)
        rows.append({
            "account_id": customer.pk,
            "name": customer.full_name,
            "phone": customer.phone or "",
            "email": customer.email or "",
            "verified_email": verified_email,
            "created": customer.created_at,
            "phone_status": "Verified" if customer.verified_at else ("Unverified" if has_phone else "Not linked"),
            "email_status": ("Verified" if same_email else
                             "Different verified sign-in email" if verified_email else "Not verified"),
            "google_account": "Linked" if customer.pk in google_ids else "Not linked",
            "last_login": customer.last_login_at or "",
            "orders": stat.get("orders", 0),
            "paid_orders": stat.get("paid_orders", 0),
            "paid_spend": stat.get("paid_spend") or Decimal("0.00"),
            "last_order": stat.get("last_order") or "",
            "notifications": "Enabled" if alias and alias.notifications_enabled else "Disabled",
            "marketing_consent": "Opted in" if alias and alias.marketing_emails_enabled else "Not opted in",
            "status": "Active" if customer.active else "Disabled",
        })
    return rows, [
        ("account_id", "Account ID"), ("name", "Registered name"), ("phone", "Phone"),
        ("email", "Profile email"), ("verified_email", "Verified sign-in email"),
        ("created", "Registered on"),
        ("phone_status", "Phone verification"), ("email_status", "Email verification"),
        ("google_account", "Google sign-in"), ("last_login", "Last login"),
        ("orders", "Orders"), ("paid_orders", "Paid orders"),
        ("paid_spend", "Confirmed paid order value"),
        ("last_order", "Latest order"), ("notifications", "Email notifications"),
        ("marketing_consent", "Marketing consent"), ("status", "Account status"),
    ]


def extra_rows(dataset, branch, first, last):
    """Return a complete, bounded business register with accurate source labels."""
    if dataset == "sales_lines":
        items = Line.objects.filter(
            document__branch=branch, document__kind__in=["sale", "return"],
            document__created_at__date__range=(first, last),
        ).select_related("document", "document__party", "product").order_by("-document__created_at", "pk")[:10001]
        rows = []
        for item in items:
            sign = -1 if item.document.kind == "return" else 1
            cost = item.unit_cost * item.quantity * item.factor
            rows.append({
                "reference": item.document.reference, "date": item.document.created_at,
                "kind": item.document.get_kind_display(),
                "customer": item.document.party.name if item.document.party else "Walk-in",
                "sku": item.product.sku, "product": item.description, "mode": item.mode,
                "quantity": item.quantity * sign, "base_units": item.quantity * item.factor * sign,
                "unit_price": item.unit_price, "discount": item.discount_percent,
                "total": item.total * sign, "cost": cost * sign,
                "gross_profit": (item.total - cost) * sign,
            })
        return rows, [
            ("reference", "Receipt"), ("date", "Posted at"), ("kind", "Transaction type"),
            ("customer", "Customer"), ("sku", "SKU"), ("product", "Item"),
            ("mode", "Selling mode"), ("quantity", "Line quantity"),
            ("base_units", "Base units"), ("unit_price", "Unit price"),
            ("discount", "Discount %"), ("total", "Net sales (GHS)"),
            ("cost", "Cost of goods (GHS)"), ("gross_profit", "Gross profit (GHS)"),
        ]

    if dataset == "online_order_lines":
        from marketplace.models import OnlineOrderLine
        items = OnlineOrderLine.objects.filter(
            order__branch=branch, order__created_at__date__range=(first, last),
        ).select_related("order", "order__customer").order_by("-order__created_at", "pk")[:10001]
        return [{
            "order": item.order.public_reference, "created": item.order.created_at,
            "customer": item.order.customer.full_name, "sku": item.sku,
            "product": item.description, "mode": item.mode,
            "quantity": item.quantity, "base_units": item.quantity * item.factor,
            "unit_price": item.unit_price, "total": item.total,
            "payment_status": item.order.get_payment_status_display(),
            "fulfilment": item.order.get_fulfilment_display(),
        } for item in items], [
            ("order", "Order reference"), ("created", "Order created"),
            ("customer", "Registered customer"), ("sku", "SKU"), ("product", "Item"),
            ("mode", "Selling mode"), ("quantity", "Quantity"), ("base_units", "Base units"),
            ("unit_price", "Unit price"), ("total", "Line total"),
            ("payment_status", "Payment state"), ("fulfilment", "Delivery method"),
        ]

    if dataset == "payment_ledger":
        payments = Payment.objects.filter(
            document__branch=branch, document__created_at__date__range=(first, last)
        ).select_related("document", "document__party", "document__created_by", "document__online_order").order_by(
            "-document__created_at", "pk")[:10001]
        rows = []
        for item in payments:
            doc = item.document
            online_order = getattr(doc, "online_order", None)
            evidence = ("Online gateway verified" if online_order and
                        online_order.payment_status in {"paid", "refunded"}
                        else "Recorded in KOFAD; provider verification not asserted")
            rows.append({
                "date": doc.created_at, "reference": doc.reference,
                "document_type": doc.get_kind_display(),
                "contact": doc.party.name if doc.party else "Walk-in",
                "channel": item.get_method_display(), "amount": item.amount,
                "direction": "Incoming" if item.direction == 1 else "Outgoing",
                "provider_evidence": evidence,
                "staff": doc.created_by.username,
                "provider_reference": item.reference,
            })
        return rows, [
            ("date", "Recorded at"), ("reference", "Document"), ("document_type", "Transaction type"),
            ("contact", "Contact"), ("channel", "Payment channel"), ("amount", "Amount (GHS)"),
            ("direction", "Cash-flow direction"), ("provider_evidence", "Verification source"),
            ("staff", "Recorded by"), ("provider_reference", "Reference"),
        ]

    if dataset == "gateway_attempts":
        from marketplace.models import MarketPaymentAttempt
        attempts = MarketPaymentAttempt.objects.filter(
            order__branch=branch, created_at__date__range=(first, last),
        ).select_related("order").order_by("-created_at", "-pk")[:10001]
        return [{
            "created": item.created_at, "order": item.order.public_reference,
            "provider": item.provider, "reference": item.reference,
            "amount": item.amount, "currency": item.currency,
            "status": item.status, "verified": item.verified_at or "",
            "checks": item.check_count,
            "order_status": item.order.get_status_display(),
            "payment_status": item.order.get_payment_status_display(),
            "provider_message": item.provider_message,
        } for item in attempts], [
            ("created", "Requested at"), ("order", "Order"),
            ("provider", "Gateway"), ("reference", "Attempt reference"),
            ("amount", "Expected amount"), ("currency", "Currency"),
            ("status", "Verification state"), ("verified", "Verified at"),
            ("checks", "Status checks"), ("order_status", "Fulfilment"),
            ("payment_status", "Order payment"), ("provider_message", "Provider explanation"),
        ]

    if dataset == "debt_invoices":
        invoices = Document.objects.filter(
            branch=branch, kind="sale", party__isnull=False,
        ).select_related("party").order_by("due_date", "created_at").iterator(chunk_size=350)
        today = timezone.localdate()
        rows = []
        for invoice in invoices:
            amount = services.balance(invoice)
            if amount <= 0:
                continue
            overdue = max((today - invoice.due_date).days, 0) if invoice.due_date else 0
            rows.append({
                "reference": invoice.reference, "customer": invoice.party.name,
                "phone": invoice.party.phone, "issued": invoice.created_at,
                "due": invoice.due_date or "", "days_overdue": overdue,
                "total": invoice.total, "paid_at_sale": invoice.paid, "balance": amount,
                "status": "Overdue" if overdue else "Outstanding",
            })
            if len(rows) > 10000:
                break  # the download handler rejects the report rather than truncating it
        return rows, [
            ("reference", "Sale reference"), ("customer", "Customer"), ("phone", "Phone"),
            ("issued", "Sale created"), ("due", "Due date"), ("days_overdue", "Days overdue"),
            ("total", "Sale total"), ("paid_at_sale", "Paid at sale"),
            ("balance", "Outstanding"), ("status", "Debt status"),
        ]

    if dataset == "message_delivery":
        records = Message.objects.filter(
            branch=branch, created_at__date__range=(first, last),
        ).order_by("-created_at", "-pk")[:10001]
        return [{
            "created": item.created_at, "channel": item.get_channel_display(),
            "recipient": item.recipient, "recipient_name": item.recipient_name,
            "status": item.status, "provider": item.provider or "Not submitted",
            "sandbox": "Test" if item.sandbox else "Live",
            "attempts": item.attempts, "segments": item.segments,
            "source": item.source_key or "", "last_error": item.last_error,
        } for item in records], [
            ("created", "Created"), ("channel", "Channel"), ("recipient", "Recipient"),
            ("recipient_name", "Recipient name"), ("status", "Actual delivery state"),
            ("provider", "Provider"), ("sandbox", "Mode"), ("attempts", "Attempts"),
            ("segments", "SMS segments"), ("source", "Source event"), ("last_error", "Last error"),
        ]

    if dataset == "email_delivery":
        from .email_models import EmailLetter
        messages = EmailLetter.objects.filter(
            created_at__date__range=(first, last),
        ).select_related("mailbox").order_by("-created_at", "-pk")[:10001]
        return [{
            "created": item.created_at, "mailbox": item.mailbox.address,
            "direction": item.get_direction_display(),
            "recipient": item.to_address, "subject": item.subject,
            "status": item.get_status_display(), "attempts": item.attempts,
            "submitted": item.submitted_at or "", "last_error": item.last_error,
        } for item in messages], [
            ("created", "Created"), ("mailbox", "Business mailbox"),
            ("direction", "Direction"), ("recipient", "Recipient"),
            ("subject", "Subject"), ("status", "Delivery state"),
            ("attempts", "Attempts"), ("submitted", "Submitted to provider"),
            ("last_error", "Last error"),
        ]

    if dataset == "stock_alerts":
        quantities = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
        rows = []
        for product in Product.objects.filter(active=True).order_by("name").iterator(chunk_size=350):
            held = quantities.get(product.pk, 0)
            if held > product.reorder_level:
                continue
            rows.append({
                "sku": product.sku, "product": product.name,
                "category": product.category, "sellable": held,
                "reorder_level": product.reorder_level,
                "shortfall": max(product.reorder_level - held, 0),
                "unit": product.base_unit,
                "unit_cost": product.cost, "stock_value": product.cost * held,
                "priority": "Out of stock" if held == 0 else "Reorder now",
            })
            if len(rows) > 10000:
                break  # prevent incomplete inventory reports
        return rows, [
            ("sku", "SKU"), ("product", "Product"),
            ("category", "Category"), ("sellable", "Sellable units"),
            ("reorder_level", "Reorder level"), ("shortfall", "Units to reorder"),
            ("unit", "Base unit"), ("unit_cost", "Unit cost"),
            ("stock_value", "Current stock value"), ("priority", "Priority"),
        ]
    raise ValueError("Unknown operational export")
