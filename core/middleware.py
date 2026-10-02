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
            if (request.user.is_staff or request.user.is_superuser or request.user.has_perm("core.manage_company") or access.totp_secret) and not request.session.get("mfa_ok"):
                if request.path not in ("/mfa/", "/logout/", "/health/"):
                    return redirect("mfa")
        response = self.get_response(request)
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.user.is_authenticated:
            response["Cache-Control"] = "no-store"
        return response
