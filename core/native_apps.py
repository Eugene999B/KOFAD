"""Download endpoints for separately distributed customer and staff native apps.

No install button is enabled until a real, approved, signed release location
is configured. This module never hands out privileged staff download metadata
on a public endpoint.
"""
import re
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone
from .mobile_release_models import MobileReleasePolicy, MobileNotice, SEMVER
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
            # Accept only version-matched customer APKs from KOFAD's public
            # release repository, never arbitrary GitHub attachments or forks.
            if hostname == "github.com" and kind == "customer" and not parsed.query:
                match = re.fullmatch(
                    r"/Eugene999B/KOFAD/releases/download/"
                    r"kofad-market-android-v(?P<v>(?:0|[1-9]\d{0,2})\."
                    r"(?:0|[1-9]\d{0,2})\.(?:0|[1-9]\d{0,3}))/"
                    r"KOFAD-customer-(?P=v)-android\.apk",
                    path,
                )
                if match:
                    return value.strip()
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
    policy = MobileReleasePolicy.objects.filter(channel=kind).first()
    minimum = ""
    reason = ""
    # Fail safely: mandatory updates are NEVER advertised without a verified,
    # published Android release that is at least the required minimum version.
    published_android = next((p for p in app["platforms"] if p["id"] == "android"), None)
    if policy and published_android and published_android["available"]:
        latest = app["version"]
        expected = policy.minimum_android_version
        if SEMVER.fullmatch(latest) and SEMVER.fullmatch(expected or ""):
            v_latest = tuple(map(int, latest.split(".")))
            v_min = tuple(map(int, expected.split(".")))
            if v_min <= v_latest:
                minimum = expected
                reason = policy.critical_update_reason
    live = timezone.now()
    notices = MobileNotice.objects.filter(
        channel=kind, enabled=True, created_at__lte=live
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=live))[:6]
    response = JsonResponse({
        "channel": kind,
        "version": app["version"],
        "platforms": {entry["id"]: entry["available"] for entry in app["platforms"]},
        "android_policy": {"minimum_version": minimum, "reason": reason},
        "notices": [
            {"id": item.pk, "title": item.title, "message": item.message,
             "priority": item.priority, "created_at": item.created_at.isoformat()}
            for item in notices
        ],
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

@require_GET
@login_required
def mobile_operations_dashboard(request):
    """Superuser-only monitoring and control entry point; no signing keys."""
    if not request.user.is_active or not request.user.is_superuser:
        raise PermissionDenied
    policies = {p.channel: p for p in MobileReleasePolicy.objects.all()}
    active_counts = {
        channel: MobileNotice.objects.filter(channel=channel, enabled=True).count()
        for channel in ("customer", "staff")
    }
    response = render(request, "native_apps/operations.html", {
        "title": "KOFAD Mobile App Control",
        "customer_app": app_metadata("customer"),
        "staff_app": app_metadata("staff"),
        "policies": policies,
        "notice_counts": active_counts,
        "admin_prefix": settings.STAFF_LOGIN_SLUG,
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response
