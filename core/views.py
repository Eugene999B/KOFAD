import hashlib
import json
import secrets
import uuid
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import F, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.debug import sensitive_post_parameters

from . import services as s
from .context import shell
from .forms import (
    CommunicationSettingsForm, CompanyForm, DebtSettingsForm, FinancePolicyForm,
    LocationSettingsForm, ManagementContactForm, PartyForm, PaymentPolicyForm, ProductForm,
    ReceiptPolicyForm, SalesPolicyForm,
)
from .identity import normalize_ghana_phone, phone_variants
from .models import (
    Access, Audit, Branch, Closing, CommunicationSettings, Company, Correction, DebtSettings,
    Document, HeldSale, Line, LoginAttempt, ManagementContact, Message, MessageTemplate,
    Movement, Operation, Party, Payment, Product, QuarantineItem, Stock, SupplierReturn,
)


def branch_for(request):
    branch = shell(request)["current_branch"]
    if not branch:
        raise PermissionDenied("No active location is assigned to your account. Contact the owner.")
    return branch


def protected(permission):
    def decorator(view):
        @login_required
        @wraps(view)
        def inner(request, *args, **kwargs):
            branch = branch_for(request)
            selected = next((p for p in permission.split("|") if request.user.has_perm("core."+p)),permission.split("|")[0])
            s.permit(request.user, branch, selected)
            try:
                return view(request, branch, *args, **kwargs)
            except ValidationError as exc:
                return render(request, "error.html", {"title": "Check your request", "error": problem(exc)}, status=400)
        return inner
    return decorator


def problem(exc):
    return "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)


def resolve_login_identifier(identifier):
    """Resolve a login name to one user without weakening per-account lockout."""
    username_matches = list(
        User.objects.filter(username__iexact=identifier).values_list("username", flat=True)[:2]
    )
    if len(username_matches) == 1:
        return username_matches[0], "user:" + username_matches[0].casefold()

    try:
        canonical_phone = normalize_ghana_phone(identifier)
        variants = phone_variants(canonical_phone)
    except ValidationError:
        return identifier, "identifier:" + identifier.casefold()

    phone_matches = list(
        Access.objects.filter(recovery_phone__in=variants)
        .values_list("user__username", flat=True)
        .distinct()[:2]
    )
    if len(phone_matches) == 1:
        return phone_matches[0], "user:" + phone_matches[0].casefold()
    return identifier, "phone:" + canonical_phone


@sensitive_post_parameters("password")
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    error = ""
    if request.method == "POST":
        identifier = request.POST.get("username", "").strip()[:150]
        canonical, lock_identity = resolve_login_identifier(identifier)
        # Per-account lock avoids trusting spoofable forwarded IP headers and
        # keeps alternate phone formats on the same lockout bucket.
        key = hashlib.sha256(lock_identity.encode()).hexdigest()
        with transaction.atomic():
            LoginAttempt.objects.get_or_create(key=key)
            attempt = LoginAttempt.objects.select_for_update().get(key=key)
            if attempt.blocked_until and attempt.blocked_until > timezone.now():
                error = "Too many attempts. Please wait 15 minutes."
            else:
                if attempt.blocked_until:
                    attempt.failures = 0
                    attempt.blocked_until = None
                user = authenticate(request, username=canonical, password=request.POST.get("password", ""))
                if user is not None:
                    access, _ = Access.objects.get_or_create(user=user)
                    login(request, user)
                    request.session["access_version"] = access.session_version
                    request.session.pop("enroll_secret", None)
                    attempt.failures = 0
                    attempt.save()
                    s.audit(user, None, "session.login", user.pk)
                    if access.force_password_change:
                        messages.info(request, "Change the temporary password before continuing.")
                        return redirect("password_change")
                    return redirect("dashboard")
                attempt.failures += 1
                if attempt.failures >= 5:
                    attempt.blocked_until = timezone.now() + timedelta(minutes=15)
                attempt.save()
                error = "The username, phone number or password is incorrect."
    return render(request, "login.html", {"error": error, "username": request.POST.get("username", "")})


@login_required
def mfa(request):
    request.session.pop("enroll_secret", None)
    return redirect("dashboard")


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
@require_POST
def switch_branch(request):
    available = shell(request)["branches"]
    branch = get_object_or_404(available, pk=request.POST.get("branch"))
    request.session["branch"] = branch.pk
    return redirect("dashboard")


@login_required
def dashboard(request):
    branch = branch_for(request)
    if not request.user.has_perm("core.view_reports"):
        if request.user.has_perm("core.operate_sales"):
            return redirect("pos")
        return redirect("inventory")
    s.permit(request.user, branch, "view_reports")
    today = timezone.localdate()
    docs = Document.objects.filter(branch=branch)
    sales = docs.filter(kind="sale", created_at__date=today)
    returned = docs.filter(kind="return", created_at__date=today)
    revenue = (sales.aggregate(t=Sum("total"))["t"] or 0) - (returned.aggregate(t=Sum("total"))["t"] or 0)
    expenses = (docs.filter(kind="expense", created_at__date=today).aggregate(t=Sum("total"))["t"] or 0) - (docs.filter(kind="reversal", original__kind="expense", created_at__date=today).aggregate(t=Sum("total"))["t"] or 0)
    debt = sum((s.party_debt(p) for p in Party.objects.filter(branch=branch, kind="customer")), Decimal(0))
    stock = Stock.objects.filter(branch=branch).select_related("product")
    low = stock.filter(quantity__lte=F("product__reorder_level"))
    week = []
    for offset in reversed(range(7)):
        day = today - timedelta(days=offset)
        value = docs.filter(kind="sale", created_at__date=day).aggregate(t=Sum("total"))["t"] or 0
        week.append({"day": day.strftime("%a"), "amount": value})
    maximum = max([d["amount"] for d in week] + [1])
    for d in week:
        d["height"] = round(float(d["amount"] / maximum) * 110) if d["amount"] else 2
        d["y"] = 130 - d["height"]
    has_products = Product.objects.filter(active=True).exists()
    has_stock = stock.filter(quantity__gt=0).exists()
    has_sales = docs.filter(kind="sale").exists()
    setup_complete = sum((has_products, has_stock, has_sales))
    from .approval_views import pending_approval_count
    pending_approvals = pending_approval_count(request.user, branch)
    return render(request, "dashboard.html", {"title": "Command centre", "revenue": revenue, "expenses": expenses,
        "debt": debt, "low": low[:6], "low_count": low.count(), "recent": docs[:7], "week": week,
        "pending_approvals": pending_approvals, "today": today,
        "channels": s.channel_totals(branch, today).items(), "sales_count": sales.count(),
        "needs_setup": setup_complete < 3, "setup_complete": setup_complete,
        "has_products": has_products, "has_stock": has_stock, "has_sales": has_sales})


@protected("operate_sales")
def pos(request, branch):
    return trade_screen(request, branch, "sale")


@protected("operate_inventory")
def purchasing(request, branch):
    return trade_screen(request, branch, "purchase")


def trade_screen(request, branch, kind):
    catalog = []
    stock = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
    query = request.GET.get("q", "").strip()[:100]
    ids = [value for value in request.GET.get("ids", "").split(",") if value.isdigit()][:100]
    products = Product.objects.filter(active=True)
    if ids:
        products = products.filter(pk__in=ids)
    elif query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(sku__icontains=query) |
            Q(barcode__icontains=query) |
            Q(category__icontains=query)
        )
    else:
        products = products.none()

    for p in products.order_by("name")[:30]:
        item = {
            "id": p.pk,
            "name": p.name,
            "sku": p.sku,
            "barcode": p.barcode,
            "category": p.category,
            "pack_size": p.pack_size,
            "pack_name": p.pack_name,
            "base_unit": p.base_unit,
            "stock": stock.get(p.pk, 0),
            "prices": {
                key: str(getattr(p, key))
                for key in ("retail_unit", "retail_pack", "wholesale_unit", "wholesale_pack")
                if getattr(p, key) is not None
            },
        }
        if kind == "purchase":
            item["cost"] = str(p.cost)
        catalog.append(item)

    if request.GET.get("format") == "json":
        return JsonResponse({
            "catalog": catalog,
            "query": query,
            "count": len(catalog),
            "search_required": not bool(query or ids),
        })

    company = s.company_policy()
    payment_methods = s.active_payment_methods(company)
    return render(request, "pos.html", {
        "title": "New sale" if kind == "sale" else "Receive purchase",
        "catalog": catalog,
        "kind": kind,
        "key": str(uuid.uuid4()),
        "q": query,
        "parties": Party.objects.filter(branch=branch, kind="customer" if kind == "sale" else "supplier"),
        "held": HeldSale.objects.filter(branch=branch, user=request.user),
        "purchase": kind == "purchase",
        "today": timezone.localdate(),
        "payment_methods": payment_methods,
        "payment_method_codes": [code for code, _ in payment_methods],
        "cash_enabled": any(code == "cash" for code, _ in payment_methods),
        "allow_discounts": kind == "sale" and company.allow_discounts,
        "allow_price_overrides": kind == "sale" and company.allow_price_overrides,
        "max_discount": company.max_discount_percent,
        "max_price_reduction": company.max_price_reduction_percent,
        "credit_override_available": kind == "sale" and company.max_credit_override > 0,
        "allow_credit_sales": kind == "sale" and company.allow_credit_sales,
        "max_credit_days": company.max_credit_days,
        "policy_controls": kind == "sale" and (
            company.allow_discounts or company.allow_price_overrides or company.max_credit_override > 0
        ),
    })

def _receipt_sms_result(user, branch, doc):
    """Send or resolve the receipt SMS for one sale without duplicating a prior accepted send."""
    if not user.has_perm("core.send_messages"):
        raise PermissionDenied
    if not doc.party_id:
        raise ValidationError("This is a walk-in sale with no customer phone number.")
    if not doc.party.consent:
        raise ValidationError("Customer SMS consent is not enabled for this sale.")
    if not settings.SMS_ENABLED:
        raise ValidationError("SMS delivery is not enabled for this deployment.")

    from .sms.service import create_draft, send_message_now
    from .sms.templates import render_for_document

    body = render_for_document(doc, "receipt")
    item = Message.objects.filter(
        branch=branch, party=doc.party
    ).filter(
        Q(source_key=f"auto:receipt:{doc.pk}")
        | Q(source_key=f"document:receipt:{doc.pk}")
        | Q(source_key__startswith=f"receipt:{doc.pk}:")
    ).order_by("-created_at").first()

    retryable = {"failed", "undelivered", "expired"}
    settled = {"sending", "accepted", "delivered", "simulated"}
    if item and item.status in settled:
        label = {
            "sending": "sending",
            "accepted": "sent",
            "delivered": "delivered",
            "simulated": "test sent",
        }.get(item.status, item.status)
        return item, f"Receipt SMS is already {label}."
    if item and item.status == "unknown":
        return item, "The previous SMS delivery result is unknown. Check the provider result before sending again."
    if item and item.status in retryable:
        sent = send_message_now(user, branch, item.pk, retry=True)
    else:
        if not item:
            item = create_draft(
                user, branch, doc.party, body,
                source_key=f"document:receipt:{doc.pk}",
            )
        sent = send_message_now(user, branch, item.pk)

    message = (
        "Receipt SMS sent to Arkesel."
        if sent.status == "accepted"
        else "Receipt SMS delivered."
        if sent.status == "delivered"
        else "Receipt SMS test completed."
        if sent.status == "simulated"
        else sent.last_error or f"Receipt SMS {sent.status}."
    )
    return sent, message


@login_required
@require_POST
def complete_trade(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValidationError("Expected a transaction object.")
        branch = branch_for(request)
        doc = s.post_trade(
            request.user,
            branch,
            data,
            request.headers.get("Idempotency-Key"),
            kind=data.get("kind", "sale"),
        )
        party = doc.party
        can_send_sms = bool(
            doc.kind == "sale"
            and party
            and party.consent
            and request.user.has_perm("core.send_messages")
            and settings.SMS_ENABLED
        )
        sms_reason = (
            ""
            if can_send_sms
            else "No customer is attached to this sale."
            if not party
            else "Customer SMS consent is not enabled."
            if not party.consent
            else "Your account does not have message-sending permission."
            if not request.user.has_perm("core.send_messages")
            else "SMS delivery is not enabled for this deployment."
        )

        sms_requested = bool(doc.kind == "sale" and data.get("customer_consent") is True)
        sms_status = ""
        sms_message = ""
        if sms_requested:
            if can_send_sms:
                try:
                    sent, sms_message = _receipt_sms_result(request.user, branch, doc)
                    sms_status = sent.status
                except (ValidationError, ValueError) as exc:
                    sms_status = "failed"
                    sms_message = problem(exc)
            else:
                sms_status = "failed"
                sms_message = sms_reason

        return JsonResponse({
            "url": f"/documents/{doc.pk}/",
            "document_id": str(doc.pk),
            "reference": doc.reference,
            "total": str(doc.total),
            "paid": str(doc.paid),
            "customer": (
                {"name": party.name, "phone": party.phone, "consent": party.consent}
                if party else None
            ),
            "can_send_sms": can_send_sms,
            "sms_reason": sms_reason,
            "sms_requested": sms_requested,
            "sms_status": sms_status,
            "sms_message": sms_message,
        })
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        return JsonResponse({"error": problem(exc)}, status=400)


@login_required
@require_POST
def send_transaction_message_api(request, pk):
    branch = branch_for(request)
    doc = get_object_or_404(
        Document.objects.select_related("party"), pk=pk, branch=branch, kind="sale"
    )
    if not request.user.has_perm("core.operate_sales"):
        raise PermissionDenied
    try:
        sent, message = _receipt_sms_result(request.user, branch, doc)
        ok = sent.status in {"accepted", "delivered", "simulated", "sending"}
        if sent.status == "unknown":
            return JsonResponse({"error": message, "status": sent.status}, status=409)
        return JsonResponse(
            {"ok": ok, "status": sent.status, "message": message},
            status=200 if ok else 502,
        )
    except PermissionDenied:
        return JsonResponse({"error": "Message-sending permission is required."}, status=403)
    except (ValidationError, ValueError) as exc:
        return JsonResponse({"error": problem(exc)}, status=400)


@protected("operate_sales")
@require_POST
def hold(request, branch):
    try:
        data = json.loads(request.body)
        if len(request.body) > 60000 or not isinstance(data.get("items"), list):
            raise ValidationError("Invalid held cart.")
        held = HeldSale.objects.create(branch=branch, user=request.user, label=str(data.get("label", "Held sale"))[:100], cart=data)
        return JsonResponse({"id": held.pk})
    except (ValueError, ValidationError, TypeError) as exc:
        return JsonResponse({"error": problem(exc)}, status=400)


@protected("operate_sales")
def held(request, branch, pk):
    item = get_object_or_404(HeldSale, pk=pk, branch=branch, user=request.user)
    if request.method == "POST":
        item.delete()
        return JsonResponse({"ok": True})
    return JsonResponse(item.cart)


@login_required
def documents(request):
    branch = branch_for(request)
    if not any(request.user.has_perm("core." + p) for p in ("operate_sales", "view_reports", "operate_finance", "operate_inventory")):
        raise PermissionDenied
    kind = request.GET.get("kind", "sale")
    allowed = ["sale", "return"] if not request.user.has_perm("core.view_reports") else list(dict(Document.KINDS))
    if request.user.has_perm("core.operate_inventory"):
        allowed += ["purchase", "supplier_return", "inventory_writeoff"]
    if request.user.has_perm("core.operate_finance"):
        allowed += ["expense", "collection", "supplier_payment", "creditor_charge", "supplier_return", "inventory_writeoff"]
    if kind not in allowed:
        raise PermissionDenied
    rows = Document.objects.filter(branch=branch, kind=kind).select_related("party", "created_by")
    q = request.GET.get("q", "")[:100]
    if q:
        rows = rows.filter(Q(reference__icontains=q) | Q(party__name__icontains=q))
    return render(request, "documents.html", {"title": dict(Document.KINDS).get(kind, "Transactions"),
        "rows": rows[:200], "kind": kind, "q": q})


@login_required
def document(request, pk):
    branch = branch_for(request)
    doc = get_object_or_404(
        Document.objects.select_related("party", "created_by", "branch", "original"),
        pk=pk, branch=branch,
    )
    permission = "operate_sales" if doc.kind in ("sale", "return") else (
        "operate_inventory" if doc.kind in ("purchase", "supplier_return", "inventory_writeoff") else "operate_finance"
    )
    if not request.user.has_perm("core.view_reports"):
        s.permit(request.user, branch, permission)
    return render(request, "document.html", {
        "title": doc.reference,
        "doc": doc,
        "outstanding": s.balance(doc) if doc.kind in ("sale", "purchase", "creditor_charge") else None,
    })


@login_required
def document_pdf(request, pk, format):
    if format not in {"a4", "thermal80", "thermal58"}:
        raise Http404("Unknown receipt format.")
    branch = branch_for(request)
    doc = get_object_or_404(
        Document.objects.select_related("party", "created_by", "branch", "original"),
        pk=pk, branch=branch
    )
    permission = "operate_sales" if doc.kind in ("sale", "return") else (
        "operate_inventory" if doc.kind in ("purchase", "supplier_return", "inventory_writeoff") else "operate_finance"
    )
    if not request.user.has_perm("core.view_reports"):
        s.permit(request.user, branch, permission)
    from .receipt_pdf import render_receipt_pdf
    response = render_receipt_pdf(doc, format)
    s.audit(request.user, branch, "receipt.pdf_opened", doc.reference, {"format": format})
    return response


@protected("operate_inventory|view_reports")
def inventory(request, branch):
    if request.method == "POST":
        try:
            s.permit(request.user, branch, "operate_inventory")
            result = s.restock_inventory(
                request.user,
                branch,
                request.POST.get("product"),
                request.POST.get("packs", 0),
                request.POST.get("loose", 0),
                request.POST.get("note", ""),
                request.POST.get("reference", ""),
                request.POST.get("unit_cost", ""),
            )
            messages.success(
                request,
                f"Restocked {result['product'].name}: +{result['added']} {result['product'].base_unit}. "
                f"New sellable balance: {result['after']}."
            )
            return redirect("inventory")
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))

    q = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "all")
    if status not in {"all", "low", "out", "healthy", "quarantine"}:
        status = "all"

    products = Product.objects.all().order_by("name")
    if q:
        products = products.filter(
            Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__icontains=q) | Q(category__icontains=q)
        )
    stocks = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
    quarantined = dict(
        QuarantineItem.objects.filter(branch=branch, status="held")
        .values("product_id").annotate(total=Sum("quantity")).values_list("product_id", "total")
    )
    rows = []
    for p in products[:500]:
        quantity = stocks.get(p.pk, 0)
        quarantine_qty = quarantined.get(p.pk, 0)
        row = {
            "product": p,
            "quantity": quantity,
            "quarantine": quarantine_qty,
            "physical": quantity + quarantine_qty,
            "packs": quantity // p.pack_size,
            "loose": quantity % p.pack_size,
            "pack_equivalent": (Decimal(quantity) / Decimal(p.pack_size)) if p.pack_size > 1 else Decimal(quantity),
            "physical_pack_equivalent": (
                Decimal(quantity + quarantine_qty) / Decimal(p.pack_size)
                if p.pack_size > 1 else Decimal(quantity + quarantine_qty)
            ),
            "low": quantity <= p.reorder_level,
            "out": quantity == 0,
            "sellable_value": Decimal(quantity) * p.cost,
        }
        if status == "low" and not (row["low"] and quantity > 0):
            continue
        if status == "out" and not row["out"]:
            continue
        if status == "healthy" and (row["low"] or row["out"]):
            continue
        if status == "quarantine" and not quarantine_qty:
            continue
        rows.append(row)

    all_products = Product.objects.all()
    all_stock = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
    catalog_count = all_products.count()
    out_count = sum(1 for p in all_products if all_stock.get(p.pk, 0) == 0)
    low_count = sum(
        1 for p in all_products
        if 0 < all_stock.get(p.pk, 0) <= p.reorder_level
    )
    sellable_value = sum(
        (Decimal(all_stock.get(p.pk, 0)) * p.cost for p in all_products),
        Decimal("0"),
    )
    quarantine_units = sum(quarantined.values(), 0)
    movements = Movement.objects.filter(branch=branch).select_related("product", "actor").order_by("-created_at")[:20]

    return render(request, "inventory.html", {
        "title": "Inventory",
        "rows": rows,
        "q": q,
        "status": status,
        "catalog_count": catalog_count,
        "out_count": out_count,
        "low_count": low_count,
        "sellable_value": sellable_value,
        "quarantine_units": quarantine_units,
        "movements": movements,
        "restock_key": str(uuid.uuid4()),
    })

@protected("change_product")
def product_edit(request, branch, pk=None):
    obj = get_object_or_404(Product, pk=pk) if pk else None
    if not obj and not request.user.has_perm("core.add_product"):
        raise PermissionDenied
    form = ProductForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        opening_total = getattr(form, "opening_total", 0)
        if opening_total and not request.user.has_perm("core.operate_inventory"):
            form.add_error(None, "Inventory permission is required to record opening stock.")
        else:
            with transaction.atomic():
                before = {k: str(v) for k, v in (Product.objects.filter(pk=pk).values().first() or {}).items()}
                product = form.save()
                if opening_total:
                    s.stock_move(
                        request.user, branch, product, opening_total,
                        f"OPEN-{product.sku}", "Opening stock recorded during product setup"
                    )
                s.audit(request.user, branch, "product.saved", product.sku,
                        {"before": before, "after": {k: str(v) for k, v in form.cleaned_data.items()},
                         "opening_stock_base_units": opening_total})
            messages.success(request, "Product saved" + (f" with {opening_total} opening base units." if opening_total else "."))
            return redirect("inventory")
    return render(request, "product_form.html", {
        "title": "Edit product" if pk else "New product",
        "form": form,
        "editing": bool(pk),
        "description": "Choose whether this product is sold as a single unit or from packs/boxes. KOFAD keeps stock in the smallest sellable unit so pack remainders stay exact.",
    })


@protected("operate_sales|operate_finance|view_reports")
def customer_search(request, branch):
    query = request.GET.get("q", "").strip()[:100]
    rows = Party.objects.filter(branch=branch, kind="customer")
    if query:
        phone_filter = Q(phone__icontains=query)
        try:
            from .identity import phone_variants
            phone_filter |= Q(phone__in=phone_variants(query))
        except ValidationError:
            pass
        rows = rows.filter(Q(name__icontains=query) | phone_filter)
    results = []
    for party in rows.order_by("name")[:20]:
        sales = Document.objects.filter(branch=branch, party=party, kind="sale")
        last_sale = sales.order_by("-created_at").first()
        results.append({
            "id": party.pk,
            "name": party.name,
            "phone": party.phone,
            "consent": party.consent,
            "outstanding": str(s.party_debt(party)),
            "purchase_count": sales.count(),
            "last_purchase_at": last_sale.created_at.isoformat() if last_sale else "",
        })
    return JsonResponse({"customers": results})


@protected("operate_sales|operate_finance|view_reports")
def customer_profile(request, branch, pk):
    from . import debts as debt_service
    party = get_object_or_404(Party, pk=pk, branch=branch, kind="customer")
    if request.method == "POST":
        try:
            s.permit(request.user, branch, "operate_finance")
            doc = debt_service.post_customer_payment(
                request.user, branch, {
                    "party": party.pk,
                    "amount": request.POST.get("amount"),
                    "pay_full": request.POST.get("pay_full", ""),
                    "method": request.POST.get("method"),
                    "reference": request.POST.get("reference", ""),
                },
                request.POST.get("key"),
            )
            return redirect("document", pk=doc.pk)
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))
    snapshot = debt_service.customer_account_snapshot(party)
    activity = Document.objects.filter(branch=branch, party=party).select_related(
        "created_by", "original"
    ).order_by("-created_at")[:100]
    return render(request, "customer_profile.html", {
        "title": party.name,
        "party": party,
        "account": snapshot,
        "activity": activity,
        "methods": s.active_payment_methods(),
        "key": request.POST.get("key") or str(uuid.uuid4()),
    })


@protected("operate_sales|operate_finance|view_reports")
def debts(request, branch):
    from . import debts as debt_service
    if request.method == "POST":
        try:
            s.permit(request.user, branch, "operate_finance")
            doc = debt_service.post_customer_payment(
                request.user, branch, request.POST.dict(), request.POST.get("key")
            )
            messages.success(request, f"Payment recorded. Receipt {doc.reference} is ready.")
            return redirect(f"/debts/?customer={doc.party_id}&payment={doc.pk}")
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))

    query = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "all")
    if status not in {"all", "overdue", "due_today", "current"}:
        status = "all"

    overview = debt_service.debt_overview(branch, query)
    rows = overview["rows"]
    if status == "overdue":
        rows = [row for row in rows if row["overdue"] > 0]
    elif status == "due_today":
        rows = [row for row in rows if row["due_today"] > 0]
    elif status == "current":
        rows = [row for row in rows if row["overdue"] == 0]

    selected = None
    selected_id = request.GET.get("customer", "") or (request.POST.get("party", "") if request.method == "POST" else "")
    if selected_id.isdigit():
        selected = next((row for row in rows if row["party"].pk == int(selected_id)), None)
        if selected is None:
            party = Party.objects.filter(pk=selected_id, branch=branch, kind="customer").first()
            if party:
                snapshot = debt_service.customer_account_snapshot(party)
                if snapshot["outstanding"] > 0:
                    selected = {"party": party, **snapshot}
    if selected is None and rows:
        selected = rows[0]

    payment_doc = None
    payment_id = request.GET.get("payment", "")
    if payment_id:
        payment_doc = Document.objects.filter(
            pk=payment_id, branch=branch, kind="collection"
        ).select_related("party").first()

    return render(request, "debts.html", {
        "title": "Customer debts",
        "q": query,
        "status": status,
        "overview": overview,
        "rows": rows,
        "selected": selected,
        "methods": s.active_payment_methods(),
        "key": request.POST.get("key") or str(uuid.uuid4()),
        "payment_doc": payment_doc,
        "open_payment_dialog": request.method == "POST",
    })

@login_required
def parties(request):
    branch = branch_for(request)
    kind = "supplier" if request.GET.get("kind") == "supplier" else "customer"
    permission = "operate_inventory" if kind == "supplier" else "operate_sales"
    if not request.user.has_perm("core.view_reports"):
        s.permit(request.user, branch, permission)
    rows = Party.objects.filter(branch=branch, kind=kind)
    q = request.GET.get("q", "")[:100]
    if q:
        rows = rows.filter(Q(name__icontains=q) | Q(phone__icontains=q))
    return render(request, "parties.html", {"title": "Suppliers" if kind == "supplier" else "Customers",
        "rows": [{"party": p, "debt": s.party_debt(p)} for p in rows[:200]], "kind": kind, "q": q})


@login_required
def party_edit(request, pk=None):
    branch = branch_for(request)
    kind = "supplier" if request.GET.get("kind") == "supplier" else "customer"
    s.permit(request.user, branch, "add_party" if not pk else "change_party")
    obj = get_object_or_404(Party, pk=pk, branch=branch) if pk else None
    actual_kind = obj.kind if obj else kind
    if not request.user.has_perm("core.operate_finance"):
        s.permit(request.user,branch,"operate_inventory" if actual_kind == "supplier" else "operate_sales")
    form = PartyForm(request.POST or None, instance=obj)
    if not request.user.has_perm("core.operate_finance"):
        form.fields.pop("credit_limit")
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            item = form.save(commit=False)
            item.branch = branch
            item.kind = obj.kind if obj else kind
            item.save()
            s.audit(request.user, branch, "party.saved", item.pk, {"name": item.name})
        return redirect("/parties/?kind=" + item.kind)
    return render(request, "form.html", {"title": "Edit contact" if pk else "New contact", "form": form,
        "description": "Name and phone are enough to start. Credit limits are controlled by finance."})


@login_required
def statement(request, pk):
    branch = branch_for(request)
    s.permit(request.user, branch, "view_reports" if request.user.has_perm("core.view_reports") else "operate_finance")
    party = get_object_or_404(Party, pk=pk, branch=branch)
    docs = Document.objects.filter(party=party).select_related("original").order_by("created_at")
    running = Decimal(0)
    rows = []
    for doc in docs:
        if doc.kind in ("sale", "purchase", "creditor_charge"):
            change = doc.total
        elif doc.kind in ("collection", "supplier_payment"):
            change = -sum((a.amount for a in doc.allocations.all()), Decimal(0))
        elif doc.kind in ("return", "supplier_return"):
            change = -sum((a.amount for a in doc.allocations.all()), Decimal(0))
        elif doc.kind == "reversal" and doc.original_id and doc.original.kind in ("collection", "supplier_payment"):
            change = sum((a.amount for a in doc.original.allocations.all()), Decimal(0))
        elif doc.kind == "reversal" and doc.original_id and doc.original.kind == "creditor_charge":
            change = -doc.original.total
        else:
            change = Decimal(0)
        running += change
        rows.append({"doc": doc, "change": change, "running": running})
    return render(request, "statement.html", {"title": party.name, "party": party, "rows": rows, "balance": running})


@protected("operate_finance")
def finance(request, branch):
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "expense":
                doc = s.post_expense(request.user, branch, request.POST.dict(), request.POST.get("key"))
            elif action in ("collection", "supplier_payment"):
                doc = s.post_payment(request.user, branch, request.POST.dict(), request.POST.get("key"), action == "supplier_payment")
            else:
                raise ValidationError("Invalid action.")
            return redirect("document", pk=doc.pk)
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))
    invoices = Document.objects.filter(branch=branch, kind__in=["sale", "purchase", "creditor_charge"], party__isnull=False)
    outstanding = [{"doc": d, "balance": s.balance(d)} for d in invoices]
    from .accounting_views import EXPENSE_CATEGORIES
    return render(request, "finance.html", {"title": "Expenses", "key": request.POST.get("key") or str(uuid.uuid4()),
        "outstanding": [r for r in outstanding if r["balance"] > 0], "methods": s.active_payment_methods(),
        "expense_categories": EXPENSE_CATEGORIES,
        "recent": Document.objects.filter(branch=branch, kind__in=["expense", "collection", "supplier_payment"])[:20]})


@protected("operate_sales|approve_operations|manage_company")
def returns(request, branch):
    from . import returns as return_controls
    from .models import CustomerReturnRequest

    if not (
        request.user.is_superuser or request.user.has_perm("core.manage_company")
        or request.user.has_perm("core.operate_sales") or request.user.has_perm("core.approve_operations")
    ):
        raise PermissionDenied("Sales or return-control access is required.")

    query = request.GET.get("q", "").strip()[:100]
    sale_id = request.GET.get("sale", "").strip()
    sales = Document.objects.filter(branch=branch, kind="sale").select_related("party")
    if query:
        sales = sales.filter(
            Q(reference__icontains=query) | Q(party__name__icontains=query) | Q(party__phone__icontains=query)
        )
    else:
        sales = sales.none()
    sales = sales.order_by("-created_at")[:40]

    selected = None
    if sale_id:
        selected = Document.objects.filter(pk=sale_id, branch=branch, kind="sale").select_related("party").first()
    elif query and len(sales) == 1:
        selected = sales[0]

    if request.method == "POST":
        try:
            selected = get_object_or_404(Document, pk=request.POST.get("sale"), branch=branch, kind="sale")
            lines = []
            for line in selected.lines.all():
                if request.POST.get(f"selected_{line.pk}") == "on":
                    lines.append({
                        "line": str(line.pk),
                        "quantity": request.POST.get(f"quantity_{line.pk}", ""),
                        "disposition": request.POST.get(f"disposition_{line.pk}", "sellable"),
                    })
            item, direct = return_controls.create_customer_return(
                request.user, branch, selected, lines,
                request.POST.get("reason", ""), request.POST.get("refund_method", "cash"),
            )
            if direct and item.posted_id:
                messages.success(request, f"Customer return {item.posted.reference} posted immediately under direct-return authority.")
                return redirect("document", pk=item.posted_id)
            messages.success(request, "Customer return sent to the Approval Center. Stock, debt and cash remain unchanged until approval.")
            return redirect(f"/returns/?sale={selected.pk}")
        except (ValidationError, PermissionDenied, ValueError) as exc:
            messages.error(request, problem(exc))

    line_rows = return_controls.sale_return_rows(selected) if selected else []
    history = CustomerReturnRequest.objects.filter(branch=branch).select_related(
        "sale", "sale__party", "requested_by", "reviewed_by", "posted"
    ).prefetch_related("lines")
    if selected:
        history = history.filter(sale=selected)
    return render(request, "returns.html", {
        "title": "Customer Returns", "q": query, "sales": sales, "selected": selected,
        "line_rows": line_rows, "methods": s.active_payment_methods(),
        "direct_authority": return_controls.can_direct_return(request.user, branch, "customer"),
        "history": history[:80],
    })


@protected("operate_inventory|approve_operations|operate_finance|manage_company")
def supplier_returns(request, branch):
    from . import inventory_exceptions as ix
    from .returns import can_direct_return

    if request.method == "POST":
        try:
            selected_ids = request.POST.getlist("selected")
            if not selected_ids and request.POST.get("line"):
                selected_ids = [request.POST.get("line")]
            if not selected_ids:
                raise ValidationError("Select at least one purchase item to return.")
            completed = []
            direct = can_direct_return(request.user, branch, "supplier")
            with transaction.atomic():
                for line_id in selected_ids:
                    item = ix.request_supplier_return(
                        request.user, branch, line_id, request.POST.get(f"quantity_{line_id}") or request.POST.get("quantity"),
                        request.POST.get("reason", ""), request.POST.get("refund_method", "cash"),
                        direct=direct,
                    )
                    completed.append(item)
            messages.success(
                request,
                f"{len(completed)} supplier return item(s) {'posted immediately under direct authority' if direct else 'sent to the Approval Center'}."
            )
            return redirect("supplier_returns")
        except (ValidationError, PermissionDenied, ValueError) as exc:
            messages.error(request, problem(exc))

    query = request.GET.get("q", "").strip()[:100]
    purchase_ids = Document.objects.filter(branch=branch, kind="purchase")
    if query:
        purchase_ids = purchase_ids.filter(
            Q(reference__icontains=query) | Q(external_reference__icontains=query)
            | Q(party__name__icontains=query) | Q(party__phone__icontains=query)
        ).values_list("pk", flat=True)
        lines = Line.objects.filter(document_id__in=purchase_ids).select_related("document", "document__party", "product")
    else:
        lines = Line.objects.none()
    line_rows = []
    for line in lines[:100]:
        reserved = SupplierReturn.objects.filter(
            source_line=line, status__in=["requested", "approved"]
        ).aggregate(total=Sum("quantity"))["total"] or 0
        line_rows.append({"line": line, "eligible": max(line.quantity - reserved, 0), "reserved": reserved})
    return render(request, "supplier_returns.html", {
        "title": "Supplier Returns", "q": query, "line_rows": line_rows,
        "methods": s.active_payment_methods(),
        "direct_authority": can_direct_return(request.user, branch, "supplier"),
        "rows": SupplierReturn.objects.filter(branch=branch).select_related(
            "source_line__product", "source_line__document", "source_line__document__party",
            "requested_by", "reviewed_by", "posted"
        )[:100],
    })


@protected("operate_inventory|approve_operations|manage_company")
def quarantine(request, branch):
    from . import inventory_exceptions as ix
    if request.method == "POST":
        try:
            action = request.POST.get("action", "request")
            if action == "request":
                direct = request.user.is_superuser or request.user.has_perm("core.manage_company")
                ix.request_quarantine(
                    request.user, branch, request.POST.get("product"), request.POST.get("quantity"),
                    request.POST.get("reason", ""), direct=direct,
                )
                messages.success(request, "Damaged stock moved to quarantine under owner authority." if direct else "Quarantine request submitted for approval.")
            elif action in ("approve", "reject"):
                ix.review_quarantine(
                    request.user, branch, request.POST.get("id"), action == "approve",
                    direct=request.user.is_superuser or request.user.has_perm("core.manage_company"),
                )
                messages.success(request, "Quarantine review recorded.")
            elif action in ("release", "writeoff"):
                ix.resolve_quarantine(
                    request.user, branch, request.POST.get("id"), action, request.POST.get("note", ""),
                    owner_direct=request.user.is_superuser or request.user.has_perm("core.manage_company"),
                )
                messages.success(request, "Quarantine resolution recorded.")
            else:
                raise ValidationError("Choose a valid quarantine action.")
            return redirect("quarantine")
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))
    return render(request, "quarantine.html", {
        "title": "Damaged stock quarantine",
        "products": Product.objects.filter(active=True),
        "rows": QuarantineItem.objects.filter(branch=branch).select_related(
            "product", "requested_by", "reviewed_by", "resolved_by", "loss_document"
        )[:100],
    })


@protected("operate_inventory|approve_operations")
def operations(request, branch):
    # Production KOFAD is intentionally single-store: Stock Operations is retired.
    # The legacy flow remains reachable only in DEBUG so the isolated historical
    # browser-smoke fixture can exercise transfer invariants without exposing the
    # workflow to real users.
    if not settings.DEBUG:
        messages.info(request, "Stock Operations has been retired for the current single-store KOFAD setup. Use Inventory Verification for discrepancies and Purchasing for stock receipts.")
        return redirect("inventory")
    if request.method == "POST":
        try:
            if request.POST.get("action") == "request":
                s.request_operation(request.user, branch, request.POST.dict())
            else:
                op = get_object_or_404(Operation.objects.filter(Q(branch=branch) | Q(destination=branch)), pk=request.POST.get("id"))
                action = request.POST.get("action")
                if action in ("resolve_arrived", "resolve_loss"):
                    from .transfers import resolve_transfer
                    resolve_transfer(request.user, op.pk, action.removeprefix("resolve_"), request.POST.get("note", ""))
                else:
                    if action == "receive" and not request.POST.get("received_quantity", "").strip():
                        raise ValidationError("Enter the number of sellable units actually received, including zero.")
                    s.advance_operation(request.user, op.pk, action, request.POST.get("received_quantity"), request.POST.get("note", ""))
            messages.success(request, "Stock operation recorded in isolated DEBUG verification.")
            return redirect("operations")
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))
    return render(request, "operations.html", {"title": "Stock operations", "products": Product.objects.filter(active=True),
        "destinations": Branch.objects.filter(active=True).exclude(pk=branch.pk),
        "rows": Operation.objects.filter(Q(branch=branch) | Q(destination=branch)).select_related(
            "product", "branch", "destination", "receipt", "receipt__recorded_by",
            "receipt__resolved_by", "receipt__loss_document"
        )[:100],
        "movements": Movement.objects.filter(branch=branch).select_related("product", "actor")[:100]})


@protected("operate_finance|manage_company")
def closings(request, branch):
    today = timezone.localdate()
    raw_day = request.POST.get("date") if request.method == "POST" else request.GET.get("date")
    try:
        selected_day = date.fromisoformat(raw_day) if raw_day else today
    except (TypeError, ValueError):
        selected_day = today

    if request.method == "POST":
        try:
            if request.POST.get("action") == "verify":
                s.verify_closing(request.user, get_object_or_404(Closing, pk=request.POST.get("id"), branch=branch))
                messages.success(request, "Daily closing independently verified.")
            else:
                owner_direct = request.user.is_superuser or request.user.has_perm("core.manage_company")
                closing = s.submit_closing(
                    request.user,
                    branch,
                    selected_day,
                    {method: request.POST.get(method, 0) for method, _ in Payment.METHODS},
                    request.POST.get("note", ""),
                    request.POST.get("opening_cash", 0),
                    request.POST.get("cash_in", 0),
                    request.POST.get("cash_out", 0),
                    owner_direct=owner_direct,
                )
                messages.success(
                    request,
                    "Daily closing submitted and finalized under owner authority."
                    if owner_direct else
                    "Daily closing submitted and sent to the Approval Center for verification."
                )
            return redirect(f"/closings/?date={selected_day.isoformat()}")
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))

    summary = s.closing_summary(branch, selected_day)
    history = list(Closing.objects.filter(branch=branch).select_related("submitted_by", "verified_by")[:100])
    for row in history:
        row.variance_view = {
            method: Decimal(str(row.counted.get(method, "0"))) - Decimal(str(row.expected.get(method, "0")))
            for method, _ in Payment.METHODS
        }
    return render(request, "closings.html", {
        "title": "Daily closing",
        "today": today.isoformat(),
        "selected_day": selected_day.isoformat(),
        "methods": Payment.METHODS,
        "summary": summary,
        "expected": summary["channel_net"].items(),
        "rows": history,
        "selected_closed": Closing.objects.filter(branch=branch, date=selected_day).exists(),
    })


def report_data(request, branch):
    from .reporting import FAMILIES, branch_comparison, build_report
    start = request.GET.get("start", timezone.localdate().replace(day=1).isoformat())
    end = request.GET.get("end", timezone.localdate().isoformat())
    family = request.GET.get("family", "register")
    query = request.GET.get("q", "").strip()[:100]
    category = request.GET.get("category", "").strip()[:40]
    method = request.GET.get("method", "").strip()[:12]
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last or family not in FAMILIES:
            raise ValueError
    except ValueError:
        raise ValidationError("Enter a valid date range and report family.")
    rows, columns = (
        branch_comparison(request.user, first, last)
        if family == "branches"
        else build_report(branch, first, last, family, query=query, category=category, method=method)
    )
    return rows, columns, {
        "start": start, "end": end, "family": family, "q": query,
        "category": category, "method": method,
    }, FAMILIES


@protected("view_reports|manage_company")
def reports(request, branch):
    from .accounting_views import EXPENSE_CATEGORIES
    from .approval_views import _approval_items
    from .business_intelligence import intelligence
    rows, columns, filters, families = report_data(request, branch)
    page = Paginator(rows, 100).get_page(request.GET.get("page"))
    page_query = request.GET.copy()
    page_query.pop("page", None)
    first, last = date.fromisoformat(filters["start"]), date.fromisoformat(filters["end"])
    intel = intelligence(branch, first, last, pending_approvals=len(_approval_items(request.user, branch)))
    return render(request, "reports.html", {
        "title": "Business Intelligence",
        "rows": [[row[key] for key, label in columns] for row in page],
        "page": page, "page_query": page_query.urlencode(),
        "headers": [label for key, label in columns], "filters": filters,
        "families": families.items(), "report_title": families[filters["family"]],
        "query": request.GET.urlencode(), "intelligence": intel,
        "expense_categories": EXPENSE_CATEGORIES, "payment_methods": Payment.METHODS,
    })


@protected("view_reports")
def export_report(request, branch, format):
    from .exports import export
    from .reporting import business_kpis
    rows, columns, filters, families = report_data(request, branch)
    first, last = date.fromisoformat(filters["start"]), date.fromisoformat(filters["end"])
    kpis = business_kpis(branch, first, last)
    s.audit(request.user, branch, "report.export", format, filters)
    scope = "Authorized branches" if filters["family"] == "branches" else branch.name
    return export(
        rows, format, f"{scope} · {families[filters['family']]}",
        Company.objects.first() or Company(), columns,
        filename=f"kofad-{filters['family']}-{filters['start']}-{filters['end']}",
        sheet_name=families[filters["family"]][:31],
        metadata={
            "Scope": scope, "From": first, "To": last,
            "Search": filters["q"] or "All", "Category": filters["category"] or "All",
            "Payment channel": filters["method"] or "All",
        },
        summary={
            "Net sales": kpis["net_sales"]["value"],
            "Gross profit": kpis["gross_profit"]["value"],
            "Expenses": kpis["expenses"]["value"],
            "Operating result": kpis["operating_result"]["value"],
        },
        notes=["Previous-period comparisons use the immediately preceding period of equal length."],
    )


@protected("view_reports|manage_company")
def audit_log(request, branch):
    query = request.GET.get("q", "").strip()[:100]
    category = request.GET.get("category", "").strip()[:32]
    severity = request.GET.get("severity", "").strip()[:12]
    actor = request.GET.get("actor", "").strip()
    start = request.GET.get("start", "")
    end = request.GET.get("end", "")
    rows = Audit.objects.filter(Q(branch=branch) | Q(branch__isnull=True, actor=request.user)).select_related("actor")
    if query:
        rows = rows.filter(Q(action__icontains=query) | Q(reference__icontains=query) | Q(entity_id__icontains=query))
    if category:
        rows = rows.filter(category=category)
    if severity:
        rows = rows.filter(severity=severity)
    if actor.isdigit():
        rows = rows.filter(actor_id=int(actor))
    try:
        if start:
            rows = rows.filter(created_at__date__gte=date.fromisoformat(start))
        if end:
            rows = rows.filter(created_at__date__lte=date.fromisoformat(end))
    except ValueError:
        messages.error(request, "Choose valid audit dates.")
    rows = list(rows[:500])
    chain_ok = True
    for row in rows:
        if not row.event_hash:
            row.integrity_ok = None
            continue
        digest_ok = s.audit_hash_for(row) == row.event_hash
        predecessor_ok = (
            not row.previous_hash
            or Audit.objects.filter(branch=row.branch, event_hash=row.previous_hash).exists()
        )
        row.integrity_ok = digest_ok and predecessor_ok
        if not row.integrity_ok:
            chain_ok = False
    categories = Audit.objects.filter(branch=branch).exclude(category="").values_list("category", flat=True).distinct()
    actors = User.objects.filter(audit__branch=branch).distinct().order_by("username")
    summary = {
        "events": len(rows),
        "high": sum(1 for row in rows if row.severity in {"high", "critical"}),
        "approvals": sum(1 for row in rows if "approved" in row.action or "verified" in row.action),
        "sealed": sum(1 for row in rows if row.event_hash),
    }
    return render(request, "audit.html", {
        "title": "Audit Intelligence", "rows": rows, "summary": summary,
        "categories": categories, "actors": actors, "selected_category": category,
        "selected_severity": severity, "selected_actor": actor, "q": query,
        "start": start, "end": end, "chain_ok": chain_ok,
    })


def _settings_form(request, branch, form_class, title, description, action):
    company = Company.objects.first() or Company.objects.create()
    form = form_class(request.POST or None, instance=company)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            before = {field: str(getattr(company, field)) for field in form.fields}
            obj = form.save()
            s.audit(request.user, branch, action, obj.pk, {
                "before": before,
                "after": {field: str(form.cleaned_data.get(field)) for field in form.fields},
            })
        messages.success(request, f"{title} saved.")
        return redirect(request.resolver_match.url_name)
    return render(request, "form.html", {
        "title": title, "form": form, "description": description, "settings_section": True,
    })


@protected("manage_company")
def settings_view(request, branch):
    return _settings_form(
        request, branch, CompanyForm, "Company profile",
        "Business identity used across the KOFAD workspace and printed documents. Currency remains GHS and the operating timezone is Africa/Accra.",
        "settings.company.updated",
    )


@protected("manage_company")
def location_settings(request, branch):
    form = LocationSettingsForm(request.POST or None, instance=branch)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            before = {"name": branch.name, "address": branch.address}
            obj = form.save()
            s.audit(request.user, branch, "settings.location.updated", obj.pk, {
                "before": before,
                "after": {"name": obj.name, "address": obj.address},
            })
        messages.success(request, "Location settings saved.")
        return redirect("location_settings")
    return render(request, "form.html", {
        "title": "Location settings",
        "form": form,
        "description": "Set the store/location name and address that appear on receipts, reports and location-scoped records.",
        "settings_section": True,
    })


@protected("manage_company")
def debt_settings(request, branch):
    item = DebtSettings.objects.first() or DebtSettings.objects.create()
    form = DebtSettingsForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            before = {field: str(getattr(item, field)) for field in form.fields}
            obj = form.save()
            s.audit(request.user, branch, "settings.debt.updated", obj.pk, {
                "before": before,
                "after": {field: str(form.cleaned_data.get(field)) for field in form.fields},
            })
        messages.success(request, "Debt settings saved.")
        return redirect("debt_settings")
    return render(request, "debt_settings.html", {
        "title": "Debt settings",
        "form": form,
        "item": item,
        "sms_enabled": settings.SMS_ENABLED,
        "sms_sandbox": settings.SMS_SANDBOX,
    })


@protected("manage_company")
def communication_settings(request, branch):
    item = CommunicationSettings.objects.first() or CommunicationSettings.objects.create()
    form = CommunicationSettingsForm(request.POST or None, instance=item, prefix="policy")
    contact_form = ManagementContactForm(request.POST or None, prefix="contact")
    action = request.POST.get("action") if request.method == "POST" else ""

    if request.method == "POST":
        try:
            if action == "save_policy" and form.is_valid():
                with transaction.atomic():
                    before = {field: str(getattr(item, field)) for field in form.fields}
                    obj = form.save()
                    s.audit(request.user, branch, "settings.communications.updated", obj.pk, {
                        "before": before,
                        "after": {field: str(form.cleaned_data.get(field)) for field in form.fields},
                    })
                messages.success(request, "Communication settings saved.")
                return redirect("communication_settings")
            if action == "add_contact" and contact_form.is_valid():
                with transaction.atomic():
                    contact = contact_form.save()
                    s.audit(request.user, branch, "settings.management_contact.added", contact.pk, {
                        "name": contact.name, "phone": contact.phone,
                    })
                messages.success(request, "Management notification contact added.")
                return redirect("communication_settings")
            if action == "delete_contact":
                contact = get_object_or_404(ManagementContact, pk=request.POST.get("id"))
                with transaction.atomic():
                    if Message.objects.filter(management_contact=contact).exists():
                        contact.active = False
                        contact.save(update_fields=["active"])
                        s.audit(request.user, branch, "settings.management_contact.deactivated", contact.pk)
                        messages.success(request, "Contact deactivated because notification history exists.")
                    else:
                        evidence = {"name": contact.name, "phone": contact.phone}
                        pk = contact.pk
                        contact.delete()
                        s.audit(request.user, branch, "settings.management_contact.deleted", pk, evidence)
                        messages.success(request, "Management notification contact removed.")
                return redirect("communication_settings")
            if action and action not in {"save_policy", "add_contact", "delete_contact"}:
                raise ValidationError("Unknown communication settings action.")
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))

    templates = {
        row.code: row for row in MessageTemplate.objects.filter(code__in=["receipt", "payment", "debt"])
    }
    return render(request, "communication_settings.html", {
        "title": "Communication settings",
        "form": form,
        "contact_form": contact_form,
        "item": item,
        "contacts": ManagementContact.objects.select_related("branch").all(),
        "templates": templates,
        "sms_enabled": settings.SMS_ENABLED,
        "sms_sandbox": settings.SMS_SANDBOX,
        "sms_provider": settings.SMS_PROVIDER,
    })


@protected("manage_company")
def sales_policy_settings(request, branch):
    return _settings_form(
        request, branch, SalesPolicyForm, "Sales & credit policies",
        "Set staff limits, manager authority thresholds and credit rules. These controls are enforced again on the server when a transaction posts.",
        "settings.sales_policy.updated",
    )


@protected("manage_company")
def payment_policy_settings(request, branch):
    return _settings_form(
        request, branch, PaymentPolicyForm, "Payment methods",
        "Choose which channels may be used for new transactions. Historical payments and audit records are never rewritten when a channel is disabled.",
        "settings.payment_policy.updated",
    )


@protected("manage_company")
def finance_policy_settings(request, branch):
    return _settings_form(
        request, branch, FinancePolicyForm, "Finance controls",
        "Set the expense authority threshold and daily-closing variance tolerance. Zero disables the expense threshold.",
        "settings.finance_policy.updated",
    )


@protected("manage_company")
def receipt_policy_settings(request, branch):
    return _settings_form(
        request, branch, ReceiptPolicyForm, "Receipts & numbering",
        "Configure new transaction references and what appears on printed receipts. Existing references and posted transaction evidence never change.",
        "settings.receipt_policy.updated",
    )


@protected("operate_sales|send_messages")
def communications(request, branch):
    from urllib.parse import quote
    from django.conf import settings
    from .sms.service import create_draft, create_direct_draft, normalize_phone, send_message_now, send_messages_now

    redirect_suffix = ""
    if request.method == "POST":
        try:
            action = request.POST.get("action", "send_compose")
            if action in ("send", "queue", "retry"):
                item = get_object_or_404(Message, pk=request.POST.get("id"), branch=branch)
                sent = send_message_now(request.user, branch, item.pk, action == "retry")
                if sent.status == "accepted":
                    messages.success(request, "SMS sent to Arkesel. Delivery confirmation is being tracked.")
                elif sent.status == "delivered":
                    messages.success(request, "SMS delivered.")
                elif sent.status == "simulated":
                    messages.success(request, "SMS sandbox submission completed.")
                else:
                    messages.error(request, sent.last_error or f"SMS {sent.status}.")
            elif action == "document":
                from .sms.templates import render_for_document
                doc = get_object_or_404(Document, pk=request.POST.get("document"), branch=branch)
                code = request.POST.get("template", "receipt")
                body = render_for_document(doc, code)
                create_draft(
                    request.user, branch, doc.party, body,
                    source_key=f"{code}:{doc.pk}:{s.balance(doc) if code == 'debt' else 'once'}:{timezone.localdate()}"
                )
                messages.success(request, "Transaction message prepared.")
            elif action == "send_compose":
                channel = request.POST.get("channel", "sms").strip().lower()
                target = request.POST.get("target", "one").strip().lower()
                body = request.POST.get("body", "").strip()
                key = uuid.UUID(request.POST.get("key", ""))
                if channel not in ("sms", "whatsapp"):
                    raise ValidationError("Choose SMS or WhatsApp.")
                if target not in ("one", "selected", "all", "manual"):
                    raise ValidationError("Choose who should receive the message.")
                if not body:
                    raise ValidationError("Type a message first.")
                if channel == "sms" and len(body) > 480:
                    raise ValidationError("Keep SMS messages at 480 characters or fewer.")
                if channel == "whatsapp" and len(body) > 1000:
                    raise ValidationError("Keep WhatsApp messages at 1,000 characters or fewer.")

                customers = Party.objects.filter(
                    branch=branch, kind="customer"
                ).exclude(phone="").order_by("name", "pk")

                selected = []
                manual_phone = ""
                if target == "one":
                    party_id = request.POST.get("party", "").strip()
                    if not party_id:
                        raise ValidationError("Choose a customer.")
                    selected = [get_object_or_404(customers, pk=party_id)]
                elif target == "selected":
                    ids = []
                    for value in request.POST.getlist("customer_ids"):
                        try:
                            ids.append(int(value))
                        except (TypeError, ValueError):
                            raise ValidationError("One selected customer is invalid.")
                    if not ids:
                        raise ValidationError("Select at least one customer.")
                    selected = list(customers.filter(pk__in=ids)[:201])
                elif target == "all":
                    if request.POST.get("confirm_all") != "yes":
                        raise ValidationError("Confirm the all-customers send first.")
                    selected = list(customers[:201])
                    if customers.count() > 200:
                        raise ValidationError("This location has more than 200 customer numbers. Use selected customers in smaller groups.")
                else:
                    manual_phone = request.POST.get("phone", "").strip()
                    if not manual_phone:
                        raise ValidationError("Enter a phone number.")

                recipients = []
                seen = set()
                if target == "manual":
                    normalized = normalize_phone(manual_phone)
                    recipients.append((None, normalized, "Manual number"))
                else:
                    for party in selected:
                        try:
                            normalized = normalize_phone(party.phone)
                        except ValidationError:
                            continue
                        if normalized in seen:
                            continue
                        seen.add(normalized)
                        recipients.append((party, normalized, party.name))

                if not recipients:
                    raise ValidationError("No valid Ghana phone numbers were found.")

                prepared = []
                for index, (party, phone, label) in enumerate(recipients):
                    item = create_direct_draft(
                        request.user,
                        branch,
                        body,
                        channel=channel,
                        source_key=f"manual:{key}:{index}",
                        party=party,
                        phone=phone,
                        label=label,
                    )
                    if channel == "whatsapp":
                        item.provider = "whatsapp-link"
                        item.status = "ready"
                        item.save(update_fields=["provider", "status"])
                    prepared.append(item)

                if channel == "sms":
                    sent = send_messages_now(
                        request.user,
                        branch,
                        [item.pk for item in prepared],
                    )
                    accepted = sum(1 for item in sent if item.status in {"accepted", "delivered", "simulated"})
                    failed = sum(1 for item in sent if item.status in {"failed", "undelivered", "expired"})
                    unknown = sum(1 for item in sent if item.status == "unknown")
                    if accepted:
                        messages.success(
                            request,
                            f"{accepted} SMS message{'' if accepted == 1 else 's'} sent to Arkesel. Delivery status will update automatically."
                        )
                    if failed or unknown:
                        first_problem = next((item.last_error for item in sent if item.last_error), "")
                        messages.error(
                            request,
                            f"{failed + unknown} SMS message{'' if failed + unknown == 1 else 's'} not confirmed. "
                            + (first_problem or "Check Recent messages for the provider result.")
                        )
                else:
                    messages.success(
                        request,
                        f"{len(prepared)} WhatsApp chat{'' if len(prepared) == 1 else 's'} prepared."
                    )
                    if len(prepared) == 1:
                        redirect_suffix = f"?wa={prepared[0].pk}"
                    else:
                        redirect_suffix = f"?wa_batch={key}"
            else:
                raise ValidationError("Unknown message action.")
            return redirect("/communications/" + redirect_suffix)
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))

    communication_policy = CommunicationSettings.objects.first() or CommunicationSettings.objects.create()
    debt_policy = DebtSettings.objects.first() or DebtSettings.objects.create()
    rows = list(
        Message.objects.filter(branch=branch).select_related(
            "party", "management_contact", "created_by", "submitted_by"
        ).order_by("-created_at")[:120]
    )
    status_labels = {
        "draft": "Ready to send",
        "sending": "Sending…",
        "accepted": "Sent",
        "delivered": "Delivered",
        "undelivered": "Not delivered",
        "expired": "Expired",
        "failed": "Failed",
        "unknown": "Delivery unknown",
        "simulated": "Test sent",
        "ready": "Ready to open",
    }
    for row in rows:
        row.whatsapp_url = ""
        row.status_label = status_labels.get(row.status, row.status.replace("_", " ").title())
        if row.channel == "whatsapp" and row.recipient:
            digits = "".join(ch for ch in row.recipient if ch.isdigit())
            row.whatsapp_url = f"https://wa.me/{digits}?text={quote(row.body)}"

    whatsapp_launch = None
    whatsapp_batch = []
    wa_id = request.GET.get("wa", "")
    if wa_id.isdigit():
        whatsapp_launch = next(
            (row for row in rows if row.pk == int(wa_id) and row.channel == "whatsapp"),
            None,
        )
    wa_batch = request.GET.get("wa_batch", "").strip()
    if wa_batch:
        prefix = f"manual:{wa_batch}:"
        whatsapp_batch = [
            row for row in rows
            if row.channel == "whatsapp" and (row.source_key or "").startswith(prefix)
        ]

    parties = list(
        Party.objects.filter(branch=branch, kind="customer")
        .exclude(phone="")
        .order_by("name", "pk")
    )
    return render(request, "communications.html", {
        "title": "Communications",
        "key": str(uuid.uuid4()),
        "parties": parties,
        "customer_count": len(parties),
        "rows": rows,
        "whatsapp_launch": whatsapp_launch,
        "whatsapp_batch": whatsapp_batch,
        "communication_policy": communication_policy,
        "debt_policy": debt_policy,
        "sms_enabled": settings.SMS_ENABLED,
        "sms_sandbox": settings.SMS_SANDBOX,
        "sms_provider": settings.SMS_PROVIDER,
    })


@protected("operate_sales|send_messages")
def communication_status(request, branch):
    raw_ids = [value for value in request.GET.get("ids", "").split(",") if value.strip().isdigit()][:80]
    ids = [int(value) for value in raw_ids]
    if not ids:
        return JsonResponse({"messages": []})
    rows = list(
        Message.objects.filter(branch=branch, pk__in=ids, channel="sms")
        .prefetch_related("delivery_attempts")
    )
    payload = []
    for row in rows:
        latest = max(row.delivery_attempts.all(), key=lambda attempt: attempt.number, default=None)
        payload.append({
            "id": row.pk,
            "status": row.status,
            "last_error": row.last_error,
            "provider_id": latest.provider_id if latest else "",
            "updated_at": latest.updated_at.isoformat() if latest else row.created_at.isoformat(),
        })
    return JsonResponse({"messages": payload})


@protected("operate_finance|manage_company")
def corrections(request, branch):
    if request.method == "POST":
        try:
            if request.POST.get("action") == "request":
                item = s.request_correction(request.user,branch,request.POST.get("original"),request.POST.get("reason",""),request.POST.get("refund_method","cash"))
                if request.user.is_superuser or request.user.has_perm("core.manage_company"):
                    s.review_correction(request.user, branch, item.pk, True, owner_direct=True)
            else:
                item = get_object_or_404(Correction,pk=request.POST.get("id"),original__branch=branch)
                action = request.POST.get("action")
                if action not in ("approve","reject"):
                    raise ValidationError("Invalid review action.")
                s.review_correction(
                    request.user, branch, item.pk, action=="approve",
                    owner_direct=request.user.is_superuser or request.user.has_perm("core.manage_company"),
                )
            messages.success(request,"Correction request recorded.")
            return redirect("corrections")
        except (ValidationError,ValueError) as exc:
            messages.error(request,problem(exc))
    return render(request,"corrections.html",{"title":"Corrections", "methods":s.active_payment_methods(),
        "documents":Document.objects.filter(branch=branch,kind__in=["sale","expense","collection","supplier_payment","creditor_charge"],correction__isnull=True)[:200],
        "rows":Correction.objects.filter(original__branch=branch).select_related("original","requested_by","reviewed_by","posted")[:100]})


@login_required
def search(request):
    branch = branch_for(request)
    q = request.GET.get("q","").strip()[:100]
    products,parties,docs = [],[],[]
    if q:
        if request.user.has_perm("core.operate_sales") or request.user.has_perm("core.operate_inventory") or request.user.has_perm("core.view_reports"):
            products = Product.objects.filter(Q(name__icontains=q)|Q(sku__icontains=q)|Q(barcode=q))[:20]
            kinds = []
            if request.user.has_perm("core.operate_sales") or request.user.has_perm("core.view_reports"):
                kinds.append("customer")
            if request.user.has_perm("core.operate_inventory") or request.user.has_perm("core.view_reports"):
                kinds.append("supplier")
            parties = Party.objects.filter(branch=branch,kind__in=kinds).filter(Q(name__icontains=q)|Q(phone__icontains=q))[:20]
        allowed = []
        if request.user.has_perm("core.operate_sales"):
            allowed += ["sale","return"]
        if request.user.has_perm("core.operate_inventory"):
            allowed += ["purchase","supplier_return","inventory_writeoff"]
        if request.user.has_perm("core.operate_finance"):
            allowed += ["expense","collection","supplier_payment","creditor_charge","supplier_return","inventory_writeoff","reversal"]
        if request.user.has_perm("core.view_reports"):
            allowed = list(dict(Document.KINDS))
        docs = Document.objects.filter(branch=branch,kind__in=allowed).filter(Q(reference__icontains=q)|Q(external_reference__icontains=q)|Q(party__name__icontains=q))[:20]
    return render(request,"search.html",{"title":"Search workspace","q":q,"products":products,"parties":parties,"documents":docs})


@login_required
@sensitive_post_parameters("old_password", "new_password1", "new_password2")
def password_change(request):
    from django.contrib.auth.forms import PasswordChangeForm
    from django.contrib.auth import update_session_auth_hash
    form = PasswordChangeForm(request.user)
    if request.method == "POST":
        with transaction.atomic():
            current = User.objects.select_for_update().get(pk=request.user.pk)
            form = PasswordChangeForm(current, request.POST)
            if form.is_valid():
                user = form.save()
                Access.objects.filter(user=user).update(must_change_password=False, force_password_change=False)
                update_session_auth_hash(request, user)
                user.access.refresh_from_db()
                request.session["access_version"] = user.access.session_version
                s.audit(user, None, "password.changed", user.pk)
                messages.success(request, "Your password has been changed.")
                return redirect("account")
    return render(request, "password_change.html", {"form":form, "title":"Change password"})


@protected("manage_company")
def message_templates(request,branch):
    from .models import MessageTemplate
    from .forms import MessageTemplateForm
    code = request.GET.get("code","receipt")
    item = get_object_or_404(MessageTemplate,code=code)
    form = MessageTemplateForm(request.POST or None,instance=item)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            before = MessageTemplate.objects.get(pk=item.pk).body
            obj = form.save()
            s.audit(request.user,branch,"message_template.updated",obj.code,{"before":before,"after":obj.body})
        messages.success(request,"Template updated.")
        return redirect("/message-templates/?code="+code)
    return render(request,"message_templates.html",{"title":"Message templates","form":form,
        "templates":MessageTemplate.objects.all(),"code":code})
