from django.contrib.auth import logout
from django.shortcuts import redirect
from .models import Access


class AccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            access, _ = Access.objects.get_or_create(user=request.user)
            if request.session.get("access_version") != access.session_version:
                logout(request)
                return redirect("login")
            if access.force_password_change and request.path not in ("/account/password/", "/logout/"):
                return redirect("password_change")
        response = self.get_response(request)
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://images.unsplash.com; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        geolocation = "(self)" if request.path.startswith("/market/checkout/") else "()"
        response["Permissions-Policy"] = f"camera=(), microphone=(), geolocation={geolocation}"
        if request.user.is_authenticated or request.session.get("market_customer_id"):
            response["Cache-Control"] = "no-store"
        return response
