import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.identity import normalize_ghana_phone
from core.models import Product
from core.views import problem, protected

from .forms import (
    CheckoutForm, ConversationMessageForm, CustomerAccessForm, CustomerLoginForm,
    CustomerPasswordChangeForm, CustomerPasswordResetForm, CustomerProfileForm, CustomerRegistrationForm,
    DeliveryTrackingForm, DeliveryZoneForm, MarketGalleryForm, MarketReturnRequestForm,
    PublicMessageForm, StaffOrderUpdateForm,
)
from .models import (
    Conversation, ConversationAttachment, ConversationMessage, CustomerAccount,
    DeliveryTrackingUpdate, DeliveryZone, MarketListing, MarketListingImage, MarketPaymentAttempt,
    MarketReturnAttachment, MarketReturnRequest, OnlineOrder, OtpThrottle, RecentView, WishlistItem,
)
from . import services


def _market_context(request, **extra):
    customer = services.customer_from_session(request)
    cart = request.session.get("market_cart", {})
    unread = 0
    if customer:
        unread = ConversationMessage.objects.filter(
            conversation__customer=customer,
            sender_type="staff",
            read_by_customer=False,
        ).count()
    context = {
        "market_customer": customer,
        "market_cart_count": sum(int(value) for value in cart.values() if str(value).isdigit()),
        "market_unread_count": unread,
        "market_wishlist_count": customer.wishlist_items.count() if customer else 0,
        "company": getattr(request, "company", None),
        **extra,
    }
    return context


def _decorate_listings(listings, branch):
    for listing in listings:
        listing.available_units = services.available_units(branch, listing.product) if branch else 0
        listing.available_sell_qty = listing.available_units // max(listing.factor, 1)
        listing.market_price_value = listing.market_price
    return listings


def _save_conversation_message(conversation, sender_type, body="", attachment=None, staff=None):
    body = (body or "").strip()[:2000]
    payload = services.prepare_support_attachment(attachment) if attachment else None
    message = ConversationMessage.objects.create(
        conversation=conversation,
        sender_type=sender_type,
        staff=staff,
        body=body,
        read_by_staff=sender_type == "staff",
        read_by_customer=sender_type != "staff",
    )
    if payload:
        ConversationAttachment.objects.create(message=message, **payload)
    Conversation.objects.filter(pk=conversation.pk).update(
        updated_at=timezone.now(), status="open"
    )
    return message


def _conversation_access(request, conversation):
    if request.user.is_authenticated and (
        request.user.is_superuser
        or request.user.has_perm("core.operate_sales")
        or request.user.has_perm("core.manage_company")
    ):
        return "staff"
    customer = services.customer_from_session(request)
    if customer and conversation.customer_id == customer.pk:
        return "customer"
    return ""


def _message_json(message):
    return {
        "id": message.pk,
        "sender": message.sender_type,
        "sender_label": message.get_sender_type_display(),
        "body": message.body,
        "created": timezone.localtime(message.created_at).strftime("%d %b · %H:%M"),
        "attachments": [{
            "id": attachment.pk,
            "name": attachment.original_name,
            "mime": attachment.mime_type,
            "is_image": attachment.is_image,
            "url": f"/market/support/attachments/{attachment.pk}/",
            "preview_url": f"/market/support/attachments/{attachment.pk}/?inline=1",
        } for attachment in message.attachments.all()],
    }


def market_customer_required(view):
    @wraps(view)
    def inner(request, *args, **kwargs):
        customer = services.customer_from_session(request)
        if not customer:
            request.session["market_after_login"] = request.get_full_path()
            messages.info(request, "Sign in with your verified phone number to continue.")
            return redirect("market_access")
        return view(request, customer, *args, **kwargs)
    return inner


def home(request):
    listings = list(
        MarketListing.objects.filter(enabled=True, product__active=True)
        .select_related("product").order_by("-featured", "sort_order", "product__name")[:8]
    )
    branch = None
    try:
        branch = services.market_branch()
    except ValidationError:
        pass
    _decorate_listings(listings, branch)
    categories = list(
        Product.objects.filter(market_listing__enabled=True, active=True)
        .exclude(category="").values_list("category", flat=True).distinct().order_by("category")[:8]
    )

    enquiry_form = PublicMessageForm(request.POST or None)
    if request.method == "POST" and enquiry_form.is_valid():
        data = enquiry_form.cleaned_data
        recent_count = Conversation.objects.filter(
            public_phone=data["phone"],
            created_at__gte=timezone.now() - timedelta(minutes=15),
        ).count()
        if recent_count >= 3:
            messages.error(request, "Too many messages were sent from this number. Please wait a little and try again.")
            return render(request, "marketplace/home.html", _market_context(
                request, title="KOFAD Market & Operations", listings=listings, enquiry_form=enquiry_form,
            ))
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
        categories=categories,
        enquiry_form=enquiry_form,
    ))


def market(request):
    query = request.GET.get("q", "").strip()[:100]
    category = request.GET.get("category", "").strip()[:80]
    sort = request.GET.get("sort", "featured")
    in_stock = request.GET.get("stock") == "available"
    featured_only = request.GET.get("featured") == "1"
    price_min_raw = request.GET.get("min_price", "").strip()
    price_max_raw = request.GET.get("max_price", "").strip()
    try:
        price_min = Decimal(price_min_raw) if price_min_raw else None
        price_max = Decimal(price_max_raw) if price_max_raw else None
    except InvalidOperation:
        price_min = price_max = None

    rows = MarketListing.objects.filter(enabled=True, product__active=True).select_related("product")
    if query:
        terms = [term for term in query.replace(",", " ").split() if term][:8]
        for term in terms:
            rows = rows.filter(
                Q(title__icontains=term) | Q(description__icontains=term) | Q(tags__icontains=term)
                | Q(product__name__icontains=term) | Q(product__sku__icontains=term)
                | Q(product__category__icontains=term) | Q(highlights__icontains=term)
            )
    if category:
        rows = rows.filter(product__category=category)
    if featured_only:
        rows = rows.filter(featured=True)

    branch = None
    try:
        branch = services.market_branch()
    except ValidationError:
        pass
    listings = _decorate_listings(list(rows.order_by("-featured", "sort_order", "product__name")[:220]), branch)
    if in_stock:
        listings = [listing for listing in listings if listing.available_sell_qty > 0]
    if price_min is not None:
        listings = [listing for listing in listings if listing.market_price_value >= price_min]
    if price_max is not None:
        listings = [listing for listing in listings if listing.market_price_value <= price_max]
    if sort == "price_low":
        listings.sort(key=lambda listing: (listing.market_price_value, listing.display_name.lower()))
    elif sort == "price_high":
        listings.sort(key=lambda listing: (-listing.market_price_value, listing.display_name.lower()))
    elif sort == "name":
        listings.sort(key=lambda listing: listing.display_name.lower())

    customer = services.customer_from_session(request)
    wishlist_ids = set(
        WishlistItem.objects.filter(customer=customer).values_list("listing_id", flat=True)
    ) if customer else set()
    for listing in listings:
        listing.in_wishlist = listing.pk in wishlist_ids

    categories = list(
        Product.objects.filter(market_listing__enabled=True, active=True)
        .exclude(category="").values_list("category", flat=True).distinct().order_by("category")
    )
    prices = [listing.market_price_value for listing in listings]
    return render(request, "marketplace/market.html", _market_context(
        request, title="KOFAD Market", listings=listings, q=query,
        selected_category=category, categories=categories,
        selected_sort=sort, in_stock=in_stock, featured_only=featured_only,
        price_min=price_min_raw, price_max=price_max_raw,
        visible_price_low=min(prices) if prices else None,
        visible_price_high=max(prices) if prices else None,
        total_catalog=MarketListing.objects.filter(enabled=True, product__active=True).count(),
    ))


def product_detail(request, pk):
    listing = get_object_or_404(
        MarketListing.objects.select_related("product").prefetch_related("gallery_images"),
        pk=pk, enabled=True, product__active=True,
    )
    branch = services.market_branch()
    _decorate_listings([listing], branch)
    customer = services.customer_from_session(request)
    in_wishlist = False
    if customer:
        RecentView.objects.update_or_create(
            customer=customer,
            listing=listing,
            defaults={"view_count": 1},
        )
        recent = RecentView.objects.get(customer=customer, listing=listing)
        if recent.view_count > 1:
            RecentView.objects.filter(pk=recent.pk).update(view_count=recent.view_count + 1)
        in_wishlist = WishlistItem.objects.filter(customer=customer, listing=listing).exists()
    related = list(
        MarketListing.objects.filter(
            enabled=True, product__active=True, product__category=listing.product.category
        ).exclude(pk=listing.pk).select_related("product").order_by("-featured", "sort_order")[:4]
    )
    _decorate_listings(related, branch)
    gallery = list(listing.gallery_images.all())
    return render(request, "marketplace/product.html", _market_context(
        request, title=listing.display_name, listing=listing, related=related,
        gallery=gallery, in_wishlist=in_wishlist,
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
    response["Cache-Control"] = "public, max-age=86400" if listing.enabled else "private, no-store"
    response["Content-Disposition"] = "inline"
    return response


def customer_access(request):
    customer = services.customer_from_session(request)
    if customer:
        return redirect("market_account")
    requested_next = request.GET.get("next", "")
    if requested_next.startswith("/") and not requested_next.startswith("//"):
        request.session["market_after_login"] = requested_next
    form = CustomerAccessForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        phone = form.cleaned_data["phone"]
        existing = CustomerAccount.objects.filter(phone=phone, active=True).first()
        if existing:
            request.session["market_login_phone"] = phone
            return redirect("market_login")
        try:
            services.send_otp(phone, "register")
            request.session["market_pending_phone"] = phone
            messages.success(request, "We sent a six-digit verification code to your phone.")
            return redirect("market_verify")
        except ValidationError as exc:
            messages.error(request, problem(exc))
    return render(request, "marketplace/access.html", _market_context(
        request, title="Sign in or create your account", form=form,
    ))


@market_customer_required
def customer_account(request, customer):
    profile_form = CustomerProfileForm(request.POST or None, instance=customer)
    if request.method == "POST" and profile_form.is_valid():
        profile_form.save()
        messages.success(request, "Your account details were updated.")
        return redirect("market_account")

    orders = customer.orders.prefetch_related("lines", "events").all()
    paid_orders = customer.orders.filter(payment_status="paid")
    summary = {
        "orders": customer.orders.count(),
        "paid_orders": paid_orders.count(),
        "spend": paid_orders.aggregate(total=Sum("total"))["total"] or 0,
        "active_orders": customer.orders.exclude(
            status__in=["delivered", "picked_up", "cancelled", "refunded"]
        ).count(),
        "support_threads": customer.conversations.count(),
        "wishlist": customer.wishlist_items.count(),
        "returns": customer.return_requests.count(),
    }
    recent_lines = []
    for order in orders[:8]:
        for line in order.lines.all():
            recent_lines.append(line)
            if len(recent_lines) >= 8:
                break
        if len(recent_lines) >= 8:
            break
    active = customer.orders.exclude(
        status__in=["delivered", "picked_up", "cancelled", "refunded"]
    ).prefetch_related("events")[:4]
    return render(request, "marketplace/account.html", _market_context(
        request,
        title="My KOFAD account",
        profile_form=profile_form,
        summary=summary,
        recent_orders=orders[:6],
        active_orders=active,
        recent_lines=recent_lines,
        addresses=customer.addresses.all()[:4],
        conversations=customer.conversations.all()[:5],
        wishlist=customer.wishlist_items.select_related("listing__product")[:6],
        recent_views=customer.recent_views.select_related("listing__product")[:6],
        return_requests=customer.return_requests.select_related("order")[:5],
    ))


def account_start(request):
    return redirect("market_access")


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


def customer_password_reset_start(request):
    if services.customer_from_session(request):
        return redirect("market")
    phone = request.POST.get("phone", "").strip()
    if request.method == "POST":
        try:
            canonical = normalize_ghana_phone(phone)
            if not CustomerAccount.objects.filter(phone=canonical, active=True).exists():
                # Do not disclose whether a number owns an account.
                messages.success(request, "If this number has a KOFAD Market account, a verification code can be used to continue.")
                return redirect("market_login")
            services.send_otp(canonical, "reset")
            request.session["market_reset_phone"] = canonical
            messages.success(request, "Verification code sent by SMS.")
            return redirect("market_password_reset_verify")
        except ValidationError as exc:
            messages.error(request, problem(exc))
    return render(request, "marketplace/password_reset_start.html", _market_context(
        request, title="Reset customer password", phone=phone,
    ))


def customer_password_reset_verify(request):
    phone = request.session.get("market_reset_phone")
    if not phone:
        return redirect("market_password_reset")
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            try:
                services.send_otp(phone, "reset")
                messages.success(request, "A new verification code was sent.")
            except ValidationError as exc:
                messages.error(request, problem(exc))
            return redirect("market_password_reset_verify")
        try:
            services.verify_otp(phone, request.POST.get("code"), "reset")
            request.session["market_reset_verified_phone"] = phone
            return redirect("market_password_reset_finish")
        except ValidationError as exc:
            messages.error(request, problem(exc))
    return render(request, "marketplace/password_reset_verify.html", _market_context(
        request, title="Verify password reset", phone=phone,
    ))


def customer_password_reset_finish(request):
    phone = request.session.get("market_reset_verified_phone")
    if not phone:
        return redirect("market_password_reset")
    customer = CustomerAccount.objects.filter(phone=phone, active=True).first()
    if not customer:
        request.session.pop("market_reset_phone", None)
        request.session.pop("market_reset_verified_phone", None)
        return redirect("market_login")
    form = CustomerPasswordResetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer.set_password(form.cleaned_data["password"])
        customer.save(update_fields=["password_hash"])
        request.session.pop("market_reset_phone", None)
        request.session.pop("market_reset_verified_phone", None)
        services.set_customer_session(request, customer)
        messages.success(request, "Your KOFAD Market password has been changed.")
        return redirect("market")
    return render(request, "marketplace/password_reset_finish.html", _market_context(
        request, title="Choose a new password", phone=phone, form=form,
    ))


def customer_login(request):
    if services.customer_from_session(request):
        return redirect("market_account")
    requested_next = request.GET.get("next", "")
    if requested_next.startswith("/") and not requested_next.startswith("//"):
        request.session["market_after_login"] = requested_next
    initial_phone = request.session.get("market_login_phone", "")
    form = CustomerLoginForm(request.POST or None, initial={"phone": initial_phone})
    if request.method == "POST" and form.is_valid():
        phone = form.cleaned_data["phone"]
        now = timezone.now()
        with transaction.atomic():
            throttle, _ = OtpThrottle.objects.select_for_update().get_or_create(phone=phone, purpose="login")
            if throttle.blocked_until and throttle.blocked_until > now:
                messages.error(request, "Too many sign-in attempts. Try again in a few minutes.")
                return render(request, "marketplace/login.html", _market_context(
                    request, title="Customer sign in", form=form,
                ))
            customer = CustomerAccount.objects.filter(phone=phone, active=True).first()
            valid = bool(customer and customer.check_password(form.cleaned_data["password"]))
            if valid:
                throttle.attempts = 0
                throttle.blocked_until = None
                throttle.save(update_fields=["attempts", "blocked_until"])
            else:
                throttle.attempts += 1
                if throttle.attempts >= 5:
                    throttle.blocked_until = now + timedelta(minutes=15)
                throttle.save(update_fields=["attempts", "blocked_until"])
        if valid:
            services.set_customer_session(request, customer)
            request.session.pop("market_login_phone", None)
            after = request.session.pop("market_after_login", None)
            return redirect(after or "market_account")
        messages.error(request, "The phone number or password is incorrect.")
    return render(request, "marketplace/login.html", _market_context(
        request, title="Customer sign in", form=form,
    ))


@market_customer_required
def customer_security(request, customer):
    form = CustomerPasswordChangeForm(request.POST or None, customer=customer)
    if request.method == "POST" and form.is_valid():
        customer.set_password(form.cleaned_data["password"])
        customer.save(update_fields=["password_hash"])
        messages.success(request, "Your customer password has been changed securely.")
        return redirect("market_account")
    return render(request, "marketplace/security.html", _market_context(
        request, title="Account security", form=form,
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
    status = request.GET.get("status", "").strip()
    query = request.GET.get("q", "").strip()[:80]
    rows = customer.orders.prefetch_related("lines", "events")
    if status in dict(OnlineOrder.STATUSES):
        rows = rows.filter(status=status)
    if query:
        rows = rows.filter(
            Q(public_reference__icontains=query)
            | Q(lines__description__icontains=query)
            | Q(lines__sku__icontains=query)
        ).distinct()
    counts = {
        "all": customer.orders.count(),
        "active": customer.orders.exclude(
            status__in=["delivered", "picked_up", "cancelled", "refunded"]
        ).count(),
        "complete": customer.orders.filter(status__in=["delivered", "picked_up"]).count(),
    }
    return render(request, "marketplace/orders.html", _market_context(
        request, title="My orders", orders=rows, selected_status=status,
        q=query, counts=counts, order_status_choices=OnlineOrder.STATUSES,
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
@require_POST
def customer_order_cancel(request, customer, pk):
    with transaction.atomic():
        order = get_object_or_404(OnlineOrder.objects.select_for_update(), pk=pk, customer=customer)
        if order.status != "awaiting_payment" or order.payment_status == "paid":
            messages.error(request, "This order can no longer be cancelled from your account.")
            return redirect("market_order", pk=order.pk)
        order.status = "cancelled"
        order.payment_status = "unpaid"
        order.save(update_fields=["status", "payment_status", "updated_at"])
        order.reservations.update(active=False)
        from .models import OrderEvent
        OrderEvent.objects.create(
            order=order, status="cancelled", title="Order cancelled",
            note="Cancelled by the customer before payment.",
        )
    messages.success(request, "The unpaid order was cancelled and its stock hold was released.")
    return redirect("market_order", pk=order.pk)


@market_customer_required
def customer_messages(request, customer, conversation_id=None):
    conversation = None
    order_hint = None
    if conversation_id:
        conversation = get_object_or_404(
            Conversation.objects.prefetch_related("messages__attachments"),
            pk=conversation_id, customer=customer,
        )
    else:
        raw_order = request.GET.get("order", "")
        if raw_order:
            order_hint = OnlineOrder.objects.filter(pk=raw_order, customer=customer).first()

    form = ConversationMessageForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        if not conversation:
            order = None
            order_id = request.POST.get("order")
            if order_id:
                order = OnlineOrder.objects.filter(pk=order_id, customer=customer).first()
            subject = request.POST.get("subject", "").strip()[:180] or (
                f"Order support · {order.public_reference}" if order else "Customer support"
            )
            conversation = Conversation.objects.create(
                customer=customer, public_name=customer.full_name,
                public_phone=customer.phone, order=order, subject=subject,
            )
        try:
            _save_conversation_message(
                conversation, "customer",
                body=form.cleaned_data.get("message", ""),
                attachment=form.cleaned_data.get("attachment"),
            )
        except ValidationError as exc:
            form.add_error("attachment", problem(exc))
        else:
            return redirect("market_message_thread", conversation_id=conversation.pk)

    conversations = customer.conversations.select_related("order", "assigned_to").prefetch_related(
        "messages__attachments"
    ).all()
    if conversation:
        conversation.messages.filter(sender_type="staff").update(read_by_customer=True)
    return render(request, "marketplace/messages.html", _market_context(
        request, title="Support", conversations=conversations, conversation=conversation,
        order_hint=order_hint, support_form=form,
    ))


def conversation_updates(request, conversation_id):
    conversation = get_object_or_404(Conversation, pk=conversation_id)
    side = _conversation_access(request, conversation)
    if not side:
        return JsonResponse({"detail": "Not found."}, status=404)
    try:
        after = max(0, int(request.GET.get("after", "0")))
    except ValueError:
        after = 0
    rows = conversation.messages.filter(pk__gt=after).prefetch_related("attachments")[:100]
    if side == "customer":
        conversation.messages.filter(pk__gt=after, sender_type="staff").update(read_by_customer=True)
    else:
        conversation.messages.filter(pk__gt=after).exclude(sender_type="staff").update(read_by_staff=True)
    response = JsonResponse({
        "conversation": conversation.pk,
        "status": conversation.status,
        "messages": [_message_json(message) for message in rows],
    })
    response["Cache-Control"] = "no-store"
    return response


def support_attachment(request, pk):
    attachment = get_object_or_404(
        ConversationAttachment.objects.select_related("message__conversation"),
        pk=pk,
    )
    if not _conversation_access(request, attachment.message.conversation):
        raise Http404
    disposition = "inline" if (
        request.GET.get("inline") == "1" and attachment.is_image
    ) else "attachment"
    filename = "".join(
        ch for ch in attachment.original_name if ch.isalnum() or ch in " ._()-"
    ).strip() or "kofad-support-file"
    response = HttpResponse(bytes(attachment.data), content_type=attachment.mime_type)
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response



def market_search_suggestions(request):
    query = request.GET.get("q", "").strip()[:80]
    if len(query) < 2:
        return JsonResponse({"results": []})
    rows = MarketListing.objects.filter(
        enabled=True, product__active=True
    ).filter(
        Q(title__icontains=query) | Q(tags__icontains=query)
        | Q(product__name__icontains=query) | Q(product__sku__icontains=query)
        | Q(product__category__icontains=query)
    ).select_related("product").order_by("-featured", "sort_order", "product__name")[:8]
    results = [{
        "id": row.pk,
        "name": row.display_name,
        "category": row.product.category or "KOFAD Market",
        "sku": row.product.sku,
        "price": str(row.market_price),
        "url": f"/market/products/{row.pk}/",
        "image": (
            f"/market/products/{row.pk}/image/thumb/"
            if row.image_thumb else row.image_url
        ),
    } for row in rows]
    response = JsonResponse({"results": results})
    response["Cache-Control"] = "no-store"
    return response


def gallery_image(request, pk, size="large"):
    image = get_object_or_404(MarketListingImage.objects.select_related("listing"), pk=pk)
    if not image.listing.enabled and not (
        request.user.is_authenticated and request.user.has_perm("core.change_product")
    ):
        raise Http404
    data = image.image_thumb if size == "thumb" else image.image_data
    if not data:
        raise Http404
    response = HttpResponse(bytes(data), content_type=image.image_mime or "image/webp")
    response["Content-Disposition"] = "inline"
    response["Cache-Control"] = "public, max-age=86400" if image.listing.enabled else "private, no-store"
    return response


@market_customer_required
@require_POST
def wishlist_toggle(request, customer, pk):
    listing = get_object_or_404(MarketListing, pk=pk, enabled=True, product__active=True)
    item = WishlistItem.objects.filter(customer=customer, listing=listing).first()
    if item:
        item.delete()
        saved = False
        messages.success(request, "Removed from your wishlist.")
    else:
        WishlistItem.objects.create(customer=customer, listing=listing)
        saved = True
        messages.success(request, "Saved to your wishlist.")
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"saved": saved, "count": customer.wishlist_items.count()})
    target = request.POST.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else "market_product", pk=listing.pk)


@market_customer_required
def customer_wishlist(request, customer):
    rows = list(
        customer.wishlist_items.select_related("listing__product").filter(
            listing__enabled=True, listing__product__active=True
        )
    )
    branch = None
    try:
        branch = services.market_branch()
    except ValidationError:
        pass
    listings = [row.listing for row in rows]
    _decorate_listings(listings, branch)
    return render(request, "marketplace/wishlist.html", _market_context(
        request, title="Wishlist", listings=listings,
    ))


@market_customer_required
@require_POST
def reorder_order(request, customer, pk):
    order = get_object_or_404(
        OnlineOrder.objects.prefetch_related("lines__listing"),
        pk=pk, customer=customer,
    )
    cart = request.session.get("market_cart", {})
    added = 0
    skipped = 0
    for line in order.lines.all():
        listing = line.listing
        if not listing or not listing.enabled or not listing.product.active:
            skipped += 1
            continue
        key = str(listing.pk)
        current = int(cart.get(key, 0) or 0)
        cart[key] = min(current + line.quantity, 999)
        added += 1
    request.session["market_cart"] = cart
    request.session.modified = True
    if added:
        messages.success(request, f"Added {added} previous item line(s) to your cart.")
    if skipped:
        messages.info(request, f"{skipped} previous item line(s) are no longer available online.")
    return redirect("market_cart")


@market_customer_required
def customer_returns(request, customer):
    rows = customer.return_requests.select_related("order", "reviewed_by").prefetch_related(
        "lines__order_line", "attachments"
    )
    return render(request, "marketplace/returns.html", _market_context(
        request, title="My returns", return_requests=rows,
    ))


@market_customer_required
def customer_return_request(request, customer, pk):
    order = get_object_or_404(
        OnlineOrder.objects.prefetch_related("lines", "return_requests__lines"),
        pk=pk, customer=customer,
    )
    if order.payment_status != "paid" or order.status not in {"delivered", "picked_up"}:
        messages.error(request, "A return request can be started only after a paid order has been handed over.")
        return redirect("market_order", pk=order.pk)

    return_rows = []
    for line in order.lines.all():
        eligible = services.eligible_market_return_quantity(line)
        return_rows.append({"line": line, "eligible": eligible})

    form = MarketReturnRequestForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        payload = [{
            "line": row["line"].pk,
            "quantity": request.POST.get(f"qty_{row['line'].pk}", "0"),
            "condition": request.POST.get(f"condition_{row['line'].pk}", "sellable"),
        } for row in return_rows]
        try:
            item = services.create_market_return_request(
                customer,
                order,
                payload,
                form.cleaned_data["reason"],
                form.cleaned_data["resolution"],
                form.cleaned_data.get("evidence"),
            )
        except ValidationError as exc:
            form.add_error(None, problem(exc))
        else:
            messages.success(request, "Your return request was submitted to KOFAD.")
            return redirect("market_return_detail", pk=item.pk)
    return render(request, "marketplace/return_request.html", _market_context(
        request, title="Request a return", order=order, rows=return_rows, form=form,
    ))


@market_customer_required
def customer_return_detail(request, customer, pk):
    item = get_object_or_404(
        MarketReturnRequest.objects.select_related("order", "reviewed_by").prefetch_related(
            "lines__order_line", "attachments"
        ),
        pk=pk, customer=customer,
    )
    return render(request, "marketplace/return_detail.html", _market_context(
        request, title="Return request", item=item,
    ))


def market_return_attachment(request, pk):
    attachment = get_object_or_404(
        MarketReturnAttachment.objects.select_related("request__customer"),
        pk=pk,
    )
    customer = services.customer_from_session(request)
    staff_allowed = request.user.is_authenticated and (
        request.user.is_superuser
        or request.user.has_perm("core.operate_sales")
        or request.user.has_perm("core.approve_operations")
        or request.user.has_perm("core.manage_company")
    )
    if not staff_allowed and (not customer or attachment.request.customer_id != customer.pk):
        raise Http404
    inline = request.GET.get("inline") == "1" and attachment.is_image
    filename = "".join(
        ch for ch in attachment.original_name if ch.isalnum() or ch in " ._()-"
    ).strip() or "kofad-return-evidence"
    response = HttpResponse(bytes(attachment.data), content_type=attachment.mime_type)
    response["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@protected("change_product|manage_company")
def market_catalog_admin(request, branch):
    from core.models import Stock
    query = request.GET.get("q", "").strip()[:100]
    state = request.GET.get("state", "all")
    products = Product.objects.all().order_by("category", "name")
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(sku__icontains=query) | Q(category__icontains=query)
        )
    listing_map = {
        row.product_id: row
        for row in MarketListing.objects.filter(product_id__in=products.values("pk")).select_related("product")
    }
    stock_map = dict(
        Stock.objects.filter(branch=branch, product_id__in=products.values("pk"))
        .values_list("product_id", "quantity")
    )
    rows = []
    for product in products[:400]:
        listing = listing_map.get(product.pk)
        photo = bool(listing and (listing.image_data or listing.image_url))
        description = bool(listing and listing.description.strip())
        published = bool(listing and listing.enabled)
        ready = bool(published and photo and description and listing.market_price > 0)
        row = {
            "product": product,
            "listing": listing,
            "photo": photo,
            "description": description,
            "published": published,
            "ready": ready,
            "stock": stock_map.get(product.pk, 0),
            "photo_source": (
                "Business upload" if listing and listing.image_data
                else "Curated" if listing and listing.image_url else "Missing"
            ),
        }
        if state == "published" and not published:
            continue
        if state == "hidden" and published:
            continue
        if state == "incomplete" and (not published or ready):
            continue
        rows.append(row)

    all_listings = MarketListing.objects.select_related("product")
    summary = {
        "products": Product.objects.count(),
        "published": all_listings.filter(enabled=True).count(),
        "featured": all_listings.filter(enabled=True, featured=True).count(),
        "photos": sum(1 for listing in all_listings if listing.image_data or listing.image_url),
    }
    return render(request, "marketplace/catalog_admin.html", {
        "title": "Market Catalog",
        "rows": rows,
        "summary": summary,
        "q": query,
        "selected_state": state,
    })


@protected("manage_company")
def market_settings(request, branch):
    selected = None
    zone_id = request.GET.get("zone", "")
    if zone_id.isdigit():
        selected = DeliveryZone.objects.filter(pk=int(zone_id)).first()
    form = DeliveryZoneForm(request.POST or None, instance=selected)

    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "toggle":
            zone = get_object_or_404(DeliveryZone, pk=request.POST.get("zone"))
            zone.active = not zone.active
            zone.save(update_fields=["active"])
            from core import services as core_services
            core_services.audit(
                request.user, branch, "market.delivery_zone_toggled", zone.pk,
                {"zone": zone.name, "active": zone.active},
            )
            messages.success(request, f"{zone.name} is now {'available' if zone.active else 'hidden'} at checkout.")
            return redirect("market_settings")
        if form.is_valid():
            zone = form.save()
            from core import services as core_services
            core_services.audit(
                request.user, branch, "market.delivery_zone_saved", zone.pk,
                {"zone": zone.name, "fee": str(zone.fee), "active": zone.active},
            )
            messages.success(request, "Delivery area saved.")
            return redirect("market_settings")

    return render(request, "marketplace/settings.html", {
        "title": "Market & Delivery",
        "form": form,
        "selected_zone": selected,
        "zones": DeliveryZone.objects.all(),
        "paystack_ready": bool(settings.PAYSTACK_SECRET_KEY),
        "otp_ready": bool(settings.CUSTOMER_OTP_ENABLED and settings.ARKESEL_API_KEY),
        "sms_ready": bool(settings.SMS_ENABLED and settings.ARKESEL_API_KEY),
        "webhook_url": request.build_absolute_uri("/market/payments/paystack/webhook/"),
        "market_url": request.build_absolute_uri("/market/"),
    })


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
    status = request.GET.get("status", "open")
    conversations = Conversation.objects.select_related(
        "customer", "order", "assigned_to"
    ).prefetch_related("messages__attachments")
    if status in {"open", "closed"}:
        conversations = conversations.filter(status=status)
    conversation = None
    support_form = ConversationMessageForm(request.POST or None, request.FILES or None)
    if conversation_id:
        conversation = get_object_or_404(conversations.model.objects.select_related(
            "customer", "order", "assigned_to"
        ).prefetch_related("messages__attachments"), pk=conversation_id)
        conversation.messages.exclude(sender_type="staff").update(read_by_staff=True)
        if request.method == "POST":
            action = request.POST.get("action", "reply")
            if action == "close":
                conversation.status = "closed"
                conversation.assigned_to = request.user
                conversation.save(update_fields=["status", "assigned_to", "updated_at"])
                return redirect("staff_market_thread", conversation_id=conversation.pk)
            if action == "reopen":
                conversation.status = "open"
                conversation.assigned_to = request.user
                conversation.save(update_fields=["status", "assigned_to", "updated_at"])
                return redirect("staff_market_thread", conversation_id=conversation.pk)
            if support_form.is_valid():
                try:
                    _save_conversation_message(
                        conversation, "staff",
                        body=support_form.cleaned_data.get("message", ""),
                        attachment=support_form.cleaned_data.get("attachment"),
                        staff=request.user,
                    )
                except ValidationError as exc:
                    support_form.add_error("attachment", problem(exc))
                else:
                    conversation.assigned_to = request.user
                    conversation.status = "open"
                    conversation.save(update_fields=["assigned_to", "status", "updated_at"])
                    if conversation.public_phone:
                        services.send_transactional_sms(
                            conversation.public_phone,
                            "KOFAD: A staff member replied to your support conversation. "
                            "Sign in to KOFAD Market to view the reply.",
                        )
                    return redirect("staff_market_thread", conversation_id=conversation.pk)
    return render(request, "marketplace/staff_inbox.html", {
        "title": "Customer Inbox",
        "conversations": conversations[:180],
        "conversation": conversation,
        "support_form": support_form,
        "selected_status": status,
        "unread": ConversationMessage.objects.filter(
            read_by_staff=False
        ).exclude(sender_type="staff").count(),
        "open_count": Conversation.objects.filter(status="open").count(),
        "closed_count": Conversation.objects.filter(status="closed").count(),
    })

