import json
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.identity import normalize_ghana_phone
from core.models import Product
from core.views import problem, protected

from .forms import (
    CheckoutForm, CustomerLoginForm, CustomerPasswordResetForm, CustomerRegistrationForm,
    DeliveryZoneForm, PublicMessageForm, StaffOrderUpdateForm,
)
from .models import (
    Conversation, ConversationMessage, CustomerAccount, DeliveryZone, MarketListing,
    MarketPaymentAttempt, OnlineOrder,
)
from . import services


def _market_context(request, **extra):
    customer = services.customer_from_session(request)
    cart = request.session.get("market_cart", {})
    context = {
        "market_customer": customer,
        "market_cart_count": sum(int(value) for value in cart.values() if str(value).isdigit()),
        "company": getattr(request, "company", None),
        **extra,
    }
    return context


def market_customer_required(view):
    @wraps(view)
    def inner(request, *args, **kwargs):
        customer = services.customer_from_session(request)
        if not customer:
            request.session["market_after_login"] = request.get_full_path()
            messages.info(request, "Sign in with your verified phone number to continue.")
            return redirect("market_login")
        return view(request, customer, *args, **kwargs)
    return inner


def home(request):
    listings = list(
        MarketListing.objects.filter(enabled=True, product__active=True)
        .select_related("product").order_by("-featured", "sort_order", "product__name")[:6]
    )
    branch = None
    try:
        branch = services.market_branch()
    except ValidationError:
        pass
    for listing in listings:
        listing.available_units = services.available_units(branch, listing.product) if branch else 0
        listing.market_price_value = listing.market_price

    enquiry_form = PublicMessageForm(request.POST or None)
    if request.method == "POST" and enquiry_form.is_valid():
        data = enquiry_form.cleaned_data
        customer = CustomerAccount.objects.filter(phone=data["phone"], active=True).first()
        conversation = Conversation.objects.create(
            customer=customer,
            public_name=data["name"],
            public_phone=data["phone"],
            subject=data["subject"],
        )
        ConversationMessage.objects.create(
            conversation=conversation,
            sender_type="customer" if customer else "visitor",
            body=data["message"].strip(),
            read_by_customer=True,
        )
        messages.success(request, "Your message has reached KOFAD. A staff member can now respond from the customer inbox.")
        return redirect("public_home")
    return render(request, "marketplace/home.html", _market_context(
        request,
        title="KOFAD Market & Operations",
        listings=listings,
        enquiry_form=enquiry_form,
    ))


def market(request):
    query = request.GET.get("q", "").strip()[:100]
    category = request.GET.get("category", "").strip()[:80]
    rows = MarketListing.objects.filter(enabled=True, product__active=True).select_related("product")
    if query:
        from django.db.models import Q
        rows = rows.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
            | Q(product__name__icontains=query) | Q(product__sku__icontains=query)
            | Q(product__category__icontains=query)
        )
    if category:
        rows = rows.filter(product__category=category)
    branch = None
    try:
        branch = services.market_branch()
    except ValidationError:
        pass
    listings = list(rows.order_by("-featured", "sort_order", "product__name")[:120])
    for listing in listings:
        listing.available_units = services.available_units(branch, listing.product) if branch else 0
        listing.available_sell_qty = listing.available_units // max(listing.factor, 1)
        listing.market_price_value = listing.market_price
    categories = (
        Product.objects.filter(market_listing__enabled=True, active=True)
        .exclude(category="").values_list("category", flat=True).distinct().order_by("category")
    )
    return render(request, "marketplace/market.html", _market_context(
        request, title="KOFAD Market", listings=listings, q=query,
        selected_category=category, categories=categories,
    ))


def product_detail(request, pk):
    listing = get_object_or_404(
        MarketListing.objects.select_related("product"),
        pk=pk, enabled=True, product__active=True,
    )
    branch = services.market_branch()
    listing.available_units = services.available_units(branch, listing.product)
    listing.available_sell_qty = listing.available_units // max(listing.factor, 1)
    listing.market_price_value = listing.market_price
    return render(request, "marketplace/product.html", _market_context(
        request, title=listing.display_name, listing=listing,
    ))


def product_image(request, pk, size="large"):
    listing = get_object_or_404(MarketListing, pk=pk)
    if not listing.enabled and not (
        request.user.is_authenticated and request.user.has_perm("core.change_product")
    ):
        raise Http404
    data = listing.image_thumb if size == "thumb" else listing.image_data
    if not data:
        raise Http404
    response = HttpResponse(bytes(data), content_type=listing.image_mime or "image/webp")
    response["Cache-Control"] = "public, max-age=86400"
    response["Content-Disposition"] = "inline"
    return response


def account_start(request):
    customer = services.customer_from_session(request)
    if customer:
        return redirect("market")
    phone = request.POST.get("phone", "").strip()
    if request.method == "POST":
        try:
            canonical = normalize_ghana_phone(phone)
            if CustomerAccount.objects.filter(phone=canonical, active=True).exists():
                messages.info(request, "This number already has a KOFAD Market account. Sign in instead.")
                return redirect("market_login")
            services.send_otp(canonical, "register")
            request.session["market_pending_phone"] = canonical
            messages.success(request, "Verification code sent by SMS.")
            return redirect("market_verify")
        except ValidationError as exc:
            messages.error(request, problem(exc))
    return render(request, "marketplace/account_start.html", _market_context(
        request, title="Create your KOFAD Market account", phone=phone,
    ))


def account_verify(request):
    phone = request.session.get("market_pending_phone")
    if not phone:
        return redirect("market_register")
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            try:
                services.send_otp(phone, "register")
                messages.success(request, "A new verification code was sent.")
            except ValidationError as exc:
                messages.error(request, problem(exc))
            return redirect("market_verify")
        try:
            services.verify_otp(phone, request.POST.get("code"), "register")
            request.session["market_verified_phone"] = phone
            return redirect("market_finish")
        except ValidationError as exc:
            messages.error(request, problem(exc))
    return render(request, "marketplace/account_verify.html", _market_context(
        request, title="Verify your phone", phone=phone,
    ))


def account_finish(request):
    phone = request.session.get("market_verified_phone")
    if not phone:
        return redirect("market_register")
    existing = CustomerAccount.objects.filter(phone=phone).first()
    if existing and existing.active:
        services.set_customer_session(request, existing)
        return redirect("market")
    form = CustomerRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        customer = existing or CustomerAccount(phone=phone)
        customer.full_name = data["full_name"].strip()
        customer.email = data["email"].strip().lower()
        customer.verified_at = timezone.now()
        customer.active = True
        customer.set_password(data["password"])
        customer.save()
        services.set_customer_session(request, customer)
        request.session.pop("market_pending_phone", None)
        request.session.pop("market_verified_phone", None)
        messages.success(request, "Welcome to KOFAD Market. Your phone number is verified.")
        after = request.session.pop("market_after_login", None)
        return redirect(after or "market")
    return render(request, "marketplace/account_finish.html", _market_context(
        request, title="Finish your account", phone=phone, form=form,
    ))


def customer_login(request):
    if services.customer_from_session(request):
        return redirect("market")
    requested_next = request.GET.get("next", "")
    if requested_next.startswith("/") and not requested_next.startswith("//"):
        request.session["market_after_login"] = requested_next
    form = CustomerLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = CustomerAccount.objects.filter(phone=form.cleaned_data["phone"], active=True).first()
        if customer and customer.check_password(form.cleaned_data["password"]):
            services.set_customer_session(request, customer)
            after = request.session.pop("market_after_login", None)
            return redirect(after or "market")
        messages.error(request, "The phone number or password is incorrect.")
    return render(request, "marketplace/login.html", _market_context(
        request, title="Customer sign in", form=form,
    ))


@require_POST
def customer_logout(request):
    services.clear_customer_session(request)
    return redirect("public_home")


@market_customer_required
@require_POST
def cart_add(request, customer, pk):
    listing = get_object_or_404(MarketListing.objects.select_related("product"), pk=pk, enabled=True, product__active=True)
    try:
        quantity = int(request.POST.get("quantity", "1"))
    except ValueError:
        quantity = 1
    quantity = min(max(quantity, 1), 999)
    branch = services.market_branch()
    available = services.available_units(branch, listing.product) // max(listing.factor, 1)
    if quantity > available:
        messages.error(request, f"Only {available} {listing.selling_label}(s) are currently available.")
        return redirect("market_product", pk=listing.pk)
    cart = dict(request.session.get("market_cart", {}))
    current = int(cart.get(str(listing.pk), 0))
    cart[str(listing.pk)] = min(current + quantity, available)
    request.session["market_cart"] = cart
    request.session.modified = True
    messages.success(request, f"{listing.display_name} added to your cart.")
    return redirect(request.POST.get("next") or "market_cart")


@market_customer_required
def cart(request, customer):
    rows = services.cart_rows(request.session.get("market_cart", {}))
    branch = services.market_branch()
    subtotal = 0
    for row in rows:
        row["available"] = services.available_units(branch, row["listing"].product) // max(row["listing"].factor, 1)
        subtotal += row["total"]
    return render(request, "marketplace/cart.html", _market_context(
        request, title="Your cart", rows=rows, subtotal=subtotal,
    ))


@market_customer_required
@require_POST
def cart_update(request, customer):
    cart_data = {}
    for key, value in request.POST.items():
        if not key.startswith("qty_"):
            continue
        listing_id = key.removeprefix("qty_")
        if not listing_id.isdigit():
            continue
        try:
            qty = int(value)
        except ValueError:
            continue
        if qty > 0:
            cart_data[listing_id] = min(qty, 999)
    request.session["market_cart"] = cart_data
    request.session.modified = True
    messages.success(request, "Cart updated.")
    return redirect("market_cart")


@market_customer_required
def checkout(request, customer):
    rows = services.cart_rows(request.session.get("market_cart", {}))
    if not rows:
        messages.info(request, "Your cart is empty.")
        return redirect("market")
    initial = {
        "recipient_name": customer.full_name,
        "phone": customer.phone,
        "email": customer.email,
        "fulfilment": "delivery",
    }
    default_address = customer.addresses.filter(is_default=True).first() or customer.addresses.first()
    if default_address:
        initial.update({
            "recipient_name": default_address.recipient_name,
            "phone": default_address.phone,
            "region": default_address.region,
            "town": default_address.town,
            "address_line": default_address.address_line,
            "landmark": default_address.landmark,
            "ghana_post_gps": default_address.ghana_post_gps,
            "latitude": default_address.latitude,
            "longitude": default_address.longitude,
        })
    has_delivery = DeliveryZone.objects.filter(active=True).exists()
    if not has_delivery:
        initial["fulfilment"] = "pickup"
    form = CheckoutForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            order = services.create_order(customer, request.session.get("market_cart", {}), form.cleaned_data)
            if order.fulfilment == "delivery":
                from .models import CustomerAddress
                CustomerAddress.objects.filter(customer=customer, is_default=True).update(is_default=False)
                CustomerAddress.objects.create(
                    customer=customer, label="Latest delivery address",
                    recipient_name=order.recipient_name, phone=order.phone, region=order.region,
                    town=order.town, address_line=order.address_line, landmark=order.landmark,
                    ghana_post_gps=order.ghana_post_gps, latitude=order.latitude, longitude=order.longitude,
                    is_default=True,
                )
            request.session["market_cart"] = {}
            request.session.modified = True
            messages.success(request, f"Order {order.public_reference} created. Complete payment to send it to fulfilment.")
            return redirect("market_order", pk=order.pk)
        except ValidationError as exc:
            messages.error(request, problem(exc))
    subtotal = sum((row["total"] for row in rows), 0)
    return render(request, "marketplace/checkout.html", _market_context(
        request, title="Checkout", form=form, rows=rows, subtotal=subtotal,
        zones=DeliveryZone.objects.filter(active=True),
    ))


@market_customer_required
@require_POST
def order_pay(request, customer, pk):
    order = get_object_or_404(OnlineOrder, pk=pk, customer=customer)
    if order.payment_status == "paid":
        return redirect("market_order", pk=order.pk)
    try:
        attempt = services.initialize_paystack(
            order,
            request.build_absolute_uri("/market/payment/return/"),
        )
        return redirect(attempt.authorization_url)
    except ValidationError as exc:
        messages.error(request, problem(exc))
        return redirect("market_order", pk=order.pk)


def payment_return(request):
    reference = request.GET.get("reference") or request.GET.get("trxref")
    if not reference:
        messages.error(request, "The payment reference is missing.")
        return redirect("market")
    attempt = MarketPaymentAttempt.objects.select_related("order").filter(reference=reference).first()
    if not attempt:
        messages.error(request, "KOFAD could not match that payment to an order.")
        return redirect("market")
    try:
        data = services.verify_paystack(reference)
        order = services.finalize_payment(reference, data)
        messages.success(request, "Payment confirmed. Your order is now with KOFAD fulfilment.")
    except ValidationError as exc:
        order = attempt.order
        messages.error(request, problem(exc))
    customer = services.customer_from_session(request)
    if customer and order.customer_id == customer.pk:
        return redirect("market_order", pk=order.pk)
    return render(request, "marketplace/payment_result.html", _market_context(
        request, title="Payment result", order=order,
    ))


@csrf_exempt
@require_POST
def paystack_webhook(request):
    signature = request.headers.get("x-paystack-signature", "")
    if not services.paystack_signature_valid(request.body, signature):
        return HttpResponse(status=401)
    try:
        event = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponse(status=400)
    if event.get("event") == "charge.success":
        reference = str((event.get("data") or {}).get("reference", ""))
        if reference:
            try:
                verified = services.verify_paystack(reference)
                services.finalize_payment(reference, verified)
            except ValidationError:
                return HttpResponse(status=200)
    return HttpResponse(status=200)


@market_customer_required
def customer_orders(request, customer):
    rows = customer.orders.prefetch_related("lines").all()
    return render(request, "marketplace/orders.html", _market_context(
        request, title="My orders", orders=rows,
    ))


@market_customer_required
def customer_order(request, customer, pk):
    order = get_object_or_404(
        OnlineOrder.objects.prefetch_related("lines", "events"),
        pk=pk, customer=customer,
    )
    return render(request, "marketplace/order_detail.html", _market_context(
        request, title=order.public_reference, order=order,
        handover_code=services.handover_code(order) if order.payment_status == "paid" else "",
    ))


@market_customer_required
def customer_messages(request, customer, conversation_id=None):
    conversation = None
    if conversation_id:
        conversation = get_object_or_404(Conversation, pk=conversation_id, customer=customer)
    if request.method == "POST":
        body = request.POST.get("message", "").strip()[:2000]
        if not body:
            messages.error(request, "Write a message first.")
        else:
            if not conversation:
                order = None
                order_id = request.POST.get("order")
                if order_id:
                    order = OnlineOrder.objects.filter(pk=order_id, customer=customer).first()
                conversation = Conversation.objects.create(
                    customer=customer, public_name=customer.full_name,
                    public_phone=customer.phone, order=order,
                    subject=request.POST.get("subject", "Customer message")[:180],
                )
            ConversationMessage.objects.create(
                conversation=conversation, sender_type="customer", body=body,
                read_by_customer=True,
            )
            Conversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now(), status="open")
            return redirect("market_message_thread", conversation_id=conversation.pk)
    conversations = customer.conversations.prefetch_related("messages").all()
    if conversation:
        conversation.messages.filter(sender_type="staff").update(read_by_customer=True)
    return render(request, "marketplace/messages.html", _market_context(
        request, title="Messages", conversations=conversations, conversation=conversation,
    ))


@protected("operate_sales|manage_company")
def staff_orders(request, branch):
    status = request.GET.get("status", "").strip()
    rows = OnlineOrder.objects.filter(branch=branch).select_related("customer", "delivery_zone", "sale_document")
    if status:
        rows = rows.filter(status=status)
    counts = {
        code: OnlineOrder.objects.filter(branch=branch, status=code).count()
        for code in ["paid", "confirmed", "preparing", "ready_pickup", "out_for_delivery"]
    }
    return render(request, "marketplace/staff_orders.html", {
        "title": "Online Orders",
        "orders": rows[:150],
        "selected_status": status,
        "counts": counts,
        "status_choices": OnlineOrder.STATUSES,
    })


@protected("operate_sales|manage_company")
def staff_order(request, branch, pk):
    order = get_object_or_404(
        OnlineOrder.objects.filter(branch=branch).select_related(
            "customer", "delivery_zone", "party", "sale_document"
        ).prefetch_related("lines", "events", "payment_attempts"),
        pk=pk,
    )
    form = StaffOrderUpdateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.advance_order(request.user, order, form.cleaned_data["action"], form.cleaned_data)
            messages.success(request, "Order workflow updated.")
            return redirect("staff_online_order", pk=order.pk)
        except ValidationError as exc:
            messages.error(request, problem(exc))
    return render(request, "marketplace/staff_order_detail.html", {
        "title": order.public_reference, "order": order, "form": form,
        "handover_code": services.handover_code(order),
    })


@protected("operate_sales|manage_company")
def staff_inbox(request, branch, conversation_id=None):
    conversations = Conversation.objects.select_related("customer", "order", "assigned_to").prefetch_related("messages")
    conversation = None
    if conversation_id:
        conversation = get_object_or_404(conversations, pk=conversation_id)
        conversation.messages.exclude(sender_type="staff").update(read_by_staff=True)
        if request.method == "POST":
            body = request.POST.get("message", "").strip()[:2000]
            action = request.POST.get("action", "reply")
            if action == "close":
                conversation.status = "closed"
                conversation.save(update_fields=["status", "updated_at"])
            elif body:
                ConversationMessage.objects.create(
                    conversation=conversation, sender_type="staff", staff=request.user,
                    body=body, read_by_staff=True,
                )
                conversation.assigned_to = request.user
                conversation.status = "open"
                conversation.save(update_fields=["assigned_to", "status", "updated_at"])
                if conversation.public_phone:
                    services.send_transactional_sms(
                        conversation.public_phone,
                        "KOFAD: A staff member replied to your message. Sign in to KOFAD Market to read it.",
                    )
            return redirect("staff_market_thread", conversation_id=conversation.pk)
    return render(request, "marketplace/staff_inbox.html", {
        "title": "Customer Inbox", "conversations": conversations[:150],
        "conversation": conversation,
        "unread": ConversationMessage.objects.filter(read_by_staff=False).exclude(sender_type="staff").count(),
    })
