"""Google OpenID Connect linking/sign-in for existing staff and customer accounts.

Uses authorization code + PKCE + state + nonce and verifies the Google-signed
ID token against Google's published JWK keys. No persistent Google tokens.
Never creates staff users or guesses owners from unverified profile emails.
"""
import base64
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives import hashes
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from core.email_identity import normalize_email
from core.models import Access
from marketplace.models import CustomerAccount, EmailIdentity, GoogleIdentity
from marketplace import services as market_services

logger = logging.getLogger(__name__)
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
SESSION_KEY = "kofad_google_oauth_pending"


def enabled():
    return bool(
        getattr(settings, "KOFAD_GOOGLE_OAUTH_ENABLED", False)
        and settings.KOFAD_GOOGLE_CLIENT_ID and settings.KOFAD_GOOGLE_CLIENT_SECRET
    )


def _redirect_uri(kind):
    if kind == "staff":
        return settings.KOFAD_STAFF_SITE_ORIGIN + reverse("google_staff_callback")
    if kind == "customer":
        return settings.KOFAD_MARKET_SITE_ORIGIN + reverse("google_customer_callback")
    raise ValidationError("Invalid account type.")


def _parse_b64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _validated_google_claims(jwt, nonce):
    """Verify RS256, kid, issuer, audience, nonce, expiry and verified email."""
    try:
        if len(jwt) > 12000 or jwt.count(".") != 2:
            raise ValueError("Invalid token shape")
        header_data, body_data, signature_data = jwt.split(".")
        header = json.loads(_parse_b64(header_data))
        claims = json.loads(_parse_b64(body_data))
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise ValueError("Unexpected token structure")
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("Unsupported token algorithm")
        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("Invalid Google token issuer")
        if claims.get("aud") != settings.KOFAD_GOOGLE_CLIENT_ID:
            raise ValueError("Invalid token audience")
        if claims.get("azp") and claims["azp"] != settings.KOFAD_GOOGLE_CLIENT_ID:
            raise ValueError("Invalid authorized party")
        now = time.time()
        if float(claims.get("exp", 0)) <= now or float(claims.get("iat", 0)) > now + 90:
            raise ValueError("Expired or future-issued token")
        if not claims.get("iat") or not constant_time_compare(str(claims.get("nonce", "")), nonce):
            raise ValueError("Invalid token nonce")
        if claims.get("email_verified") is not True:
            raise ValueError("Google mailbox is not verified")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not (5 <= len(subject) <= 255):
            raise ValueError("Invalid Google account subject")
        email = normalize_email(claims.get("email", ""))

        jwks = cache.get("kofad-google-oidc-jwks-v1")
        if not jwks:
            response = requests.get(JWKS_URL, timeout=8, allow_redirects=False)
            response.raise_for_status()
            jwks = response.json()
            if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
                raise ValueError("Invalid Google public key data")
            cache.set("kofad-google-oidc-jwks-v1", jwks, timeout=1800)
        matched = next(
            (key for key in jwks["keys"]
             if key.get("kid") == header["kid"] and key.get("kty") == "RSA"
             and key.get("use", "sig") == "sig"),
            None,
        )
        if not matched:
            raise ValueError("Unrecognized Google signing key")
        public_key = rsa.RSAPublicNumbers(
            int.from_bytes(_parse_b64(matched["e"]), "big"),
            int.from_bytes(_parse_b64(matched["n"]), "big"),
        ).public_key()
        public_key.verify(
            _parse_b64(signature_data),
            (header_data + "." + body_data).encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return {"subject": subject, "email": email}
    except (ValueError, TypeError, KeyError, AttributeError, InvalidSignature, requests.RequestException) as exc:
        raise ValidationError("Google sign-in could not be verified. Please try again.") from exc


def _redirect(kind, success=False):
    if kind == "customer":
        return redirect("market_account" if success else "market_login")
    return redirect("dashboard" if success else "login")


def _current_owner(request, kind):
    if kind == "staff":
        if not request.user.is_authenticated or not request.user.is_active:
            return None
        return request.user
    if kind == "customer":
        return market_services.customer_from_session(request)
    return None


@never_cache
@sensitive_post_parameters("current_password")
def start(request, kind, mode):
    if kind not in {"staff", "customer"} or mode not in {"login", "link"}:
        return _redirect("staff")
    if not enabled():
        messages.error(request, "Google sign-in is being connected. Please use your existing sign-in method.")
        return _redirect(kind)
    if mode == "link":
        # Linking a second login method is a privileged action: require current
        # password and existing account session. The POST is CSRF protected.
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        owner = _current_owner(request, kind)
        if owner is None or not owner.check_password(request.POST.get("current_password", "")):
            messages.error(request, "Enter your current account password to link Google.")
            return _redirect(kind, success=bool(owner))
    else:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        owner = None

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    request.session[SESSION_KEY] = {
        "kind": kind, "mode": mode, "state": state, "nonce": nonce,
        "verifier": verifier, "owner_id": owner.pk if owner else None,
        "created_at": time.time(),
    }
    params = {
        "client_id": settings.KOFAD_GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(kind),
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    response = redirect(AUTHORIZE_URL + "?" + urlencode(params))
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
def callback(request, kind):
    if kind not in {"staff", "customer"}:
        return _redirect("staff")
    pending = request.session.pop(SESSION_KEY, None)
    if not pending or pending.get("kind") != kind or not enabled():
        messages.error(request, "Google sign-in request expired. Please try again.")
        return _redirect(kind)
    state = request.GET.get("state", "")
    if (
        not state or not constant_time_compare(state, pending.get("state", ""))
        or time.time() - pending.get("created_at", 0) > 600
        or pending.get("mode") not in {"login", "link"}
    ):
        messages.error(request, "Google sign-in request expired. Please try again.")
        return _redirect(kind)
    code = request.GET.get("code", "")
    if request.GET.get("error") or not code or len(code) > 4096:
        messages.error(request, "Google sign-in was cancelled or could not be completed.")
        return _redirect(kind)
    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "code": code, "client_id": settings.KOFAD_GOOGLE_CLIENT_ID,
                "client_secret": settings.KOFAD_GOOGLE_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(kind),
                "code_verifier": pending["verifier"],
                "grant_type": "authorization_code",
            },
            timeout=12,
            allow_redirects=False,
        )
        response.raise_for_status()
        provider_response = response.json()
        if not isinstance(provider_response, dict):
            raise ValidationError("Invalid Google token response.")
        identity = _validated_google_claims(
            provider_response.get("id_token", ""), pending["nonce"]
        )
        if pending["mode"] == "link":
            owner = _current_owner(request, kind)
            if owner is None or owner.pk != pending["owner_id"]:
                raise ValidationError("Your account session changed. Please link Google again.")
            try:
                with transaction.atomic():
                    binding = GoogleIdentity.objects.select_for_update().filter(
                        kind=kind, owner_id=owner.pk
                    ).first()
                    if GoogleIdentity.objects.filter(
                        kind=kind, subject=identity["subject"]
                    ).exclude(owner_id=owner.pk).exists():
                        raise ValidationError("This Google account is already linked.")
                    if binding:
                        binding.subject = identity["subject"]
                        binding.email = identity["email"]
                        binding.save(update_fields=["subject", "email"])
                    else:
                        GoogleIdentity.objects.create(
                            kind=kind, owner_id=owner.pk,
                            subject=identity["subject"], email=identity["email"],
                        )
                    # A verified Google mailbox may also become the email/password
                    # login alias, but never steal an alias from another account.
                    existing = EmailIdentity.objects.select_for_update().filter(
                        kind=kind, owner_id=owner.pk,
                    ).first()
                    if not existing or not existing.verified_at:
                        if not EmailIdentity.objects.filter(
                            kind=kind, email=identity["email"],
                            verified_at__isnull=False,
                        ).exclude(owner_id=owner.pk).exists():
                            alias, _ = EmailIdentity.objects.get_or_create(
                                kind=kind, owner_id=owner.pk
                            )
                            alias.email = identity["email"]
                            alias.verified_at = timezone.now()
                            alias.pending_email = ""
                            alias.code_digest = ""
                            alias.expires_at = None
                            alias.save()
                messages.success(request, "Google account linked. Your phone sign-in remains available.")
                return _redirect(kind, success=True)
            except IntegrityError as exc:
                raise ValidationError("This Google account is already linked to another account.") from exc

        binding = GoogleIdentity.objects.filter(
            kind=kind, subject=identity["subject"]
        ).first()
        if not binding:
            raise ValidationError(
                "This Google account is not linked yet. Sign in with your existing "
                "KOFAD credentials and link Google in Account Security."
            )
        if kind == "staff":
            user = User.objects.filter(pk=binding.owner_id, is_active=True).first()
            if not user:
                raise ValidationError("Staff account is unavailable.")
            access, _ = Access.objects.get_or_create(user=user)
            auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            market_services.clear_customer_session(request)
            request.session["access_version"] = access.session_version
            request.session["staff_session_expires_at"] = (
                timezone.now().timestamp() + settings.STAFF_SESSION_SECONDS
            )
            request.session.set_expiry(settings.SESSION_COOKIE_AGE)
            request.session.pop("enroll_secret", None)
            request.session.pop("mfa_verified_at", None)
            from core.services import audit
            audit(user, None, "session.login_google", user.pk)
            binding.last_login_at = timezone.now()
            binding.email = identity["email"]
            binding.save(update_fields=["last_login_at", "email"])
            if access.force_password_change:
                return redirect("password_change")
            from core.security import requires_mfa
            if settings.PRIVILEGED_MFA_ENFORCED and requires_mfa(user):
                return redirect("mfa")
            return redirect("dashboard")
        customer = CustomerAccount.objects.filter(
            pk=binding.owner_id, active=True, verified_at__isnull=False,
        ).first()
        if customer is None:
            raise ValidationError("Customer account is unavailable. Verify your phone first.")
        market_services.set_customer_session(request, customer)
        binding.last_login_at = timezone.now()
        binding.email = identity["email"]
        binding.save(update_fields=["last_login_at", "email"])
        return redirect(request.session.pop("market_after_login", None) or "market_account")
    except (requests.RequestException, ValueError, KeyError, ValidationError) as exc:
        # Never disclose tokens, authorization code or credentials to logs/UI.
        logger.warning("Google OAuth sign-in failed for kind=%s", kind)
        if isinstance(exc, ValidationError):
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Google could not verify this sign-in. Please try again.")
        return _redirect(kind)


@never_cache
@sensitive_post_parameters("current_password")
def unlink(request, kind):
    """Revoke a Google login binding; existing phone/password credentials stay."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if kind not in {"staff", "customer"}:
        return _redirect("staff")
    owner = _current_owner(request, kind)
    if owner is None or not owner.check_password(request.POST.get("current_password", "")):
        messages.error(request, "Enter your current password to disconnect Google.")
        return _redirect(kind, success=bool(owner))
    GoogleIdentity.objects.filter(kind=kind, owner_id=owner.pk).delete()
    messages.success(request, "Google sign-in disconnected. Your current phone and password still work.")
    return _redirect(kind, success=True)
