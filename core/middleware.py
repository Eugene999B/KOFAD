from django.conf import settings
from django.contrib.auth import logout
from django.utils import timezone
from django.shortcuts import redirect
from django.http import HttpResponse
from .models import Access
from .security import requires_mfa


class RequestSizeLimitMiddleware:
    """Reject obviously oversized requests before Django materializes request.body."""
    WEBHOOK_LIMITS = {
        "/market/payments/paystack/webhook/": 65536,
        "/market/payments/hubtel/callback/": 131072,
        "/whatsapp/webhook/": 524288,
        "/sms/delivery/": 131072,
        "/settings/backup/": 110 * 1024 * 1024,
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw_length = request.META.get("CONTENT_LENGTH")
        if raw_length:
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                length = -1
            limit = self.WEBHOOK_LIMITS.get(request.path, settings.DATA_UPLOAD_MAX_MEMORY_SIZE)
            if request.path == "/whatsapp/webhook/":
                limit = min(limit, settings.WHATSAPP_WEBHOOK_MAX_BYTES)
            if length < 0 or length > limit:
                response = HttpResponse("Request too large.", status=413, content_type="text/plain")
                response["Cache-Control"] = "no-store"
                return response
        return self.get_response(request)


class AccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = timezone.now().timestamp()
            staff_expires_at = request.session.get("staff_session_expires_at")
            if staff_expires_at is None:
                request.session["staff_session_expires_at"] = now + settings.STAFF_SESSION_SECONDS
            else:
                try:
                    expired = float(staff_expires_at) <= now
                except (TypeError, ValueError):
                    expired = True
                if expired:
                    logout(request)
                    return redirect("login")
            access, _ = Access.objects.get_or_create(user=request.user)
            if request.session.get("access_version") != access.session_version:
                logout(request)
                return redirect("login")
            if access.force_password_change and request.path not in ("/account/password/", "/logout/"):
                return redirect("password_change")
            if settings.PRIVILEGED_MFA_ENFORCED and requires_mfa(request.user):
                raw_verified = request.session.get("mfa_verified_at")
                try:
                    mfa_valid = float(raw_verified) + settings.MFA_SESSION_SECONDS > now
                except (TypeError, ValueError):
                    mfa_valid = False
                if not mfa_valid and request.path not in ("/mfa/", "/logout/", "/account/password/", "/session/state/"):
                    return redirect("mfa")
        response = self.get_response(request)
        geolocation_paths = ("/market/checkout/", "/market-settings/", "/online-orders/")
        google_map_page = bool(
            settings.GOOGLE_MAPS_BROWSER_KEY
            and settings.GOOGLE_MAPS_BROWSER_KEY_RESTRICTED
            and request.path.startswith(geolocation_paths)
        )
        if google_map_page:
            response["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://*.googleapis.com https://*.gstatic.com *.google.com https://*.ggpht.com https://*.googleusercontent.com blob:; "
                "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
                "img-src 'self' data: blob: https://images.unsplash.com https://tile.openstreetmap.org https://*.googleapis.com https://*.gstatic.com *.google.com https://*.ggpht.com https://*.googleusercontent.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "connect-src 'self' data: blob: https://*.googleapis.com *.google.com https://*.gstatic.com; "
                "frame-src *.google.com; worker-src blob:; "
                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            )
        else:
            response["Content-Security-Policy"] = "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self' https://unpkg.com; img-src 'self' data: https://images.unsplash.com https://tile.openstreetmap.org; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        geolocation = "(self)" if request.path.startswith(geolocation_paths) else "()"
        response["Permissions-Policy"] = f"camera=(), microphone=(), geolocation={geolocation}"
        if request.user.is_authenticated or request.session.get("market_customer_id"):
            response["Cache-Control"] = "no-store"
        return response
