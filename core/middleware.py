from django.conf import settings
from django.contrib.auth import logout
from django.utils import timezone
from django.shortcuts import redirect
from .models import Access


class AccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.path.startswith("/market/"):
            logout(request)
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
        response = self.get_response(request)
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self' https://unpkg.com; img-src 'self' data: https://images.unsplash.com https://*.tile.openstreetmap.org; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        geolocation_paths = ("/market/checkout/", "/market-settings/", "/online-orders/")
        geolocation = "(self)" if request.path.startswith(geolocation_paths) else "()"
        response["Permissions-Policy"] = f"camera=(), microphone=(), geolocation={geolocation}"
        if request.user.is_authenticated or request.session.get("market_customer_id"):
            response["Cache-Control"] = "no-store"
        return response
