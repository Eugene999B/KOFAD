"""Business Gmail API sender over HTTPS for Railway Hobby.

Gmail consent is administrator-only, uses offline OAuth and stores an encrypted
refresh token. Staff/customer Google sign-in remains a separate OAuth client.
"""
import base64
import hashlib
import hmac
import logging
import secrets
import time
from email.message import EmailMessage
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from marketplace.models import GmailSenderConnection
from core import google_oauth
from core.services import audit

logger = logging.getLogger(__name__)
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"
SESSION_KEY = "kofad_gmail_sender_oauth_pending"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def configured():
    return bool(
        getattr(settings, "KOFAD_GMAIL_API_ENABLED", False)
        and settings.KOFAD_GMAIL_CLIENT_ID and settings.KOFAD_GMAIL_CLIENT_SECRET
    )


def ready():
    return configured() and GmailSenderConnection.objects.filter(pk=1).exists()


def connection():
    return GmailSenderConnection.objects.filter(pk=1).first()


def _cipher():
    derived = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        b"kofad:gmail-api-refresh-token:v1", hashlib.sha256,
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _callback_uri():
    return settings.KOFAD_STAFF_SITE_ORIGIN + "/auth/google/gmail/callback/"


def _permitted(request):
    user = request.user
    return user.is_authenticated and user.is_active and (
        user.is_superuser or user.has_perm("core.manage_company")
    )


@never_cache
@sensitive_post_parameters("current_password")
def connect_start(request):
    if request.method != "POST":
        return redirect("account")
    if not configured():
        messages.error(request, "Gmail API setup is not connected yet.")
        return redirect("account")
    if not _permitted(request) or not request.user.check_password(request.POST.get("current_password", "")):
        messages.error(request, "Your current administrator password is required.")
        return redirect("account")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    request.session[SESSION_KEY] = {
        "state": state, "nonce": nonce, "verifier": verifier,
        "owner_id": request.user.pk, "created_at": time.time(),
    }
    params = {
        "client_id": settings.KOFAD_GMAIL_CLIENT_ID,
        "redirect_uri": _callback_uri(),
        "response_type": "code",
        "scope": "openid email " + GMAIL_SCOPE,
        "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "false",
    }
    response = redirect(google_oauth.AUTHORIZE_URL + "?" + urlencode(params))
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
def connect_callback(request):
    pending = request.session.pop(SESSION_KEY, None)
    if (
        not _permitted(request) or not configured() or not pending
        or not constant_time_compare(request.GET.get("state", ""), pending.get("state", ""))
        or not request.GET.get("state")
        or pending.get("owner_id") != request.user.pk
        or time.time() - pending.get("created_at", 0) > 600
    ):
        messages.error(request, "Gmail connection expired. Start again from My Account.")
        return redirect("account")
    if request.GET.get("error") or not request.GET.get("code") or len(request.GET["code"]) > 4096:
        messages.error(request, "Gmail connection was cancelled.")
        return redirect("account")
    try:
        response = requests.post(
            google_oauth.TOKEN_URL,
            data={
                "client_id": settings.KOFAD_GMAIL_CLIENT_ID,
                "client_secret": settings.KOFAD_GMAIL_CLIENT_SECRET,
                "code": request.GET["code"],
                "code_verifier": pending["verifier"],
                "redirect_uri": _callback_uri(),
                "grant_type": "authorization_code",
            },
            timeout=12, allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or GMAIL_SCOPE not in payload.get("scope", "").split():
            raise ValidationError("Google did not grant Gmail send-only access.")
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or len(refresh_token) < 20:
            raise ValidationError("No offline Gmail access was granted. Reconnect and approve all requested access.")
        identity = google_oauth._validated_google_claims(
            payload.get("id_token", ""), pending["nonce"],
            client_id=settings.KOFAD_GMAIL_CLIENT_ID,
        )
        with transaction.atomic():
            GmailSenderConnection.objects.update_or_create(
                pk=1,
                defaults={
                    "email": identity["email"],
                    "google_subject": identity["subject"],
                    "encrypted_refresh_token": _cipher().encrypt(refresh_token.encode()).decode("ascii"),
                    "connected_by_id": request.user.pk,
                },
            )
        audit(request.user, None, "email.gmail_connected", request.user.pk)
        messages.success(request, "Business Gmail connected securely. HTTPS email delivery is ready.")
    except (ValueError, TypeError, KeyError, requests.RequestException, ValidationError) as exc:
        logger.warning("Gmail API authorisation failed (no credentials logged)")
        messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError)
                       else "Google could not finish Gmail authorisation. Try again.")
    return redirect("account")


@never_cache
@sensitive_post_parameters("current_password")
def disconnect(request):
    if request.method != "POST":
        return redirect("account")
    if not _permitted(request) or not request.user.check_password(request.POST.get("current_password", "")):
        messages.error(request, "Current administrator password required.")
        return redirect("account")
    GmailSenderConnection.objects.filter(pk=1).delete()
    audit(request.user, None, "email.gmail_disconnected", request.user.pk)
    messages.success(request, "Gmail sender disconnected. No further emails can be sent via this connection.")
    return redirect("account")


def send_gmail(*, subject, body, recipient):
    """Send one opt-in transaction email with the linked sender via Gmail HTTPS."""
    sender = connection()
    if not configured() or not sender:
        raise ValidationError("Gmail sender is not connected.")
    try:
        token_response = requests.post(
            google_oauth.TOKEN_URL,
            data={
                "client_id": settings.KOFAD_GMAIL_CLIENT_ID,
                "client_secret": settings.KOFAD_GMAIL_CLIENT_SECRET,
                "refresh_token": _cipher().decrypt(
                    sender.encrypted_refresh_token.encode("ascii")
                ).decode("utf-8"),
                "grant_type": "refresh_token",
            },
            timeout=12, allow_redirects=False,
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not isinstance(access_token, str) or len(access_token) < 20:
            raise ValueError("Missing Gmail access token")
        letter = EmailMessage()
        letter["From"] = sender.email
        letter["To"] = recipient
        letter["Subject"] = subject
        letter.set_content(body)
        raw_message = base64.urlsafe_b64encode(letter.as_bytes()).decode("ascii").rstrip("=")
        response = requests.post(
            SEND_URL,
            json={"raw": raw_message},
            headers={"Authorization": "Bearer " + access_token},
            timeout=15, allow_redirects=False,
        )
        response.raise_for_status()
        GmailSenderConnection.objects.filter(pk=sender.pk).update(last_send_at=timezone.now())
        return 1
    except Exception as exc:
        # No recipient, tokens or API response bodies are logged.
        logger.warning("Gmail API email delivery failed; retry is managed by the outbox")
        raise ValidationError("Gmail delivery is temporarily unavailable.") from exc


@never_cache
@sensitive_post_parameters("current_password")
def send_test(request):
    if request.method != "POST":
        return redirect("account")
    if not _permitted(request) or not request.user.check_password(request.POST.get("current_password", "")):
        messages.error(request, "Current administrator password required.")
        return redirect("account")
    sender = connection()
    if not sender or not configured():
        messages.error(request, "Gmail sender is not connected.")
        return redirect("account")
    try:
        send_gmail(
            subject="KOFAD Gmail API connection test",
            body="Your KOFAD Gmail connection is working. This is a test notification from your private staff account.",
            recipient=sender.email,
        )
        messages.success(request, "Test email sent. Check the linked Gmail inbox.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("account")
