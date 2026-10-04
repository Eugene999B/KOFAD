import hashlib
import hmac
import io
import json
import secrets
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core import services as core_services
from core.identity import normalize_ghana_phone
from core.models import Branch, Closing, Document, Line, Party, Payment, Product, Stock
from core.sms.providers import get_provider
from .models import (
    CustomerAccount, DeliveryZone, MarketListing, MarketPaymentAttempt, OnlineOrder,
    OnlineOrderLine, OrderEvent, OtpThrottle, StockReservation,
)


PAYSTACK_INITIALIZE = "https://api.paystack.co/transaction/initialize"
PAYSTACK_VERIFY = "https://api.paystack.co/transaction/verify/"
ARKESEL_OTP_GENERATE = "https://sms.arkesel.com/api/otp/generate"
ARKESEL_OTP_VERIFY = "https://sms.arkesel.com/api/otp/verify"


def market_branch():
    branch = Branch.objects.filter(active=True).order_by("pk").first()
    if not branch:
        raise ValidationError("KOFAD Market is waiting for an active shop location.")
    return branch


def listing_price(listing):
    value = getattr(listing.product, listing.price_source, None)
    if value is None or value <= 0:
        raise ValidationError(f"{listing.display_name} does not currently have a valid Market price.")
    return value


def active_reserved_units(branch, product, exclude_order=None):
    rows = StockReservation.objects.filter(
        branch=branch, product=product, active=True, expires_at__gt=timezone.now()
    )
    if exclude_order:
        rows = rows.exclude(order=exclude_order)
    return rows.aggregate(total=Sum("units"))["total"] or 0


def available_units(branch, product):
    stock = Stock.objects.filter(branch=branch, product=product).values_list("quantity", flat=True).first() or 0
    return max(int(stock) - int(active_reserved_units(branch, product)), 0)


def _square_webp(image, size, quality):
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    canvas = Image.new("RGB", (size, size), "white")
    contained = ImageOps.contain(image.convert("RGB"), (size - 80, size - 80), Image.Resampling.LANCZOS)
    left = (size - contained.width) // 2
    top = (size - contained.height) // 2
    canvas.paste(contained, (left, top))
    output = io.BytesIO()
    canvas.save(output, "WEBP", quality=quality, method=6)
    return output.getvalue()


def compress_market_image(upload):
    raw = upload.read()
    if not raw:
        raise ValidationError("Choose a product image.")
    if len(raw) > settings.MARKET_IMAGE_MAX_BYTES:
        raise ValidationError("That image is too large. Use an image smaller than 25 MB.")
    try:
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError("KOFAD could not read that picture. Use a normal camera/image file.")
    if image.width < 120 or image.height < 120:
        raise ValidationError("Use a clearer picture at least 120 × 120 pixels.")
    if image.width * image.height > 60000000:
        raise ValidationError("That picture has too many pixels. Resize it before uploading.")
    return (
        _square_webp(image, 1200, 82),
        _square_webp(image, 480, 78),
        "image/webp",
    )


def save_listing_image(listing, upload):
    large, thumb, mime = compress_market_image(upload)
    listing.image_data = large
    listing.image_thumb = thumb
    listing.image_mime = mime
    listing.image_name = str(getattr(upload, "name", "product-image"))[:180]
    listing.image_updated_at = timezone.now()


def _otp_headers():
    if not settings.ARKESEL_API_KEY:
        raise ValidationError("Customer phone verification is not configured yet.")
    return {"api-key": settings.ARKESEL_API_KEY, "Content-Type": "application/json", "Accept": "application/json"}


def send_otp(phone, purpose="register"):
    phone = normalize_ghana_phone(phone)
    now = timezone.now()
    with transaction.atomic():
        row, _ = OtpThrottle.objects.select_for_update().get_or_create(phone=phone, purpose=purpose)
        if row.blocked_until and row.blocked_until > now:
            raise ValidationError("Too many verification attempts. Try again later.")
        if row.last_sent_at and row.last_sent_at < now - timedelta(hours=24):
            row.send_count = 0
            row.attempts = 0
        if row.last_sent_at and row.last_sent_at > now - timedelta(seconds=60):
            raise ValidationError("Please wait one minute before requesting another code.")
        if row.send_count >= 8:
            row.blocked_until = now + timedelta(hours=1)
            row.save(update_fields=["blocked_until"])
            raise ValidationError("Too many codes were requested. Try again in one hour.")

    if not settings.CUSTOMER_OTP_ENABLED:
        raise ValidationError("Customer phone verification is temporarily unavailable.")
    payload = {
        "expiry": 5,
        "length": 6,
        "medium": "sms",
        "message": "Your KOFAD verification code is %otp_code%. It expires in 5 minutes.",
        "number": phone,
        "sender_id": settings.SMS_SENDER_ID,
        "type": "numeric",
    }
    try:
        response = requests.post(
            ARKESEL_OTP_GENERATE, headers=_otp_headers(), json=payload,
            timeout=settings.SMS_TIMEOUT_SECONDS, allow_redirects=False,
        )
        data = response.json()
    except (requests.RequestException, ValueError):
        raise ValidationError("We could not send the verification code right now. Please try again.")
    if not 200 <= response.status_code < 300 or (
        isinstance(data, dict) and str(data.get("status", "")).lower() in {"error", "failed", "failure"}
    ):
        raise ValidationError("We could not send the verification code right now. Please try again.")

    with transaction.atomic():
        row = OtpThrottle.objects.select_for_update().get(phone=phone, purpose=purpose)
        row.send_count += 1
        row.attempts = 0
        row.last_sent_at = now
        row.expires_at = now + timedelta(minutes=5)
        row.verified_at = None
        row.save(update_fields=["send_count", "attempts", "last_sent_at", "expires_at", "verified_at"])
    return phone


def verify_otp(phone, code, purpose="register"):
    phone = normalize_ghana_phone(phone)
    code = str(code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise ValidationError("Enter the six-digit verification code.")
    now = timezone.now()
    with transaction.atomic():
        row = OtpThrottle.objects.select_for_update().filter(phone=phone, purpose=purpose).first()
        if not row or not row.expires_at or row.expires_at < now:
            raise ValidationError("That verification code has expired. Request a new one.")
        if row.blocked_until and row.blocked_until > now:
            raise ValidationError("Too many verification attempts. Try again later.")

    try:
        response = requests.post(
            ARKESEL_OTP_VERIFY, headers=_otp_headers(), json={"code": code, "number": phone},
            timeout=settings.SMS_TIMEOUT_SECONDS, allow_redirects=False,
        )
        data = response.json()
    except (requests.RequestException, ValueError):
        raise ValidationError("We could not verify the code right now. Please try again.")

    success = 200 <= response.status_code < 300 and isinstance(data, dict)
    if success:
        status = str(data.get("status", "")).lower()
        success = status in {"success", "successful", "verified", "ok"} or data.get("success") is True

    with transaction.atomic():
        row = OtpThrottle.objects.select_for_update().get(phone=phone, purpose=purpose)
        if success:
            row.verified_at = now
            row.attempts = 0
            row.save(update_fields=["verified_at", "attempts"])
            return phone
        row.attempts += 1
        if row.attempts >= 6:
            row.blocked_until = now + timedelta(minutes=15)
        row.save(update_fields=["attempts", "blocked_until"])
    raise ValidationError("That verification code is not correct.")


def customer_from_session(request):
    pk = request.session.get("market_customer_id")
    if not pk:
        return None
    customer = CustomerAccount.objects.filter(pk=pk, active=True).first()
    if not customer:
        request.session.pop("market_customer_id", None)
    return customer


def set_customer_session(request, customer):
    request.session.cycle_key()
    request.session["market_customer_id"] = customer.pk
    customer.last_login_at = timezone.now()
    customer.save(update_fields=["last_login_at"])


def clear_customer_session(request):
    request.session.pop("market_customer_id", None)
    request.session.pop("market_pending_phone", None)
    request.session.pop("market_verified_phone", None)


def _reference():
    return "KFD-ORD-" + secrets.token_hex(6).upper()


def cart_rows(cart):
    clean = {}
    for key, value in (cart or {}).items():
        if str(key).isdigit() and str(value).isdigit():
            quantity = min(max(int(value), 1), 999)
            clean[int(key)] = quantity
    listings = MarketListing.objects.filter(
        pk__in=clean, enabled=True, product__active=True
    ).select_related("product")
    rows = []
    for listing in listings:
        price = listing_price(listing)
        quantity = clean[listing.pk]
        rows.append({
            "listing": listing,
            "quantity": quantity,
            "price": price,
            "total": price * quantity,
            "units": listing.factor * quantity,
        })
    rows.sort(key=lambda row: (row["listing"].sort_order, row["listing"].display_name.casefold()))
    return rows


@transaction.atomic
def create_order(customer, cart, cleaned):
    branch = core_services.lock_branch(market_branch())
    rows = cart_rows(cart)
    if not rows:
        raise ValidationError("Your cart is empty.")
    zone = cleaned.get("delivery_zone") if cleaned.get("fulfilment") == "delivery" else None
    delivery_fee = zone.fee if zone else Decimal("0")
    subtotal = sum((row["total"] for row in rows), Decimal("0"))
    order = OnlineOrder.objects.create(
        public_reference=_reference(),
        customer=customer,
        branch=branch,
        delivery_zone=zone,
        fulfilment=cleaned["fulfilment"],
        recipient_name=cleaned["recipient_name"].strip(),
        phone=cleaned["phone"],
        email=cleaned["email"].strip().lower(),
        region=cleaned.get("region") or "",
        town=cleaned.get("town") or "",
        address_line=cleaned.get("address_line") or "",
        landmark=cleaned.get("landmark") or "",
        ghana_post_gps=cleaned.get("ghana_post_gps") or "",
        latitude=cleaned.get("latitude"),
        longitude=cleaned.get("longitude"),
        customer_note=cleaned.get("customer_note") or "",
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        total=subtotal + delivery_fee,
    )
    expiry = timezone.now() + timedelta(minutes=settings.MARKET_RESERVATION_MINUTES)
    for row in rows:
        listing = row["listing"]
        product = Product.objects.select_for_update().get(pk=listing.product_id)
        stock = Stock.objects.select_for_update().filter(branch=branch, product=product).first()
        quantity = stock.quantity if stock else 0
        reserved = active_reserved_units(branch, product)
        if row["units"] > max(quantity - reserved, 0):
            raise ValidationError(f"{product.name} no longer has enough stock for that quantity.")
        OnlineOrderLine.objects.create(
            order=order, product=product, listing=listing,
            description=listing.display_name, sku=product.sku, mode=listing.selling_label,
            quantity=row["quantity"], factor=listing.factor, unit_price=row["price"],
            unit_cost=product.cost, total=row["total"],
        )
        StockReservation.objects.create(
            order=order, branch=branch, product=product, units=row["units"],
            expires_at=expiry,
        )
    OrderEvent.objects.create(
        order=order, status="awaiting_payment", title="Order created",
        note=f"Stock held for {settings.MARKET_RESERVATION_MINUTES} minutes while payment is completed.",
    )
    if customer.email != order.email:
        customer.email = order.email
        customer.save(update_fields=["email"])
    return order


def _paystack_headers():
    if not settings.PAYSTACK_SECRET_KEY:
        raise ValidationError("Online payment is not configured yet. Please contact KOFAD.")
    return {
        "Authorization": "Bearer " + settings.PAYSTACK_SECRET_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def initialize_paystack(order, callback_url):
    reference = order.public_reference + "-" + secrets.token_hex(3).upper()
    payload = {
        "email": order.email,
        "amount": str(int(order.total * 100)),
        "currency": "GHS",
        "reference": reference,
        "callback_url": callback_url,
        "channels": ["card", "mobile_money", "bank_transfer"],
        "metadata": json.dumps({
            "order_reference": order.public_reference,
            "customer_phone": order.phone,
            "fulfilment": order.fulfilment,
        }),
    }
    attempt = MarketPaymentAttempt.objects.create(
        order=order, reference=reference, amount=order.total, currency="GHS", status="initializing"
    )
    order.payment_status = "initializing"
    order.payment_reference = reference
    order.save(update_fields=["payment_status", "payment_reference", "updated_at"])
    try:
        response = requests.post(
            PAYSTACK_INITIALIZE, headers=_paystack_headers(), json=payload,
            timeout=settings.PAYSTACK_TIMEOUT_SECONDS, allow_redirects=False,
        )
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        attempt.status = "failed"
        attempt.provider_message = "Paystack could not be reached."
        attempt.save(update_fields=["status", "provider_message"])
        order.payment_status = "failed"
        order.save(update_fields=["payment_status", "updated_at"])
        raise ValidationError("We could not start the payment. Your order is saved; please try again.") from exc
    if not 200 <= response.status_code < 300 or not data.get("status"):
        attempt.status = "failed"
        attempt.provider_message = str(data.get("message", "Paystack rejected the payment initialization."))[:240]
        attempt.save(update_fields=["status", "provider_message"])
        order.payment_status = "failed"
        order.save(update_fields=["payment_status", "updated_at"])
        raise ValidationError(attempt.provider_message)
    payload_data = data.get("data") or {}
    authorization_url = str(payload_data.get("authorization_url", ""))
    parsed = urlsplit(authorization_url)
    if parsed.scheme != "https" or parsed.hostname != "checkout.paystack.com":
        raise ValidationError("Paystack returned an unsafe checkout address.")
    attempt.status = "pending"
    attempt.access_code = str(payload_data.get("access_code", ""))[:120]
    attempt.authorization_url = authorization_url
    attempt.provider_message = str(data.get("message", ""))[:240]
    attempt.save(update_fields=["status", "access_code", "authorization_url", "provider_message"])
    order.payment_status = "pending"
    order.save(update_fields=["payment_status", "updated_at"])
    return attempt


def verify_paystack(reference):
    safe_reference = str(reference or "").strip()
    if not safe_reference or len(safe_reference) > 100:
        raise ValidationError("Invalid payment reference.")
    try:
        response = requests.get(
            PAYSTACK_VERIFY + safe_reference,
            headers=_paystack_headers(),
            timeout=settings.PAYSTACK_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError("We could not verify the payment yet. Please refresh shortly.") from exc
    if not 200 <= response.status_code < 300 or not data.get("status"):
        raise ValidationError("Paystack could not verify this payment.")
    return data.get("data") or {}


def _system_actor():
    user, created = User.objects.get_or_create(
        username="market-system",
        defaults={"first_name": "KOFAD", "last_name": "Online Market", "is_active": False},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


def _delivery_product():
    product, _ = Product.objects.get_or_create(
        sku="KOFAD-DELIVERY",
        defaults={
            "name": "Delivery service", "base_unit": "delivery", "pack_name": "delivery",
            "pack_size": 1, "cost": Decimal("0"), "retail_unit": Decimal("0"),
            "reorder_level": 0, "active": False,
        },
    )
    return product


def _payment_method(channel):
    if channel == "mobile_money":
        return "momo"
    if channel == "card":
        return "card"
    return "bank"


@transaction.atomic
def finalize_payment(reference, provider_data):
    attempt = MarketPaymentAttempt.objects.select_for_update().select_related("order").filter(reference=reference).first()
    if not attempt:
        raise ValidationError("This payment reference does not belong to a KOFAD order.")
    order = OnlineOrder.objects.select_for_update().get(pk=attempt.order_id)
    if order.payment_status == "paid" and order.sale_document_id:
        return order
    if str(provider_data.get("status", "")).lower() != "success":
        attempt.status = str(provider_data.get("status", "failed"))[:24]
        attempt.save(update_fields=["status"])
        order.payment_status = "failed"
        order.save(update_fields=["payment_status", "updated_at"])
        raise ValidationError("The payment has not been completed.")
    amount = provider_data.get("amount")
    currency = str(provider_data.get("currency", "")).upper()
    if amount != int(order.total * 100) or currency != "GHS":
        order.ledger_status = "attention"
        order.save(update_fields=["ledger_status", "updated_at"])
        raise ValidationError("The verified payment amount does not match this order.")

    branch = core_services.lock_branch(order.branch)
    actor = _system_actor()
    party = Party.objects.filter(branch=branch, kind="customer", phone=order.phone).first()
    if not party:
        party = Party.objects.create(
            branch=branch, kind="customer", name=order.recipient_name,
            phone=order.phone, email=order.email,
            address=", ".join(value for value in [order.address_line, order.town, order.region] if value),
            consent=False,
        )
    doc = Document.objects.create(
        branch=branch, kind="sale", party=party, reference=core_services.reference("sale", branch),
        total=order.total, paid=order.total, document_date=timezone.localdate(),
        note=f"Online Market order {order.public_reference}", created_by=actor,
        external_reference=reference,
    )
    for item in order.lines.select_related("product"):
        stock = Stock.objects.select_for_update().filter(branch=branch, product=item.product).first()
        available = stock.quantity if stock else 0
        reserved_by_others = active_reserved_units(branch, item.product, exclude_order=order)
        if item.base_units > max(available - reserved_by_others, 0):
            order.ledger_status = "attention"
            order.save(update_fields=["ledger_status", "updated_at"])
            raise ValidationError(f"Stock changed before payment completed for {item.description}. Management has been alerted.")
        Line.objects.create(
            document=doc, product=item.product, description=item.description, mode=item.mode,
            quantity=item.quantity, factor=item.factor, list_price=item.unit_price,
            unit_price=item.unit_price, discount_percent=0, unit_cost=item.unit_cost, total=item.total,
        )
        core_services.stock_move(
            actor, branch, item.product, -item.base_units, doc.reference,
            f"Online order {order.public_reference}",
        )
    if order.delivery_fee > 0:
        Line.objects.create(
            document=doc, product=_delivery_product(), description="Delivery service", mode="delivery",
            quantity=1, factor=1, list_price=order.delivery_fee, unit_price=order.delivery_fee,
            discount_percent=0, unit_cost=0, total=order.delivery_fee,
        )
    channel = str(provider_data.get("channel", ""))
    Payment.objects.create(
        document=doc, method=_payment_method(channel), amount=order.total,
        reference=reference, direction=1,
    )
    now = timezone.now()
    order.party = party
    order.sale_document = doc
    order.status = "paid"
    order.payment_status = "paid"
    order.payment_reference = reference
    order.payment_channel = channel[:40]
    order.paid_at = now
    order.ledger_status = "attention" if Closing.objects.filter(branch=branch, date=timezone.localdate()).exists() else "posted"
    order.save(update_fields=[
        "party", "sale_document", "status", "payment_status", "payment_reference",
        "payment_channel", "paid_at", "ledger_status", "updated_at",
    ])
    order.reservations.update(active=False)
    attempt.status = "success"
    attempt.verified_at = now
    attempt.save(update_fields=["status", "verified_at"])
    OrderEvent.objects.create(
        order=order, status="paid", title="Payment confirmed",
        note="Payment was verified by Paystack and the order entered KOFAD fulfilment.",
    )
    core_services.audit(actor, branch, "sale.online_paid", order.public_reference, {
        "document": doc.reference, "paystack_reference": reference,
        "amount": str(order.total), "channel": channel,
        "late_after_close": order.ledger_status == "attention",
    })
    send_transactional_sms(
        order.phone,
        f"KOFAD: Payment confirmed for {order.public_reference}. We are preparing your order. "
        f"Track it in your KOFAD Market account.",
    )
    return order


def paystack_signature_valid(raw_body, signature):
    if not settings.PAYSTACK_SECRET_KEY or not signature:
        return False
    digest = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, str(signature))


def handover_code(order):
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        ("market-handover:" + str(order.pk)).encode(),
        hashlib.sha256,
    ).hexdigest()
    return str(int(digest[:12], 16) % 1000000).zfill(6)


def send_transactional_sms(phone, body):
    if not settings.SMS_ENABLED or not settings.ARKESEL_API_KEY:
        return "disabled"
    try:
        provider = get_provider(settings.SMS_PROVIDER)
        result = provider.submit(
            normalize_ghana_phone(phone), body[:1000], settings.SMS_SENDER_ID, "",
            settings.SMS_SANDBOX,
        )
        return result.status
    except Exception:
        return "failed"


TRANSITIONS = {
    "confirm": ({"paid"}, "confirmed", "Order confirmed"),
    "prepare": ({"paid", "confirmed"}, "preparing", "Preparing your order"),
    "ready_pickup": ({"paid", "confirmed", "preparing"}, "ready_pickup", "Ready for pickup"),
    "dispatch": ({"paid", "confirmed", "preparing"}, "out_for_delivery", "Out for delivery"),
    "complete_delivery": ({"out_for_delivery"}, "delivered", "Delivered"),
    "complete_pickup": ({"ready_pickup"}, "picked_up", "Picked up"),
    "cancel_unpaid": ({"awaiting_payment"}, "cancelled", "Order cancelled"),
}


@transaction.atomic
def advance_order(user, order, action, cleaned):
    order = OnlineOrder.objects.select_for_update().get(pk=order.pk)
    rule = TRANSITIONS.get(action)
    if not rule or order.status not in rule[0]:
        raise ValidationError("That order cannot move to the selected stage.")
    target, title = rule[1], rule[2]
    if action == "dispatch" and order.fulfilment != "delivery":
        raise ValidationError("A pickup order cannot be sent out for delivery.")
    if action == "ready_pickup" and order.fulfilment != "pickup":
        raise ValidationError("A delivery order cannot be marked ready for pickup.")
    if action in {"complete_delivery", "complete_pickup"}:
        if str(cleaned.get("handover_code", "")).strip() != handover_code(order):
            raise ValidationError("Enter the customer's six-digit handover code to complete this order.")
    if action == "dispatch":
        order.delivery_agent_name = cleaned.get("delivery_agent_name", "").strip()
        order.delivery_agent_phone = cleaned.get("delivery_agent_phone", "")
    order.status = target
    if target in {"delivered", "picked_up"}:
        order.completed_at = timezone.now()
    if target == "cancelled":
        order.reservations.update(active=False)
    order.staff_note = cleaned.get("note", "").strip() or order.staff_note
    order.save(update_fields=[
        "status", "delivery_agent_name", "delivery_agent_phone",
        "completed_at", "staff_note", "updated_at",
    ])
    note = cleaned.get("note", "").strip()
    OrderEvent.objects.create(order=order, status=target, title=title, note=note, actor=user)
    core_services.audit(user, order.branch, "sale.online_status", order.public_reference, {
        "status": target, "action": action,
    })
    if target in {"confirmed", "preparing", "ready_pickup", "out_for_delivery", "delivered", "picked_up"}:
        extra = ""
        if target in {"ready_pickup", "out_for_delivery"}:
            extra = f" Your handover code is {handover_code(order)}."
        send_transactional_sms(order.phone, f"KOFAD: {title} for {order.public_reference}.{extra}")
    return order
