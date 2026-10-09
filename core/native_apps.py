"""Download endpoints for separately distributed customer and staff native apps.

No install button is enabled until a real, approved, signed release location
is configured. This module never hands out privileged staff download metadata
on a public endpoint.
"""
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


PLATFORMS = (
    ("android", "Android", "Google Play / verified Android release"),
    ("ios", "iPhone & iPad", "Apple App Store"),
    ("windows", "Windows PC", "Signed Windows installer"),
)


def approved_release_url(value, platform):
    """Reject redirects, IPs, credentials, scheme downgrades and unknown stores."""
    if not isinstance(value, str) or not value or len(value) > 1200:
        return ""
    try:
        parsed = urlsplit(value.strip())
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            return ""
        if parsed.fragment or parsed.netloc.lower() != hostname:
            return ""
        path = parsed.path or ""
        owned = hostname == "kofadimpex.com" or hostname.endswith(".kofadimpex.com")
        if platform == "ios":
            return value.strip() if hostname == "apps.apple.com" and path.startswith("/") else ""
        if platform == "android":
            if hostname == "play.google.com" and path == "/store/apps/details" and "id=" in parsed.query:
                return value.strip()
            if owned and path.lower().endswith(".apk") and not parsed.query:
                return value.strip()
            return ""
        if platform == "windows":
            if owned and path.lower().endswith((".exe", ".msi", ".msix")) and not parsed.query:
                return value.strip()
            return ""
    except ValueError:
        return ""
    return ""


def app_metadata(kind):
    if kind not in {"customer", "staff"}:
        raise ValueError("Unsupported client type")
    prefix = f"KOFAD_{kind.upper()}_APP_"
    entries = []
    for platform, label, description in PLATFORMS:
        value = getattr(settings, prefix + platform.upper() + "_URL", "")
        url = approved_release_url(value, platform)
        entries.append({
            "id": platform, "label": label, "description": description,
            "url": url, "available": bool(url),
        })
    return {
        "kind": kind,
        "title": "KOFAD Market" if kind == "customer" else "KOFAD Staff",
        "description": (
            "A private shopping companion for orders, products and customer support."
            if kind == "customer" else
            "The dedicated business application for authorized KOFAD team members."
        ),
        "version": getattr(settings, prefix + "VERSION", "").strip() or "",
        "platforms": entries,
        "released": any(item["available"] for item in entries),
    }


@require_GET
def customer_downloads(request):
    from marketplace.views import _market_context
    app = app_metadata("customer")
    return render(request, "native_apps/customer.html", _market_context(
        request, title="Get the KOFAD Market app", app=app,
    ))


@require_GET
@login_required
def staff_downloads(request):
    # Keeping the page private also keeps staff distribution separate from customer SEO.
    if not request.user.is_active:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    app = app_metadata("staff")
    response = render(request, "native_apps/staff.html", {
        "title": "Get the KOFAD Staff app", "app": app,
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@require_GET
def customer_release_status(request):
    app = app_metadata("customer")
    response = JsonResponse({
        "channel": "customer",
        "version": app["version"],
        "platforms": {p["id"]: {"available": p["available"], "url": p["url"]}
                      for p in app["platforms"]},
    })
    response["Cache-Control"] = "public, max-age=300"
    return response


@require_GET
@login_required
def staff_release_status(request):
    if not request.user.is_active:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    app = app_metadata("staff")
    response = JsonResponse({
        "channel": "staff",
        "version": app["version"],
        "platforms": {p["id"]: {"available": p["available"], "url": p["url"]}
                      for p in app["platforms"]},
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response
