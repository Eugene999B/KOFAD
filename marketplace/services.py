import hashlib
import hmac
import io
import json
import logging
import math
import re
import secrets
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core import services as core_services
from core.identity import normalize_ghana_phone
from core.models import Branch, Closing, Company, Document, Line, Party, Payment, Product, Stock
from core.sms.providers import get_provider
from .models import (
    CustomerAccount, DeliveryZone, DeliveryTrackingUpdate, MarketListing, MarketListingImage,
    MarketPaymentAttempt, MarketReturnAttachment, MarketReturnRequest, MarketReturnRequestLine,
    OnlineOrder, OnlineOrderLine, OrderEvent, OtpThrottle, StockReservation,
)


PAYSTACK_INITIALIZE = "https://api.paystack.co/transaction/initialize"
PAYSTACK_VERIFY = "https://api.paystack.co/transaction/verify/"
PAYSTACK_REFUND = "https://api.paystack.co/refund"
ARKESEL_OTP_GENERATE = "https://sms.arkesel.com/api/otp/generate"
ARKESEL_OTP_VERIFY = "https://sms.arkesel.com/api/otp/verify"
GOOGLE_ROUTES = "https://routes.googleapis.com/directions/v2:computeRoutes"
GOOGLE_GEOCODE = "https://maps.googleapis.com/maps/api/geocode/json"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"

logger = logging.getLogger(__name__)


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


def _delivery_company():
    return Company.objects.first() or Company()


def _validated_coordinates(latitude, longitude):
    try:
        lat = Decimal(str(latitude))
        lng = Decimal(str(longitude))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValidationError("Choose a valid map location.") from exc
    if not lat.is_finite() or not lng.is_finite():
        raise ValidationError("Choose a valid map location.")
    if not Decimal("-90") <= lat <= Decimal("90") or not Decimal("-180") <= lng <= Decimal("180"):
        raise ValidationError("Choose a valid map location.")
    return lat, lng


def _haversine_km(origin_lat, origin_lng, destination_lat, destination_lng):
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        [float(origin_lat), float(origin_lng), float(destination_lat), float(destination_lng)],
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return Decimal(str(6371.0088 * 2 * math.asin(math.sqrt(a))))


def _google_route(origin_lat, origin_lng, destination_lat, destination_lng):
    if not settings.GOOGLE_MAPS_SERVER_KEY:
        return None
    payload = {
        "origin": {"location": {"latLng": {"latitude": float(origin_lat), "longitude": float(origin_lng)}}},
        "destination": {"location": {"latLng": {"latitude": float(destination_lat), "longitude": float(destination_lng)}}},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "computeAlternativeRoutes": False,
        "languageCode": "en-GB",
        "units": "METRIC",
    }
    try:
        response = requests.post(
            GOOGLE_ROUTES,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": settings.GOOGLE_MAPS_SERVER_KEY,
                "X-Goog-FieldMask": "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline",
            },
            json=payload,
            timeout=settings.GOOGLE_MAPS_TIMEOUT_SECONDS,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Google route lookup failed: %s", exc)
        return None
    routes = body.get("routes") or []
    if not (200 <= response.status_code < 300 and routes):
        logger.warning("Google route lookup was unavailable: %s", body.get("error") or body)
        return None
    route = routes[0]
    duration = str(route.get("duration") or "0s").rstrip("s")
    try:
        duration_seconds = max(int(float(duration or 0)), 0)
    except ValueError:
        duration_seconds = 0
    return {
        "distance_km": Decimal(str(route.get("distanceMeters", 0))) / Decimal("1000"),
        "duration_seconds": duration_seconds,
        "polyline": ((route.get("polyline") or {}).get("encodedPolyline") or "")[:12000],
        "source": "google_route",
    }


def delivery_quote(latitude=None, longitude=None):
    company = _delivery_company()
    mode = company.delivery_pricing_mode
    if not company.delivery_enabled:
        raise ValidationError("Delivery is not available right now.")

    destination = None
    if latitude not in (None, "") and longitude not in (None, ""):
        destination = _validated_coordinates(latitude, longitude)

    origin = None
    if company.delivery_origin_latitude is not None and company.delivery_origin_longitude is not None:
        origin = (company.delivery_origin_latitude, company.delivery_origin_longitude)

    route = None
    if destination and origin:
        route = _google_route(*origin, *destination) if mode == "distance" else None
        if route is None:
            route = {
                "distance_km": _haversine_km(*origin, *destination),
                "duration_seconds": 0,
                "polyline": "",
                "source": "straight_line",
            }

    if mode == "distance" and not origin:
        raise ValidationError("Delivery pricing is not ready yet. Please choose pickup or contact us.")
    if mode == "distance" and not destination:
        raise ValidationError("Pin your delivery location to calculate the delivery fee.")

    distance = route["distance_km"] if route else None
    if distance is not None:
        distance = distance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        maximum = company.delivery_max_distance_km
        if maximum > 0 and distance > maximum:
            raise ValidationError(
                f"This location is {distance} km away, outside the {maximum} km delivery range."
            )

    if mode == "free":
        fee = Decimal("0")
    elif mode == "flat":
        fee = company.delivery_flat_fee
    else:
        fee = (distance * company.delivery_rate_per_km).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        fee = max(fee, company.delivery_minimum_fee)

    return {
        "mode": mode,
        "fee": fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "distance_km": distance,
        "distance_source": route["source"] if route else "",
        "duration_seconds": route["duration_seconds"] if route else 0,
        "polyline": route["polyline"] if route else "",
        "origin_latitude": origin[0] if origin else None,
        "origin_longitude": origin[1] if origin else None,
        "origin_label": company.delivery_origin_label or company.address or company.name,
    }


def _nominatim_headers():
    company = _delivery_company()
    contact = company.email or "support@kofad.local"
    return {"User-Agent": f"KOFAD-Market/1.0 ({contact})", "Accept": "application/json"}


def location_search(query):
    query = (query or "").strip()[:180]
    if len(query) < 3:
        raise ValidationError("Enter at least 3 characters to search for a location.")
    if settings.GOOGLE_MAPS_SERVER_KEY:
        try:
            response = requests.get(
                GOOGLE_GEOCODE,
                params={"address": query, "components": "country:GH", "key": settings.GOOGLE_MAPS_SERVER_KEY},
                timeout=settings.GOOGLE_MAPS_TIMEOUT_SECONDS,
            )
            body = response.json()
            if response.ok and body.get("status") in {"OK", "ZERO_RESULTS"}:
                return [
                    {
                        "label": row.get("formatted_address") or query,
                        "latitude": row["geometry"]["location"]["lat"],
                        "longitude": row["geometry"]["location"]["lng"],
                    }
                    for row in (body.get("results") or [])[:5]
                    if row.get("geometry", {}).get("location")
                ]
        except (requests.RequestException, ValueError, KeyError) as exc:
            logger.warning("Google geocoding failed: %s", exc)

    if not cache.add("kofad:nominatim:search-lock", "1", timeout=1):
        raise ValidationError("Please wait a moment before searching again.")
    try:
        response = requests.get(
            NOMINATIM_SEARCH,
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 5,
                "countrycodes": "gh",
                "addressdetails": 1,
            },
            headers=_nominatim_headers(),
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError("Location search is temporarily unavailable. You can still use current location or pin the map.") from exc
    return [
        {
            "label": str(row.get("display_name") or query)[:240],
            "latitude": row.get("lat"),
            "longitude": row.get("lon"),
        }
        for row in rows[:5]
        if row.get("lat") and row.get("lon")
    ]


def reverse_location(latitude, longitude):
    lat, lng = _validated_coordinates(latitude, longitude)
    if settings.GOOGLE_MAPS_SERVER_KEY:
        try:
            response = requests.get(
                GOOGLE_GEOCODE,
                params={"latlng": f"{lat},{lng}", "key": settings.GOOGLE_MAPS_SERVER_KEY},
                timeout=settings.GOOGLE_MAPS_TIMEOUT_SECONDS,
            )
            body = response.json()
            if response.ok and body.get("status") == "OK" and body.get("results"):
                return {"label": body["results"][0].get("formatted_address") or f"{lat}, {lng}"}
        except (requests.RequestException, ValueError, KeyError) as exc:
            logger.warning("Google reverse geocoding failed: %s", exc)

    if not cache.add("kofad:nominatim:reverse-lock", "1", timeout=1):
        return {"label": f"{lat}, {lng}"}
    try:
        response = requests.get(
            NOMINATIM_REVERSE,
            params={"lat": str(lat), "lon": str(lng), "format": "jsonv2", "zoom": 18},
            headers=_nominatim_headers(),
            timeout=10,
        )
        response.raise_for_status()
        body = response.json()
        return {"label": str(body.get("display_name") or f"{lat}, {lng}")[:240]}
    except (requests.RequestException, ValueError):
        return {"label": f"{lat}, {lng}"}


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
    listing.image_url = ""
    listing.image_credit = ""


def save_gallery_image(listing, upload, *, alt_text="", sort_order=100):
    large, thumb, mime = compress_market_image(upload)
    return MarketListingImage.objects.create(
        listing=listing,
        image_data=large,
        image_thumb=thumb,
        image_mime=mime,
        image_name=str(getattr(upload, "name", "gallery-image"))[:180],
        alt_text=(alt_text or listing.display_name)[:180],
        sort_order=sort_order,
    )


SUPPORT_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
SUPPORT_DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
}


def prepare_support_attachment(upload):
    raw = upload.read()
    if not raw:
        raise ValidationError("The attached file is empty.")
    if len(raw) > SUPPORT_ATTACHMENT_MAX_BYTES:
        raise ValidationError("Support attachments must be 10 MB or smaller.")

    name = str(getattr(upload, "name", "attachment"))[:220]
    lower = name.lower()
    if (getattr(upload, "content_type", "") or "").startswith("image/"):
        try:
            image = Image.open(io.BytesIO(raw))
            image = ImageOps.exif_transpose(image)
            image.load()
        except (UnidentifiedImageError, OSError, ValueError):
            raise ValidationError("KOFAD could not read that attached image.")
        if image.width * image.height > 60000000:
            raise ValidationError("That attached image has too many pixels.")
        image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        output = io.BytesIO()
        image.convert("RGB").save(output, "WEBP", quality=82, method=6)
        stem = name.rsplit(".", 1)[0][:190] or "support-image"
        data = output.getvalue()
        return {
            "original_name": stem + ".webp",
            "mime_type": "image/webp",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "data": data,
        }

    extension = next((ext for ext in SUPPORT_DOCUMENT_TYPES if lower.endswith(ext)), "")
    if not extension:
        raise ValidationError("Attach an image, PDF, Word, Excel or text file.")
    mime = SUPPORT_DOCUMENT_TYPES[extension]
    return {
        "original_name": name,
        "mime_type": mime,
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "data": raw,
    }


def _otp_digest(phone, purpose, code):
    payload = f"kofad-market-otp:{phone}:{purpose}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def _submit_customer_otp_sms(phone, code):
    if not settings.SMS_ENABLED or not settings.ARKESEL_API_KEY:
        raise ValidationError("Customer phone verification is temporarily unavailable.")
    body = f"KOFAD verification code: {code}. It expires in 10 minutes. Do not share this code."
    try:
        provider = get_provider(settings.SMS_PROVIDER)
        provider.validate()
        # Customer verification must reach the handset; never use the SMS sandbox here.
        result = provider.submit(
            phone,
            body,
            settings.SMS_SENDER_ID,
            "",
            False,
        )
    except ValidationError:
        logger.exception("Customer OTP SMS configuration rejected")
        raise ValidationError("We could not send the verification code right now. Please try again.")
    except Exception:
        logger.exception("Customer OTP SMS submission raised an unexpected error")
        raise ValidationError("We could not send the verification code right now. Please try again.")

    if result.status not in {"accepted", "delivered", "unknown"}:
        logger.warning(
            "Customer OTP SMS rejected status=%s error_code=%s detail=%s recipient_suffix=%s",
            result.status,
            result.error_code,
            result.error_detail,
            phone[-4:],
        )
        raise ValidationError("We could not send the verification code right now. Please try again.")
    if result.status == "unknown":
        logger.warning(
            "Customer OTP SMS provider result uncertain error_code=%s detail=%s recipient_suffix=%s",
            result.error_code,
            result.error_detail,
            phone[-4:],
        )
    return result


def send_otp(phone, purpose="register"):
    phone = normalize_ghana_phone(phone)
    if not settings.CUSTOMER_OTP_ENABLED:
        raise ValidationError("Customer phone verification is temporarily unavailable.")
    # Persist the throttle identity even when a provider rejects the first send.
    OtpThrottle.objects.get_or_create(phone=phone, purpose=purpose)
    with transaction.atomic():
        row = OtpThrottle.objects.select_for_update().get(phone=phone, purpose=purpose)
        now = timezone.now()
        if row.blocked_until and row.blocked_until > now:
            raise ValidationError("Too many verification attempts. Try again later.")
        if row.last_sent_at and row.last_sent_at < now - timedelta(hours=24):
            row.send_count = 0
            row.attempts = 0
        if row.last_sent_at and row.last_sent_at > now - timedelta(seconds=60):
            raise ValidationError("Please wait one minute before requesting another code.")
        if row.send_count >= 8:
            raise ValidationError("Daily verification-code limit reached. Try again later.")
        # Hold only this phone/purpose lock during the bounded provider call.
        # Concurrent resend requests cannot deliver two different usable codes.
        code = f"{secrets.randbelow(1000000):06d}"
        _submit_customer_otp_sms(phone, code)
        issued_at = timezone.now()
        row.send_count += 1
        row.attempts = 0
        row.last_sent_at = issued_at
        row.expires_at = issued_at + timedelta(minutes=10)
        row.verified_at = None
        row.code_digest = _otp_digest(phone, purpose, code)
        row.save(update_fields=[
            "send_count", "attempts", "last_sent_at", "expires_at",
            "verified_at", "code_digest",
        ])
    return phone


def verify_otp(phone, code, purpose="register"):
    phone = normalize_ghana_phone(phone)
    # Accept common SMS formatting such as "123 456" or "123-456",
    # but reject letters, extra digits and other ambiguous input.
    raw_code = str(code or "").strip()
    code = re.sub(r"[\s-]", "", raw_code)
    if not re.fullmatch(r"[0-9]{6}", code):
        raise ValidationError("Enter the six-digit verification code.")
    now = timezone.now()

    with transaction.atomic():
        row = OtpThrottle.objects.select_for_update().filter(phone=phone, purpose=purpose).first()
        if not row or not row.expires_at or row.expires_at < now or not row.code_digest:
            raise ValidationError("That verification code has expired. Request a new one.")
        if row.blocked_until and row.blocked_until > now:
            raise ValidationError("Too many verification attempts. Try again later.")

        expected = _otp_digest(phone, purpose, code)
        if hmac.compare_digest(row.code_digest, expected):
            row.verified_at = now
            row.attempts = 0
            row.code_digest = ""
            row.save(update_fields=["verified_at", "attempts", "code_digest"])
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
    now = timezone.now().timestamp()
    expires_at = request.session.get("market_session_expires_at")
    if expires_at is None:
        request.session["market_session_expires_at"] = now + settings.MARKET_SESSION_SECONDS
    else:
        try:
            expired = float(expires_at) <= now
        except (TypeError, ValueError):
            expired = True
        if expired:
            clear_customer_session(request)
            return None
    customer = CustomerAccount.objects.filter(pk=pk, active=True).first()
    stamp = request.session.get("market_credential_stamp")
    if not customer or (stamp and not hmac.compare_digest(stamp, customer_credential_stamp(customer))):
        clear_customer_session(request)
        return None
    if not stamp:
        request.session["market_credential_stamp"] = customer_credential_stamp(customer)
    return customer


def customer_credential_stamp(customer):
    return hmac.new(settings.SECRET_KEY.encode(), (customer.phone + ":" + customer.password_hash).encode(), hashlib.sha256).hexdigest()


def set_customer_session(request, customer):
    preserved = {
        key: request.session.get(key)
        for key in ("market_cart", "market_after_login")
        if request.session.get(key) is not None
    }
    if request.user.is_authenticated:
        auth_logout(request)
        for key, value in preserved.items():
            request.session[key] = value
    request.session.cycle_key()
    request.session["market_customer_id"] = customer.pk
    request.session["market_credential_stamp"] = customer_credential_stamp(customer)
    request.session["market_session_expires_at"] = (
        timezone.now().timestamp() + settings.MARKET_SESSION_SECONDS
    )
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)
    customer.last_login_at = timezone.now()
    customer.save(update_fields=["last_login_at"])


def clear_customer_session(request):
    request.session.pop("market_customer_id", None)
    request.session.pop("market_credential_stamp", None)
    request.session.pop("market_change_phone", None)
    request.session.pop("market_session_expires_at", None)
    request.session.pop("market_pending_phone", None)
    request.session.pop("market_verified_phone", None)


def _reference():
    return "KFD-ORD-" + secrets.token_hex(6).upper()


def _confirmed_reference():
    for _ in range(8):
        reference = f"KFD-{timezone.localdate():%Y%m%d}-{secrets.token_hex(4).upper()}"
        if not OnlineOrder.objects.filter(confirmed_reference=reference).exists():
            return reference
    raise RuntimeError("Could not allocate a unique KOFAD order ID.")


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
    quote = (
        delivery_quote(cleaned.get("latitude"), cleaned.get("longitude"))
        if cleaned.get("fulfilment") == "delivery"
        else {
            "mode": "pickup", "fee": Decimal("0"), "distance_km": None,
            "distance_source": "", "duration_seconds": 0, "polyline": "",
            "origin_latitude": None, "origin_longitude": None,
        }
    )
    delivery_fee = quote["fee"]
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
        delivery_distance_km=quote["distance_km"],
        delivery_distance_source=quote["distance_source"],
        delivery_pricing_mode=quote["mode"],
        delivery_origin_latitude=quote["origin_latitude"],
        delivery_origin_longitude=quote["origin_longitude"],
        delivery_route_polyline=quote["polyline"],
        delivery_duration_seconds=quote["duration_seconds"] or None,
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


@transaction.atomic
def refresh_order_reservations(order):
    order = OnlineOrder.objects.select_for_update().select_related("branch").get(pk=order.pk)
    if order.payment_status == "paid":
        return order
    if order.status != "awaiting_payment":
        raise ValidationError("This order can no longer be paid.")

    branch = core_services.lock_branch(order.branch)
    expiry = timezone.now() + timedelta(minutes=settings.MARKET_RESERVATION_MINUTES)
    for item in order.lines.select_related("product"):
        stock = Stock.objects.select_for_update().filter(branch=branch, product=item.product).first()
        stock_units = stock.quantity if stock else 0
        reserved_by_others = active_reserved_units(branch, item.product, exclude_order=order)
        if item.base_units > max(stock_units - reserved_by_others, 0):
            raise ValidationError(
                f"{item.description} no longer has enough stock for this order. "
                "Please contact KOFAD or create a new order with the available quantity."
            )
        StockReservation.objects.update_or_create(
            order=order,
            product=item.product,
            defaults={
                "branch": branch,
                "units": item.base_units,
                "expires_at": expiry,
                "active": True,
            },
        )
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
    headers = _paystack_headers()  # Fail before creating an attempt when unconfigured.
    with transaction.atomic():
        order = OnlineOrder.objects.select_for_update().get(pk=order.pk)
        if order.payment_status in {"paid", "refunded"} or order.status != "awaiting_payment":
            raise ValidationError("This order cannot start another payment.")
        recent = order.payment_attempts.filter(
            status__in=["initializing", "submission_unknown", "pending", "attention"],
        ).order_by("-created_at").first()
        if recent:
            if recent.provider != "paystack":
                raise ValidationError("Reconcile the original payment before changing provider.")
            if recent.authorization_url and recent.status == "pending":
                return recent
            raise ValidationError("The previous payment is being checked. Please do not pay again.")
        order = refresh_order_reservations(order)
        reference = order.public_reference + "-" + secrets.token_hex(8).upper()
        attempt = MarketPaymentAttempt.objects.create(
            order=order, provider="paystack", reference=reference, amount=order.total,
            currency="GHS", status="initializing",
        )
        order.payment_status = "initializing"
        order.payment_reference = reference
        order.save(update_fields=["payment_status", "payment_reference", "updated_at"])
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
    try:
        response = requests.post(
            PAYSTACK_INITIALIZE, headers=headers, json=payload,
            timeout=settings.PAYSTACK_TIMEOUT_SECONDS, allow_redirects=False,
        )
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        attempt.status = "submission_unknown"
        attempt.provider_message = "Paystack could not be reached."
        attempt.save(update_fields=["status", "provider_message"])
        order.payment_status = "pending"
        order.save(update_fields=["payment_status", "updated_at"])
        raise ValidationError("We could not start the payment. Your order is saved; please wait for confirmation.") from exc
    if not isinstance(data, dict):
        data = {}
    if not 200 <= response.status_code < 300 or data.get("status") is not True:
        attempt.status = "submission_unknown"
        attempt.provider_message = str(data.get("message", "Paystack rejected the payment initialization."))[:240]
        attempt.save(update_fields=["status", "provider_message"])
        order.payment_status = "pending"
        order.save(update_fields=["payment_status", "updated_at"])
        raise ValidationError("We could not start the payment. Please wait for confirmation.")
    payload_data = data.get("data")
    if not isinstance(payload_data, dict):
        payload_data = {}
    authorization_url = str(payload_data.get("authorization_url", ""))
    parsed = urlsplit(authorization_url)
    if (parsed.scheme != "https" or parsed.netloc != "checkout.paystack.com"
            or not parsed.path.strip("/") or not payload_data.get("access_code")
            or payload_data.get("reference") != reference or len(authorization_url) > 200):
        attempt.status = "submission_unknown"
        attempt.provider_message = "Invalid secure checkout response."
        attempt.save(update_fields=["status", "provider_message"])
        order.payment_status = "pending"
        order.save(update_fields=["payment_status", "updated_at"])
        raise ValidationError("We could not open the secure payment page. Please wait for confirmation.")
    attempt.status = "pending"
    attempt.access_code = str(payload_data.get("access_code", ""))[:120]
    attempt.authorization_url = authorization_url
    attempt.provider_message = str(data.get("message", ""))[:240]
    attempt.save(update_fields=["status", "access_code", "authorization_url", "provider_message"])
    order.payment_status = "pending"
    order.save(update_fields=["payment_status", "updated_at"])
    return attempt


class PaymentVerificationUnavailable(ValidationError):
    """Temporary provider failure; webhook delivery must be retried."""


def verify_paystack(reference):
    safe_reference = str(reference or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._=-]{1,100}", safe_reference):
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
        raise PaymentVerificationUnavailable("We could not verify the payment yet. Please refresh shortly.") from exc
    if not 200 <= response.status_code < 300 or not isinstance(data, dict) or data.get("status") is not True:
        raise PaymentVerificationUnavailable("We could not verify the payment yet. Please try again shortly.")
    verified = data.get("data")
    if not isinstance(verified, dict):
        raise PaymentVerificationUnavailable("The payment provider returned an incomplete verification.")
    if verified.get("reference") != safe_reference:
        raise ValidationError("The verified payment reference does not match this payment.")
    return verified


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
def post_order_to_ledger(order, actor=None):
    order = OnlineOrder.objects.select_for_update().select_related("branch", "customer").get(pk=order.pk)
    if order.sale_document_id:
        return order
    if order.payment_status != "paid":
        raise ValidationError("Only a verified paid order can enter the KOFAD sales ledger.")

    branch = core_services.lock_branch(order.branch)
    core_services.ensure_open(branch)
    actor = actor or _system_actor()

    lines = list(order.lines.select_related("product"))
    for item in lines:
        stock = Stock.objects.select_for_update().filter(branch=branch, product=item.product).first()
        available = stock.quantity if stock else 0
        reserved_by_others = active_reserved_units(branch, item.product, exclude_order=order)
        if item.base_units > max(available - reserved_by_others, 0):
            order.ledger_status = "attention"
            order.save(update_fields=["ledger_status", "updated_at"])
            raise ValidationError(
                f"{item.description} no longer has enough stock to post this paid order. "
                "Resolve the stock shortage before fulfilment."
            )

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
        note=(
            f"Online Market order {order.public_reference}. "
            f"Online payment verified at {order.paid_at.isoformat() if order.paid_at else 'verified time unavailable'}."
        ),
        created_by=actor,
        external_reference=order.payment_reference,
    )
    for item in lines:
        sale_line = Line.objects.create(
            document=doc, product=item.product, description=item.description, mode=item.mode,
            quantity=item.quantity, factor=item.factor, list_price=item.unit_price,
            unit_price=item.unit_price, discount_percent=0, unit_cost=item.unit_cost, total=item.total,
        )
        item.sale_line = sale_line
        item.save(update_fields=["sale_line"])
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
    Payment.objects.create(
        document=doc, method=_payment_method(order.payment_channel), amount=order.total,
        reference=order.payment_reference, direction=1,
    )
    order.party = party
    order.sale_document = doc
    order.ledger_status = "posted"
    order.save(update_fields=["party", "sale_document", "ledger_status", "updated_at"])
    order.reservations.update(active=False)
    core_services.audit(actor, branch, "sale.online_posted", order.public_reference, {
        "document": doc.reference,
        "payment_reference": order.payment_reference,
        "amount": str(order.total),
        "channel": order.payment_channel,
    })
    return order


@transaction.atomic
def finalize_payment(reference, provider_data, expected_provider="paystack"):
    attempt = MarketPaymentAttempt.objects.select_for_update().select_related("order").filter(reference=reference).first()
    if not attempt:
        raise ValidationError("This payment reference does not belong to a KOFAD order.")
    if attempt.provider != expected_provider:
        raise ValidationError("Payment provider does not match the saved attempt.")
    order = OnlineOrder.objects.select_for_update().get(pk=attempt.order_id)
    if order.payment_status == "paid":
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

    was_cancelled = order.status == "cancelled"
    channel = str(provider_data.get("channel", ""))[:40]
    now = timezone.now()
    order.status = "paid"
    order.payment_status = "paid"
    if not order.confirmed_reference:
        order.confirmed_reference = _confirmed_reference()
    order.payment_reference = reference
    order.payment_channel = channel
    order.paid_at = now
    order.ledger_status = "pending"
    order.save(update_fields=[
        "status", "payment_status", "confirmed_reference", "payment_reference", "payment_channel",
        "paid_at", "ledger_status", "updated_at",
    ])
    attempt.status = "success"
    attempt.verified_at = now
    attempt.save(update_fields=["status", "verified_at"])
    order.reservations.update(expires_at=now + timedelta(hours=24), active=True)
    OrderEvent.objects.create(
        order=order, status="paid", title="Payment confirmed",
        note="Payment verified successfully. Your order is confirmed for fulfilment.",
    )

    branch_closed = Closing.objects.filter(branch=order.branch, date=timezone.localdate()).exists()
    if branch_closed or was_cancelled:
        order.ledger_status = "attention"
        order.save(update_fields=["ledger_status", "updated_at"])
        OrderEvent.objects.create(
            order=order, status="paid", title="Awaiting ledger posting",
            note=("Payment arrived after cancellation. Review with the customer before fulfilment." if was_cancelled else "The shop day was already closed. KOFAD preserved the payment and stock hold for the next open business period."),
            customer_visible=False,
        )
    else:
        try:
            order = post_order_to_ledger(order)
        except ValidationError as exc:
            order.ledger_status = "attention"
            order.save(update_fields=["ledger_status", "updated_at"])
            OrderEvent.objects.create(
                order=order, status="paid", title="Fulfilment attention required",
                note=str(exc), customer_visible=False,
            )

    from .notifications import queue_order_sms
    queue_order_sms(
        order, "paid",
        f"KOFAD: Payment confirmed for {order.customer_reference}. "
        f"Amount: GHS {order.total:.2f}. Follow your order status in your account. "
        "Track it in your KOFAD Market account.",
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
    if action != "cancel_unpaid" and order.payment_status == "paid" and not order.sale_document_id:
        # A payment accepted after daily closing remains safely outside the closed
        # ledger until the next open period. Fulfilment cannot advance first.
        order = post_order_to_ledger(order, actor=user)
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
        order.dispatched_at = timezone.now()
    order.status = target
    if target in {"delivered", "picked_up"}:
        order.completed_at = timezone.now()
    if target == "cancelled":
        order.reservations.update(active=False)
    order.staff_note = cleaned.get("note", "").strip() or order.staff_note
    order.save(update_fields=[
        "status", "delivery_agent_name", "delivery_agent_phone", "dispatched_at",
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
        from .notifications import queue_order_sms
        queue_order_sms(order, target, f"KOFAD: {title} for {order.customer_reference}.{extra}")
    return order



def market_return_value(item):
    return sum(
        (row.order_line.unit_price * row.quantity for row in item.lines.select_related("order_line")),
        Decimal("0"),
    )


def _apply_refund_provider_state(item, data, message=""):
    status = str((data or {}).get("status", "")).strip().lower()[:32]
    refund_id = str((data or {}).get("id", "") or item.provider_refund_id)[:80]
    raw_amount = (data or {}).get("amount")
    if raw_amount is not None:
        try:
            item.refund_amount = Decimal(str(raw_amount)) / Decimal("100")
        except (ArithmeticError, ValueError):
            pass
    item.provider_refund_id = refund_id
    item.provider_refund_status = status
    item.provider_refund_message = str(message or "")[:240]
    if status == "processed":
        item.status = "completed"
        item.refund_processed_at = timezone.now()
        if not item.completed_at:
            item.completed_at = timezone.now()
    elif status in {"failed", "needs-attention"}:
        item.status = "refund_attention"
    else:
        item.status = "processing"
    item.save(update_fields=[
        "status", "refund_amount", "provider_refund_id", "provider_refund_status",
        "provider_refund_message", "refund_processed_at", "completed_at",
    ])
    return item


def initiate_paystack_refund(item):
    if MarketPaymentAttempt.objects.filter(order=item.order, reference=item.order.payment_reference, provider="hubtel").exists():
        raise ValidationError("Hubtel refund requires staff reconciliation through Hubtel. Do not submit it to Paystack.")
    item = MarketReturnRequest.objects.select_related(
        "order", "core_return_request"
    ).prefetch_related("lines__order_line").get(pk=item.pk)
    if item.provider_refund_id:
        return item
    if item.refund_initiated_at and item.provider_refund_status in {"submitting", "submission_unknown"}:
        raise ValidationError(
            "A previous Paystack refund submission has an unknown outcome. "
            "Reconcile it in Paystack before attempting another refund."
        )
    if not item.core_return_request_id or item.core_return_request.status != "approved":
        raise ValidationError("The KOFAD return must be posted before the payment refund can begin.")
    if not item.order.payment_reference:
        raise ValidationError("The original Paystack payment reference is missing.")
    if not settings.PAYSTACK_SECRET_KEY:
        raise ValidationError("Paystack refund processing is not configured yet.")

    amount = market_return_value(item)
    if amount <= 0:
        raise ValidationError("This return does not have a refundable amount.")
    if amount > item.order.total:
        raise ValidationError("The refund amount cannot exceed the original online order total.")

    payload = {
        "transaction": item.order.payment_reference,
        "amount": int(amount * 100),
        "currency": "GHS",
        "customer_note": item.reason[:240],
        "merchant_note": f"KOFAD {item.order.public_reference} return {item.pk}"[:240],
    }
    item.refund_amount = amount
    item.refund_initiated_at = timezone.now()
    item.provider_refund_status = "submitting"
    item.provider_refund_message = "KOFAD submitted the refund request to Paystack."
    item.save(update_fields=[
        "refund_amount", "refund_initiated_at",
        "provider_refund_status", "provider_refund_message",
    ])
    try:
        response = requests.post(
            PAYSTACK_REFUND,
            headers=_paystack_headers(),
            json=payload,
            timeout=settings.PAYSTACK_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        item.provider_refund_status = "submission_unknown"
        item.provider_refund_message = (
            "Paystack submission outcome is unknown because the network response was lost. "
            "Reconcile this refund before retrying."
        )
        item.save(update_fields=["provider_refund_status", "provider_refund_message"])
        raise ValidationError(item.provider_refund_message) from exc
    if not 200 <= response.status_code < 300 or not body.get("status"):
        item.provider_refund_status = "rejected"
        item.provider_refund_message = str(
            body.get("message") or "Paystack did not accept the refund request."
        )[:240]
        item.save(update_fields=["provider_refund_status", "provider_refund_message"])
        raise ValidationError(item.provider_refund_message)
    data = body.get("data") or {}
    _apply_refund_provider_state(item, data, body.get("message", ""))

    OrderEvent.objects.create(
        order=item.order,
        status="refund_started",
        title="Refund submitted",
        note=f"GHS {amount:.2f} was submitted to the original payment channel.",
        customer_visible=True,
    )
    core_services.audit(
        _system_actor(), item.order.branch, "sale.online_refund_started", item.order.public_reference,
        {
            "market_return": str(item.pk),
            "refund_amount": str(amount),
            "paystack_refund_id": item.provider_refund_id,
            "paystack_status": item.provider_refund_status,
        },
    )
    return item


def refresh_paystack_refund(item):
    if MarketPaymentAttempt.objects.filter(order=item.order, reference=item.order.payment_reference, provider="hubtel").exists():
        raise ValidationError("Hubtel refund requires staff reconciliation through Hubtel. Do not submit it to Paystack.")
    item = MarketReturnRequest.objects.select_related("order").get(pk=item.pk)
    if not item.provider_refund_id:
        raise ValidationError("No Paystack refund has been initiated for this return.")
    if not settings.PAYSTACK_SECRET_KEY:
        raise ValidationError("Paystack refund processing is not configured yet.")
    try:
        response = requests.get(
            f"{PAYSTACK_REFUND}/{item.provider_refund_id}",
            headers=_paystack_headers(),
            timeout=settings.PAYSTACK_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError("KOFAD could not refresh the Paystack refund status.") from exc
    if not 200 <= response.status_code < 300 or not body.get("status"):
        raise ValidationError(str(body.get("message") or "Paystack could not retrieve this refund.")[:240])
    item = _apply_refund_provider_state(item, body.get("data") or {}, body.get("message", ""))
    if item.provider_refund_status == "processed":
        send_transactional_sms(
            item.order.phone,
            f"KOFAD: Your GHS {item.refund_amount:.2f} refund for "
            f"{item.order.customer_reference} has been processed.",
        )
    return item


def apply_paystack_refund_webhook(event_name, data):
    refund_id = str((data or {}).get("id", ""))
    if not refund_id:
        return None
    item = MarketReturnRequest.objects.select_related("order").filter(
        provider_refund_id=refund_id
    ).first()
    if not item:
        return None
    previous_status = item.provider_refund_status
    item = _apply_refund_provider_state(item, data, event_name)
    if previous_status == item.provider_refund_status:
        return item
    OrderEvent.objects.create(
        order=item.order,
        status=event_name.replace(".", "_")[:32],
        title={
            "refund.pending": "Refund pending",
            "refund.processing": "Refund processing",
            "refund.processed": "Refund processed",
            "refund.failed": "Refund failed",
            "refund.needs-attention": "Refund needs attention",
        }.get(event_name, "Refund status updated"),
        note=(
            f"Paystack refund status: {item.provider_refund_status}."
            if item.provider_refund_status
            else "Paystack sent a refund status update."
        ),
        customer_visible=True,
    )
    if item.provider_refund_status == "processed":
        send_transactional_sms(
            item.order.phone,
            f"KOFAD: Your GHS {item.refund_amount:.2f} refund for "
            f"{item.order.public_reference} has been processed.",
        )
    return item


def eligible_market_return_quantity(order_line):
    reserved = MarketReturnRequestLine.objects.filter(
        order_line=order_line,
        request__status__in=["requested", "approved", "processing", "refund_attention", "completed"],
    ).aggregate(total=Sum("quantity"))["total"] or 0
    return max(int(order_line.quantity) - int(reserved), 0)


@transaction.atomic
def create_market_return_request(customer, order, line_payload, reason, resolution, evidence=None):
    order = OnlineOrder.objects.select_for_update().get(
        pk=order.pk, customer=customer
    )
    if order.payment_status != "paid" or order.status not in {"delivered", "picked_up"}:
        raise ValidationError("Returns can be requested only after a paid order has been handed over.")
    if not order.sale_document_id:
        raise ValidationError("This order has not reached the KOFAD sales ledger yet.")
    reason = str(reason or "").strip()
    if len(reason) < 10:
        raise ValidationError("Explain the reason for the return in a little more detail.")
    if resolution not in dict(MarketReturnRequest.RESOLUTIONS):
        raise ValidationError("Choose a valid resolution.")

    validated = []
    for raw in line_payload:
        order_line = OnlineOrderLine.objects.select_for_update().filter(
            pk=raw.get("line"), order=order
        ).first()
        if not order_line:
            raise ValidationError("One selected item does not belong to this order.")
        try:
            quantity = int(raw.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if quantity <= 0:
            continue
        eligible = eligible_market_return_quantity(order_line)
        if quantity > eligible:
            raise ValidationError(
                f"{order_line.description}: only {eligible} item(s) are currently eligible for return."
            )
        condition = str(raw.get("condition") or "sellable")
        if condition not in {"sellable", "damaged"}:
            raise ValidationError("Choose a valid item condition.")
        if not order_line.sale_line_id:
            raise ValidationError(
                f"{order_line.description} is not linked to its posted KOFAD sale line yet."
            )
        validated.append((order_line, quantity, condition))
    if not validated:
        raise ValidationError("Choose at least one item and quantity to return.")

    refund_amount = sum(
        (order_line.unit_price * quantity for order_line, quantity, _ in validated),
        Decimal("0"),
    )
    item = MarketReturnRequest.objects.create(
        order=order,
        customer=customer,
        resolution=resolution,
        reason=reason,
        refund_amount=refund_amount,
    )
    MarketReturnRequestLine.objects.bulk_create([
        MarketReturnRequestLine(
            request=item, order_line=order_line, quantity=quantity, condition=condition
        )
        for order_line, quantity, condition in validated
    ])
    if evidence:
        payload = prepare_support_attachment(evidence)
        MarketReturnAttachment.objects.create(request=item, **payload)

    OrderEvent.objects.create(
        order=order, status="return_requested", title="Return request submitted",
        note="KOFAD is reviewing your return request.",
    )
    send_transactional_sms(
        order.phone,
        f"KOFAD: Return request received for {order.customer_reference}. "
        "You can follow its status in My KOFAD.",
    )
    return item


@transaction.atomic
def review_market_return_request(user, item, action, note=""):
    from core import returns as return_service

    item = MarketReturnRequest.objects.select_for_update().select_related(
        "order", "order__branch"
    ).get(pk=item.pk)
    note = str(note or "").strip()[:1000]

    if action == "approve":
        if item.status != "requested":
            raise ValidationError("Only a new return request can be approved.")
        item.status = "approved"
        item.staff_note = note
        item.reviewed_by = user
        item.reviewed_at = timezone.now()
        item.save(update_fields=["status", "staff_note", "reviewed_by", "reviewed_at"])
        OrderEvent.objects.create(
            order=item.order, status="return_approved", title="Return approved",
            note=note or "KOFAD approved the return request. Follow staff instructions for handover.",
            actor=user,
        )
        send_transactional_sms(
            item.order.phone,
            f"KOFAD: Your return request for {item.order.customer_reference} was approved.",
        )
        return item

    if action == "reject":
        if item.status not in {"requested", "approved"}:
            raise ValidationError("This return request cannot be rejected now.")
        item.status = "rejected"
        item.staff_note = note
        item.reviewed_by = user
        item.reviewed_at = timezone.now()
        item.save(update_fields=["status", "staff_note", "reviewed_by", "reviewed_at"])
        OrderEvent.objects.create(
            order=item.order, status="return_rejected", title="Return request declined",
            note=note or "KOFAD could not approve this return request.", actor=user,
        )
        send_transactional_sms(
            item.order.phone,
            f"KOFAD: Your return request for {item.order.customer_reference} was reviewed. "
            "Open My KOFAD for the decision.",
        )
        return item

    if action in {"sync_refund", "refresh_refund"}:
        if not item.core_return_request_id or item.core_return_request.status != "approved":
            raise ValidationError("The KOFAD return must be approved and posted before refunding the payment.")
        try:
            if item.provider_refund_id:
                return refresh_paystack_refund(item)
            return initiate_paystack_refund(item)
        except ValidationError as exc:
            item.status = "refund_attention"
            item.provider_refund_message = str(exc)[:240]
            item.save(update_fields=["status", "provider_refund_message"])
            return item

    if action != "process":
        raise ValidationError("Choose a valid return action.")
    if item.status != "approved":
        raise ValidationError("Approve the customer request before processing the physical return.")

    payload = []
    for row in item.lines.select_related("order_line__sale_line"):
        if not row.order_line.sale_line_id:
            raise ValidationError("One return item is not linked to the original KOFAD sale line.")
        payload.append({
            "line": row.order_line.sale_line_id,
            "quantity": row.quantity,
            "disposition": "sellable" if row.condition == "sellable" else "quarantine",
        })

    refund_method = _payment_method(item.order.payment_channel)
    core_item, direct = return_service.create_customer_return(
        user,
        item.order.branch,
        item.order.sale_document,
        payload,
        item.reason,
        refund_method,
    )
    item.core_return_request = core_item
    item.status = "processing"
    item.staff_note = note or item.staff_note
    item.reviewed_by = user
    item.reviewed_at = timezone.now()
    item.refund_amount = market_return_value(item)
    if not direct and getattr(core_item, "status", "") != "approved":
        item.provider_refund_status = "awaiting_kofad_approval"
        item.provider_refund_message = (
            "The physical return is waiting for KOFAD approval before the payment refund begins."
        )
    item.save(update_fields=[
        "core_return_request", "status", "staff_note", "reviewed_by",
        "reviewed_at", "refund_amount", "provider_refund_status", "provider_refund_message",
    ])
    OrderEvent.objects.create(
        order=item.order,
        status="return_processing",
        title="Return entered into KOFAD returns",
        note=(
            "The return is waiting for KOFAD approval before the payment refund begins."
            if item.provider_refund_status == "awaiting_kofad_approval"
            else "The physical return was posted to KOFAD. Payment refund is starting."
        ),
        actor=user,
    )
    if direct or getattr(core_item, "status", "") == "approved":
        try:
            item = initiate_paystack_refund(item)
        except ValidationError as exc:
            item.status = "refund_attention"
            item.provider_refund_message = str(exc)[:240]
            item.save(update_fields=["status", "provider_refund_message"])
    return item


@transaction.atomic
def save_delivery_tracking(user, order, cleaned):
    order = OnlineOrder.objects.select_for_update().get(pk=order.pk)
    if order.fulfilment != "delivery":
        raise ValidationError("Delivery tracking is available only for delivery orders.")
    if order.status in {"cancelled", "refunded"}:
        raise ValidationError("This order is no longer active.")

    name = str(cleaned.get("delivery_agent_name") or "").strip()
    phone = str(cleaned.get("delivery_agent_phone") or "").strip()
    eta = cleaned.get("estimated_delivery_at")
    update_fields = []
    if name:
        order.delivery_agent_name = name
        update_fields.append("delivery_agent_name")
    if phone:
        order.delivery_agent_phone = phone
        update_fields.append("delivery_agent_phone")
    if eta:
        order.estimated_delivery_at = eta
        update_fields.append("estimated_delivery_at")
    if update_fields:
        update_fields.append("updated_at")
        order.save(update_fields=update_fields)

    update = DeliveryTrackingUpdate.objects.create(
        order=order,
        status=str(cleaned.get("status") or "").strip()[:40],
        note=str(cleaned.get("note") or "").strip()[:320],
        latitude=cleaned.get("latitude"),
        longitude=cleaned.get("longitude"),
        actor=user,
        customer_visible=bool(cleaned.get("customer_visible")),
    )
    if update.customer_visible:
        OrderEvent.objects.create(
            order=order,
            status="delivery_update",
            title=update.status or "Delivery update",
            note=update.note,
            actor=user,
            customer_visible=True,
        )
    core_services.audit(user, order.branch, "sale.online_delivery_tracking", order.public_reference, {
        "driver": order.delivery_agent_name,
        "eta": order.estimated_delivery_at.isoformat() if order.estimated_delivery_at else "",
        "tracking_status": update.status,
        "location": (
            f"{update.latitude},{update.longitude}"
            if update.latitude is not None and update.longitude is not None else ""
        ),
    })
    return update
