"""KOFAD native OAuth-style authorization-code + S256 PKCE session gateway.

Public native clients never receive browser cookies, staff passwords, payment keys,
or unscoped data. Authorization is confirmed in the official OS browser using
existing KOFAD customer/staff sign-in and MFA. This is a server-side foundation;
the OS-browser callback and secure-storage client still require native QA.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.models import Access, Branch
from core.security import requires_mfa
from . import services
from .mobile_auth_models import MobileAuthorizationGrant, MobileDeviceSession
from .native_api import NATIVE_ORIGINS

# All callback destinations are static identifiers controlled by the signed app.
# No caller-supplied https redirect and no wildcards.
CLIENTS = {
    "customer": {"id": "kofad-market", "uri": "kofadmarket://auth/callback"},
    "staff": {"id": "kofad-staff", "uri": "kofadstaff://auth/callback"},
}
STATE = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
ACCESS_TTL = timedelta(minutes=15)
CUSTOMER_REFRESH_TTL = timedelta(days=14)
STAFF_REFRESH_TTL = timedelta(hours=12)
CODE_TTL = timedelta(minutes=2)


class NativeCallbackRedirect(HttpResponseRedirect):
    allowed_schemes = ["kofadmarket", "kofadstaff"]


def _digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _public_response(request, data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    response["Vary"] = "Origin"
    origin = request.headers.get("Origin", "")
    if origin in NATIVE_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response["Access-Control-Max-Age"] = "300"
    return response


def _preflight(request):
    response = HttpResponse(status=204)
    response["Cache-Control"] = "no-store"
    response["Vary"] = "Origin"
    origin = request.headers.get("Origin", "")
    if origin in NATIVE_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response["Access-Control-Max-Age"] = "300"
    return response


def _request_allowed(request):
    return not request.headers.get("Origin") or request.headers["Origin"] in NATIVE_ORIGINS


def _json_body(request):
    if not request.content_type.startswith("application/json") or len(request.body) > 4096:
        return None
    try:
        value = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _staff_principal(user, session):
    """Validate employee, session version and MFA before allowing branch selection."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    access = Access.objects.filter(user=user).first()
    if not access or access.force_password_change:
        return None
    if session is None or session.get("access_version") != access.session_version:
        return None
    mfa_at = None
    if settings.PRIVILEGED_MFA_ENFORCED and requires_mfa(user):
        try:
            mfa_at = float(session.get("mfa_verified_at"))
        except (TypeError, ValueError):
            return None
        if mfa_at + settings.MFA_SESSION_SECONDS <= timezone.now().timestamp():
            return None
    return access, mfa_at


def _staff_branches(user):
    assigned = Branch.objects.filter(active=True)
    return assigned if user.is_superuser else assigned.filter(access__user=user)


def _staff_context(user, session):
    principal = _staff_principal(user, session)
    if not principal:
        return None
    # Every native staff grant is explicitly bound to the selected, assigned
    # branch; the app may not pick a default when this is missing.
    current = session.get("branch")
    branch = _staff_branches(user).filter(pk=current).first() if current else None
    return (*principal[:1], branch, principal[1]) if branch else None


def _valid_device(session, channel):
    now = timezone.now()
    if not session or session.channel != channel or session.revoked_at or session.refresh_expires_at <= now:
        return False
    if channel == "customer":
        customer = session.customer
        if not customer or not customer.active:
            return False
        return hmac.compare_digest(
            session.customer_credential_stamp,
            services.customer_credential_stamp(customer),
        )
    user = session.staff_user
    if not user or not user.is_active:
        return False
    access = Access.objects.filter(user=user).first()
    if not access or access.force_password_change or access.session_version != session.staff_access_version:
        return False
    branch = session.branch
    if not branch or not branch.active:
        return False
    if not user.is_superuser and not access.branches.filter(pk=branch.pk).exists():
        return False
    if settings.PRIVILEGED_MFA_ENFORCED and requires_mfa(user):
        if session.staff_mfa_at is None:
            return False
        if session.staff_mfa_at + settings.MFA_SESSION_SECONDS <= now.timestamp():
            return False
    return True


def _bearer_session(request, channel):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[7:]
    if len(token) != 43 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    session = MobileDeviceSession.objects.select_related(
        "customer", "staff_user", "branch"
    ).filter(access_hash=_digest(token), channel=channel, access_expires_at__gt=timezone.now()).first()
    return session if _valid_device(session, channel) else None


@require_http_methods(["GET"])
def capabilities(request, channel):
    """Anonymous, non-sensitive feature detection for the signed native clients."""
    client = CLIENTS[channel]
    return _public_response(request, {
        "version": 1,
        "channel": channel,
        "native_mobile_token_login": bool(getattr(settings, "KOFAD_NATIVE_AUTH_ENABLED", False)),
        "client_id": client["id"],
        "redirect_uri": client["uri"],
    })


@require_http_methods(["GET", "POST"])
def authorize(request, channel):
    """Existing browser login and form CSRF precede the one-time app callback."""
    if not getattr(settings, "KOFAD_NATIVE_AUTH_ENABLED", False):
        return HttpResponse(status=404)
    client = CLIENTS[channel]
    params = request.GET if request.method == "GET" else request.POST
    challenge = params.get("code_challenge", "")
    state = params.get("state", "")
    redirect_uri = params.get("redirect_uri", "")
    client_id = params.get("client_id", "")
    if not (
        CHALLENGE.fullmatch(challenge) and STATE.fullmatch(state)
        and redirect_uri == client["uri"] and client_id == client["id"]
        and params.get("code_challenge_method") == "S256"
    ):
        return HttpResponse("Invalid KOFAD app authorization request.", status=400)
    customer = services.customer_from_session(request) if channel == "customer" else None
    if channel == "staff" and request.method == "POST" and params.get("branch"):
        # CSRF remains enforced on this ordinary browser form submission.
        principal = _staff_principal(request.user, request.session)
        if not principal:
            return HttpResponse("Complete staff authentication before selecting a branch.", status=403)
        branch_id = str(params.get("branch", ""))
        if not branch_id.isascii() or not branch_id.isdecimal() or len(branch_id) > 12:
            return HttpResponse("Invalid branch selection.", status=403)
        chosen = _staff_branches(request.user).filter(pk=int(branch_id)).first()
        if not chosen:
            return HttpResponse("That branch is not assigned to your account.", status=403)
        request.session["branch"] = chosen.pk
    staff = _staff_context(request.user, request.session) if channel == "staff" else None
    if channel == "customer" and customer is None:
        if request.method != "GET":
            return HttpResponse("Sign in before authorizing.", status=401)
        return redirect("/market/access/?" + urlencode({"next": request.get_full_path()}))
    if channel == "staff" and staff is None:
        principal = _staff_principal(request.user, request.session)
        if principal and request.method == "GET":
            # The verified employee chooses their branch on the consent page.
            # Nothing is authorized until a CSRF-protected POST binds the branch.
            response = render(request, "marketplace/mobile_native_authorize.html", {
                "channel": channel,
                "app_name": "KOFAD Staff",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "challenge": challenge,
                "select_branch": True,
                "branches": _staff_branches(request.user).order_by("name"),
            })
            response["Cache-Control"] = "no-store"
            return response
        if request.method != "GET":
            return HttpResponse("Complete staff authentication and branch selection first.", status=403)
        request.session["staff_mobile_auth_return"] = request.get_full_path()
        if getattr(request.user, "is_authenticated", False):
            if settings.PRIVILEGED_MFA_ENFORCED and requires_mfa(request.user):
                return redirect("mfa")
            return redirect("dashboard")
        return redirect(settings.STAFF_LOGIN_PATH + "?" + urlencode({"next": request.get_full_path()}))

    if request.method == "POST":
        raw = secrets.token_urlsafe(32)
        data = {
            "code_hash": _digest(raw), "channel": channel,
            "code_challenge": challenge, "client_id": client_id,
            "redirect_uri": redirect_uri, "expires_at": timezone.now() + CODE_TTL,
        }
        if customer:
            data.update(customer=customer, customer_credential_stamp=services.customer_credential_stamp(customer))
        else:
            access, branch, mfa_at = staff
            data.update(staff_user=request.user, branch=branch,
                        staff_access_version=access.session_version, staff_mfa_at=mfa_at)
        MobileAuthorizationGrant.objects.create(**data)
        response = NativeCallbackRedirect(
            redirect_uri + "?" + urlencode({"code": raw, "state": state})
        )
        response["Cache-Control"] = "no-store"
        return response

    response = render(request, "marketplace/mobile_native_authorize.html", {
        "channel": channel,
        "app_name": "KOFAD Market" if channel == "customer" else "KOFAD Staff",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "challenge": challenge,
    })
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def token(request, channel):
    if not getattr(settings, "KOFAD_NATIVE_AUTH_ENABLED", False):
        return HttpResponse(status=404)
    if request.method == "OPTIONS":
        return _preflight(request)
    if not _request_allowed(request):
        return _public_response(request, {"error": "origin_not_allowed"}, 403)
    data = _json_body(request)
    if data is None:
        return _public_response(request, {"error": "invalid_request"}, 400)
    kind = data.get("grant_type")
    client = CLIENTS[channel]
    if data.get("client_id") != client["id"]:
        return _public_response(request, {"error": "invalid_client"}, 400)
    now = timezone.now()
    if kind == "authorization_code":
        code = data.get("code", "")
        verifier = data.get("code_verifier", "")
        if not (
            isinstance(code, str) and isinstance(verifier, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{43}", code)
            and VERIFIER.fullmatch(verifier)
            and data.get("redirect_uri") == client["uri"]
        ):
            return _public_response(request, {"error": "invalid_grant"}, 400)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        with transaction.atomic():
            grant = MobileAuthorizationGrant.objects.select_for_update().filter(code_hash=_digest(code), channel=channel).first()
            if (not grant or grant.consumed_at or grant.expires_at <= now
                or grant.redirect_uri != client["uri"]
                or grant.client_id != client["id"]
                or not hmac.compare_digest(grant.code_challenge, challenge)):
                return _public_response(request, {"error": "invalid_grant"}, 400)
            grant.consumed_at = now
            grant.save(update_fields=["consumed_at"])
            access = secrets.token_urlsafe(32)
            refresh = secrets.token_urlsafe(32)
            ttl = STAFF_REFRESH_TTL if channel == "staff" else CUSTOMER_REFRESH_TTL
            session = MobileDeviceSession.objects.create(
                channel=channel, customer=grant.customer, staff_user=grant.staff_user, branch=grant.branch,
                customer_credential_stamp=grant.customer_credential_stamp,
                staff_access_version=grant.staff_access_version,
                staff_mfa_at=grant.staff_mfa_at,
                access_hash=_digest(access), refresh_hash=_digest(refresh),
                access_expires_at=now + ACCESS_TTL, refresh_expires_at=now + ttl,
            )
            if not _valid_device(session, channel):
                session.revoked_at = now
                session.save(update_fields=["revoked_at"])
                return _public_response(request, {"error": "reauthentication_required"}, 403)
        return _public_response(request, {
            "token_type": "Bearer", "access_token": access,
            "refresh_token": refresh, "expires_in": int(ACCESS_TTL.total_seconds()),
            "refresh_expires_in": int(ttl.total_seconds()), "channel": channel,
        })
    if kind == "refresh_token":
        provided = data.get("refresh_token", "")
        if not isinstance(provided, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", provided):
            return _public_response(request, {"error": "invalid_grant"}, 400)
        with transaction.atomic():
            session = MobileDeviceSession.objects.select_for_update().filter(channel=channel, refresh_hash=_digest(provided)).first()
            if not _valid_device(session, channel):
                return _public_response(request, {"error": "invalid_grant"}, 400)
            access = secrets.token_urlsafe(32)
            refresh = secrets.token_urlsafe(32)
            session.access_hash = _digest(access)
            session.refresh_hash = _digest(refresh)
            session.access_expires_at = now + ACCESS_TTL
            session.last_rotated_at = now
            session.save(update_fields=["access_hash", "refresh_hash", "access_expires_at", "last_rotated_at"])
        return _public_response(request, {
            "token_type": "Bearer", "access_token": access, "refresh_token": refresh,
            "expires_in": int(ACCESS_TTL.total_seconds()), "channel": channel,
        })
    return _public_response(request, {"error": "unsupported_grant_type"}, 400)


@csrf_exempt
@require_http_methods(["GET", "OPTIONS"])
def me(request, channel):
    if not getattr(settings, "KOFAD_NATIVE_AUTH_ENABLED", False):
        return HttpResponse(status=404)
    if request.method == "OPTIONS":
        return _preflight(request)
    if not _request_allowed(request):
        return _public_response(request, {"error": "origin_not_allowed"}, 403)
    session = _bearer_session(request, channel)
    if not session:
        return _public_response(request, {"error": "authentication_required"}, 401)
    if channel == "customer":
        return _public_response(request, {
            "channel": "customer", "id": session.customer_id,
            "display_name": session.customer.full_name,
            "email": session.customer.email,
        })
    access = Access.objects.filter(user=session.staff_user).first()
    return _public_response(request, {
        "channel": "staff", "id": session.staff_user_id,
        "display_name": session.staff_user.get_full_name() or session.staff_user.username,
        "branch": {"id": session.branch_id, "name": session.branch.name},
        "permissions": {
            p: session.staff_user.has_perm("core." + p)
            for p in ("operate_sales", "operate_inventory", "operate_finance",
                      "approve_operations", "view_reports", "manage_company")
        },
        "access_version": access.session_version,
    })


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def revoke(request, channel):
    if not getattr(settings, "KOFAD_NATIVE_AUTH_ENABLED", False):
        return HttpResponse(status=404)
    if request.method == "OPTIONS":
        return _preflight(request)
    if not _request_allowed(request):
        return _public_response(request, {"error": "origin_not_allowed"}, 403)
    session = _bearer_session(request, channel)
    if session is None:
        return _public_response(request, {"error": "authentication_required"}, 401)
    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at"])
    from .mobile_push_models import MobilePushSubscription
    MobilePushSubscription.objects.filter(device_session=session).delete()
    return _public_response(request, {"revoked": True})
