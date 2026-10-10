"""Download endpoints for separately distributed customer and staff native apps.

No install button is enabled until a real, approved, signed release location
is configured. This module never hands out privileged staff download metadata
on a public endpoint.
"""
import re
from urllib.parse import parse_qs, urlsplit

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


def approved_release_url(value, platform, kind=None):
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
        owned = hostname == "downloads.kofadimpex.com"
        if platform == "ios":
            return value.strip() if hostname == "apps.apple.com" and re.search(r"/id\d+(?:/)?$", path) else ""
        if platform == "android":
            identifier = parse_qs(parsed.query).get("id", [""])[0]
            expected = "com.kofadimpex." + ("market" if kind == "customer" else "staff") if kind else ""
            if hostname == "play.google.com" and path == "/store/apps/details" and (
                identifier == expected if expected else bool(identifier)
            ):
                return value.strip()
            if owned and kind in {"customer", "staff"} and path.startswith("/android/" + kind + "/") and path.lower().endswith(".apk") and not parsed.query:
                return value.strip()
            return ""
        if platform == "windows":
            if owned and kind in {"customer", "staff"} and path.startswith("/windows/" + kind + "/") and path.lower().endswith((".exe", ".msi", ".msix")) and not parsed.query:
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
        url = approved_release_url(value, platform, kind)
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


# The installed Capacitor app has its own local security origin, unlike a
# Django page. Version metadata is deliberately public and intentionally
# contains no direct staff installer link, staff account data or credentials.
NATIVE_LOCAL_ORIGINS = frozenset({
    "capacitor://localhost", "https://localhost", "http://localhost",
})


def _native_version_response(request, kind):
    app = app_metadata(kind)
    response = JsonResponse({
        "channel": kind,
        "version": app["version"],
        "platforms": {entry["id"]: entry["available"] for entry in app["platforms"]},
    })
    response["Cache-Control"] = "public, max-age=120"
    response["Vary"] = "Origin"
    response["X-Content-Type-Options"] = "nosniff"
    origin = request.headers.get("Origin", "").strip()
    if origin in NATIVE_LOCAL_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Methods"] = "GET"
    if kind == "staff":
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@require_GET
def customer_native_version(request):
    return _native_version_response(request, "customer")


@require_GET
def staff_native_version(request):
    return _native_version_response(request, "staff")
