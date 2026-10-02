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
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import F, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services as s
from .context import shell
from .forms import CompanyForm, PartyForm, ProductForm
from .models import (
    Access, Audit, Branch, Closing, Company, Correction, Document, HeldSale, Line, LoginAttempt,
    Message, Movement, Operation, Party, Payment, Product, Stock,
)
from .security import matching_step, new_secret


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


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "")[:150]
        # Per-account lock avoids trusting spoofable forwarded IP headers.
        key = hashlib.sha256(username.casefold().encode()).hexdigest()
        with transaction.atomic():
            LoginAttempt.objects.get_or_create(key=key)
            attempt = LoginAttempt.objects.select_for_update().get(key=key)
            if attempt.blocked_until and attempt.blocked_until > timezone.now():
                error = "Too many attempts. Please wait 15 minutes."
            else:
                if attempt.blocked_until:
                    attempt.failures = 0
                    attempt.blocked_until = None
                user = authenticate(request, username=username, password=request.POST.get("password", ""))
                if user is not None:
                    access, _ = Access.objects.get_or_create(user=user)
                    if access.must_change_password and settings.KOFAD_SETUP_KEY:
                        supplied = request.POST.get("setup_key", "")
                        if not secrets.compare_digest(supplied.encode(), settings.KOFAD_SETUP_KEY.encode()):
                            user = None
                if user is not None:
                    login(request, user)
                    if access.must_change_password and settings.KOFAD_SETUP_KEY:
                        request.session["setup_key_digest"] = hashlib.sha256(settings.KOFAD_SETUP_KEY.encode()).hexdigest()
                    request.session["access_version"] = access.session_version
                    request.session["mfa_ok"] = not bool(access.totp_secret) and not (user.is_staff or user.is_superuser or user.has_perm("core.manage_company"))
                    attempt.failures = 0
                    attempt.save()
                    s.audit(user, None, "session.login", user.pk)
                    return redirect("mfa" if not request.session["mfa_ok"] else "dashboard")
                attempt.failures += 1
                if attempt.failures >= 5:
                    attempt.blocked_until = timezone.now() + timedelta(minutes=15)
                attempt.save()
                error = "The username or password is incorrect."
    return render(request, "login.html", {"error": error, "setup_required": bool(settings.KOFAD_SETUP_KEY) and Access.objects.filter(must_change_password=True).exists()})


@login_required
def mfa(request):
    access = request.user.access
    enrolled = bool(access.totp_secret)
    secret = access.totp_secret or request.session.get("enroll_secret") or new_secret()
    if not enrolled:
        request.session["enroll_secret"] = secret
    error = ""
    if request.method == "POST":
        with transaction.atomic():
            access = Access.objects.select_for_update().get(user=request.user)
            attempt_key = hashlib.sha256(f"mfa:{request.user.pk}".encode()).hexdigest()
            LoginAttempt.objects.get_or_create(key=attempt_key)
            attempt = LoginAttempt.objects.select_for_update().get(key=attempt_key)
            if attempt.blocked_until and attempt.blocked_until > timezone.now():
                return render(request, "mfa.html", {"enrolled": enrolled, "error": "Too many verification attempts. Wait 15 minutes."})
            if attempt.blocked_until:
                attempt.failures = 0
                attempt.blocked_until = None
            step = matching_step(secret, request.POST.get("code", ""))
            if step is not None and step > access.totp_last_step:
                access.totp_secret = secret
                access.totp_last_step = step
                access.save(update_fields=["totp_secret", "totp_last_step"])
                attempt.failures = 0
                attempt.save()
                request.session["mfa_ok"] = True
                request.session.pop("enroll_secret", None)
                request.session.pop("mfa_failures", None)
                s.audit(request.user, None, "session.mfa", request.user.pk)
                return redirect("dashboard")
            attempt.failures += 1
            if attempt.failures >= 5:
                attempt.blocked_until = timezone.now() + timedelta(minutes=15)
            attempt.save()
            error = "The code is invalid or already used. Wait for the next code."
    return render(request, "mfa.html", {"enrolled": enrolled, "secret": secret if not enrolled else "", "error": error})


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
    return render(request, "dashboard.html", {"title": "Command centre", "revenue": revenue, "expenses": expenses,
        "debt": debt, "low": low[:6], "low_count": low.count(), "recent": docs[:7], "week": week,
        "pending": Operation.objects.filter(branch=branch, status="requested").count(), "today": today,
        "channels": s.channel_totals(branch, today).items(), "sales_count": sales.count()})


@protected("operate_sales")
def pos(request, branch):
    return trade_screen(request, branch, "sale")


@protected("operate_inventory")
def purchasing(request, branch):
    return trade_screen(request, branch, "purchase")


def trade_screen(request, branch, kind):
    catalog = []
    stock = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
    query = request.GET.get("q", "")[:100]
    products = Product.objects.filter(active=True)
    if query:
        products = products.filter(Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcode=query))
    for p in products[:300]:
        item = {"id": p.pk, "name": p.name, "sku": p.sku, "barcode": p.barcode, "category": p.category,
                "pack_size": p.pack_size, "pack_name": p.pack_name, "base_unit": p.base_unit,
                "stock": stock.get(p.pk, 0), "prices": {k: str(getattr(p, k)) for k in
                    ("retail_unit", "retail_pack", "wholesale_unit", "wholesale_pack") if getattr(p, k) is not None}}
        if kind == "purchase":
            item["cost"] = str(p.cost)
        catalog.append(item)
    if request.GET.get("format") == "json":
        return JsonResponse({"catalog":catalog})
    return render(request, "pos.html", {"title": "New sale" if kind == "sale" else "Receive purchase",
        "catalog": catalog, "kind": kind, "key": str(uuid.uuid4()), "q": query,
        "parties": Party.objects.filter(branch=branch, kind="customer" if kind == "sale" else "supplier"),
        "held": HeldSale.objects.filter(branch=branch, user=request.user),
        "purchase": kind == "purchase"})


@login_required
@require_POST
def complete_trade(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValidationError("Expected a transaction object.")
        doc = s.post_trade(request.user, branch_for(request), data, request.headers.get("Idempotency-Key"),
                           kind=data.get("kind", "sale"))
        return JsonResponse({"url": f"/documents/{doc.pk}/", "reference": doc.reference})
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
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
        allowed += ["purchase"]
    if request.user.has_perm("core.operate_finance"):
        allowed += ["expense", "collection", "supplier_payment"]
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
    doc = get_object_or_404(Document.objects.select_related("party", "created_by", "branch", "original"), pk=pk, branch=branch)
    permission = "operate_sales" if doc.kind in ("sale", "return") else "operate_inventory" if doc.kind == "purchase" else "operate_finance"
    if not request.user.has_perm("core.view_reports"):
        s.permit(request.user, branch, permission)
    return render(request, "document.html", {"title": doc.reference, "doc": doc,
        "outstanding": s.balance(doc) if doc.kind in ("sale", "purchase") else None})


@protected("operate_inventory|view_reports")
def inventory(request, branch):
    q = request.GET.get("q", "")[:100]
    products = Product.objects.all()
    if q:
        products = products.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode=q))
    stocks = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
    rows = []
    for p in products[:200]:
        quantity = stocks.get(p.pk, 0)
        rows.append({"product": p, "quantity": quantity, "packs": quantity // p.pack_size,
                     "loose": quantity % p.pack_size, "low": quantity <= p.reorder_level})
    return render(request, "inventory.html", {"title": "Inventory", "rows": rows, "q": q})


@protected("change_product")
def product_edit(request, branch, pk=None):
    obj = get_object_or_404(Product, pk=pk) if pk else None
    if not obj and not request.user.has_perm("core.add_product"):
        raise PermissionDenied
    form = ProductForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            before = {k: str(v) for k, v in (Product.objects.filter(pk=pk).values().first() or {}).items()}
            product = form.save()
            s.audit(request.user, branch, "product.saved", product.sku,
                    {"before": before, "after": {k: str(v) for k, v in form.cleaned_data.items()}})
        messages.success(request, "Product saved. Use a stock operation to record opening stock.")
        return redirect("inventory")
    return render(request, "form.html", {"title": "Edit product" if pk else "New product", "form": form,
        "description": "Leave a price blank to disable that selling mode. Quantities are always held in base units."})


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
    docs = Document.objects.filter(party=party).order_by("created_at")
    running = Decimal(0)
    rows = []
    for doc in docs:
        change = doc.balance if doc.kind in ("sale", "purchase") else -sum((a.amount for a in doc.allocations.all()), Decimal(0))
        if doc.kind == "reversal" and doc.original_id and doc.original.kind in ("collection", "supplier_payment"):
            change = sum((a.amount for a in doc.original.allocations.all()), Decimal(0))
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
    invoices = Document.objects.filter(branch=branch, kind__in=["sale", "purchase"], party__isnull=False)
    outstanding = [{"doc": d, "balance": s.balance(d)} for d in invoices]
    return render(request, "finance.html", {"title": "Finance", "key": request.POST.get("key") or str(uuid.uuid4()),
        "outstanding": [r for r in outstanding if r["balance"] > 0], "methods": Payment.METHODS,
        "recent": Document.objects.filter(branch=branch, kind__in=["expense", "collection", "supplier_payment"])[:20]})


@protected("approve_operations")
def returns(request, branch):
    if request.method == "POST":
        try:
            doc = s.post_return(request.user, branch, request.POST.dict(), request.POST.get("key"))
            return redirect("document", pk=doc.pk)
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))
    ref = request.GET.get("q", "")
    lines = Line.objects.filter(document__branch=branch, document__kind="sale", document__reference=ref)
    return render(request, "returns.html", {"title": "Returns", "lines": lines, "q": ref,
        "key": request.POST.get("key") or str(uuid.uuid4()), "methods": Payment.METHODS})


@protected("operate_inventory|approve_operations")
def operations(request, branch):
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
            messages.success(request, "Stock operation recorded.")
            return redirect("operations")
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))
    return render(request, "operations.html", {"title": "Stock operations", "products": Product.objects.filter(active=True),
        "destinations": Branch.objects.filter(active=True).exclude(pk=branch.pk),
        "rows": Operation.objects.filter(Q(branch=branch) | Q(destination=branch)).select_related("product", "branch", "destination", "receipt", "receipt__recorded_by", "receipt__resolved_by")[:100],
        "movements": Movement.objects.filter(branch=branch).select_related("product", "actor")[:100]})


@protected("operate_finance")
def closings(request, branch):
    if request.method == "POST":
        try:
            if request.POST.get("action") == "verify":
                s.verify_closing(request.user, get_object_or_404(Closing, pk=request.POST.get("id"), branch=branch))
            else:
                s.submit_closing(request.user, branch, date.fromisoformat(request.POST.get("date", "")),
                    {m: request.POST.get(m, 0) for m, _ in Payment.METHODS}, request.POST.get("note", ""))
            messages.success(request, "Closing recorded.")
            return redirect("closings")
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))
    return render(request, "closings.html", {"title": "Daily closing", "today": timezone.localdate().isoformat(),
        "methods": Payment.METHODS, "expected": s.channel_totals(branch, timezone.localdate()).items(),
        "rows": Closing.objects.filter(branch=branch).select_related("submitted_by", "verified_by")[:100]})


def report_data(request, branch):
    from .reporting import build_report, branch_comparison, FAMILIES
    start = request.GET.get("start", timezone.localdate().replace(day=1).isoformat())
    end = request.GET.get("end", timezone.localdate().isoformat())
    family = request.GET.get("family","register")
    try:
        first,last = date.fromisoformat(start),date.fromisoformat(end)
        if first > last or family not in FAMILIES:
            raise ValueError
    except ValueError:
        raise ValidationError("Enter a valid date range and report family.")
    rows,columns = branch_comparison(request.user,first,last) if family == 'branches' else build_report(branch,first,last,family)
    return rows,columns,{"start":start,"end":end,"family":family},FAMILIES


@protected("view_reports")
def reports(request, branch):
    rows,columns,filters,families = report_data(request,branch)
    page = Paginator(rows, 100).get_page(request.GET.get("page"))
    page_query = request.GET.copy()
    page_query.pop("page", None)
    return render(request,"reports.html",{"title":"Reports","rows":[[row[key] for key,label in columns] for row in page],
        "page":page, "page_query":page_query.urlencode(),
        "headers":[label for key,label in columns],"filters":filters,"families":families.items(),
        "report_title":families[filters["family"]],"query":request.GET.urlencode()})


@protected("view_reports")
def export_report(request, branch, format):
    from .exports import export
    rows,columns,filters,families = report_data(request,branch)
    s.audit(request.user,branch,"report.export",format,filters)
    scope = "Authorized branches" if filters["family"] == "branches" else branch.name
    return export(rows,format,f"{scope} / {families[filters['family']]}",Company.objects.first() or Company(),columns)


@protected("view_reports")
def audit_log(request, branch):
    return render(request, "audit.html", {"title": "Audit trail",
        "rows": Audit.objects.filter(Q(branch=branch) | Q(branch__isnull=True, actor=request.user)).select_related("actor")[:300]})


@protected("manage_company")
def settings_view(request, branch):
    company = Company.objects.first()
    form = CompanyForm(request.POST or None, instance=company)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            old = {k: str(v) for k, v in (Company.objects.filter(pk=company.pk).values().first() if company else {}).items()}
            obj = form.save()
            s.audit(request.user, branch, "company.updated", obj.pk,
                    {"before": old, "after": {k: str(v) for k, v in form.cleaned_data.items()}})
        messages.success(request, "Company settings saved.")
        return redirect("settings")
    return render(request, "form.html", {"title": "Company settings", "form": form,
        "description": "Currency is GHS and the operating timezone is Africa/Accra. Review tax requirements before launch."})


@protected("operate_sales|send_messages")
def communications(request, branch):
    from .sms.service import create_draft,queue_message
    from django.conf import settings
    if request.method == "POST":
        try:
            action = request.POST.get("action","draft")
            if action in ("queue","retry"):
                item = get_object_or_404(Message,pk=request.POST.get("id"),branch=branch)
                queue_message(request.user,branch,item.pk,action=="retry")
                messages.success(request,"SMS queued. The worker will submit it to the selected provider.")
            elif action == "document":
                from .sms.templates import render_for_document
                doc = get_object_or_404(Document,pk=request.POST.get("document"),branch=branch)
                code = request.POST.get("template","receipt")
                body = render_for_document(doc,code)
                create_draft(request.user,branch,doc.party,body,source_key=f"{code}:{doc.pk}:{s.balance(doc) if code == 'debt' else 'once'}:{timezone.localdate()}")
                messages.success(request,"Transaction message prepared. Review it before queueing.")
            elif action == "draft":
                party = get_object_or_404(Party,pk=request.POST.get("party"),branch=branch)
                key = uuid.UUID(request.POST.get("key",""))
                create_draft(request.user,branch,party,request.POST.get("body",""),request.POST.get("channel","sms"),f"manual:{key}")
                messages.success(request,"Draft prepared. Review the recipient, content and estimated segments below.")
            else:
                raise ValidationError("Unknown message action.")
            return redirect("communications")
        except (ValidationError,ValueError) as exc:
            messages.error(request,problem(exc))
    return render(request,"communications.html",{"title":"Communications","key":str(uuid.uuid4()),
        "parties":Party.objects.filter(branch=branch,consent=True),
        "rows":Message.objects.filter(branch=branch).select_related("party","created_by").order_by("-created_at")[:100],
        "sms_enabled":settings.SMS_ENABLED,"sms_sandbox":settings.SMS_SANDBOX,"sms_provider":settings.SMS_PROVIDER})


@protected("operate_finance")
def corrections(request, branch):
    if request.method == "POST":
        try:
            if request.POST.get("action") == "request":
                s.request_correction(request.user,branch,request.POST.get("original"),request.POST.get("reason",""),request.POST.get("refund_method","cash"))
            else:
                item = get_object_or_404(Correction,pk=request.POST.get("id"),original__branch=branch)
                action = request.POST.get("action")
                if action not in ("approve","reject"):
                    raise ValidationError("Invalid review action.")
                s.review_correction(request.user,branch,item.pk,action=="approve")
            messages.success(request,"Correction request recorded.")
            return redirect("corrections")
        except (ValidationError,ValueError) as exc:
            messages.error(request,problem(exc))
    return render(request,"corrections.html",{"title":"Corrections", "methods":Payment.METHODS,
        "documents":Document.objects.filter(branch=branch,kind__in=["sale","expense","collection","supplier_payment"],correction__isnull=True)[:200],
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
            allowed += ["purchase"]
        if request.user.has_perm("core.operate_finance"):
            allowed += ["expense","collection","supplier_payment","reversal"]
        if request.user.has_perm("core.view_reports"):
            allowed = list(dict(Document.KINDS))
        docs = Document.objects.filter(branch=branch,kind__in=allowed,reference__icontains=q)[:20]
    return render(request,"search.html",{"title":"Search workspace","q":q,"products":products,"parties":parties,"documents":docs})


@login_required
def password_change(request):
    from django.contrib.auth.forms import PasswordChangeForm
    from django.contrib.auth import update_session_auth_hash
    form = PasswordChangeForm(request.user,request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            Access.objects.filter(user=user).update(must_change_password=False)
            update_session_auth_hash(request,user)
            user.access.refresh_from_db()
            request.session["access_version"] = user.access.session_version
            s.audit(user,None,"password.changed",user.pk)
        return redirect("mfa" if not request.session.get("mfa_ok") else "dashboard")
    return render(request,"password_change.html",{"form":form})


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
