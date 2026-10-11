"""Independent opt-in Telegram password recovery; existing SMS/email flows unchanged.

A registered phone is only an account lookup hint, never Telegram identity proof.
Only a previously website-confirmed, explicitly recovery-opted-in Telegram link can
receive a code. Staff retain all privileged MFA requirements after resetting.
"""
import hmac
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .identity import normalize_ghana_phone
from .models import Access, TelegramIdentity, TelegramOutbound, TelegramRecoveryChallenge
from .telegram_integration import _digest


def _account(kind, pk):
    if kind == "staff":
        return User.objects.filter(pk=pk, is_active=True).first()
    if kind == "customer":
        from marketplace.models import CustomerAccount
        return CustomerAccount.objects.filter(pk=pk, active=True, verified_at__isnull=False).first()
    return None


def _stamp(account, kind):
    hashed = account.password if kind == "staff" else account.password_hash
    return _digest("recovery-stamp", kind + ":" + str(account.pk) + ":" + hashed)


def _find_account(kind, identifier, staff_username):
    try:
        normalized = normalize_ghana_phone(identifier)
    except ValidationError:
        return None
    if kind == "customer":
        from marketplace.models import CustomerAccount
        return CustomerAccount.objects.filter(phone=normalized, active=True, verified_at__isnull=False).first()
    username = (staff_username or "").strip()[:150]
    if not username:
        return None
    matches = list(User.objects.filter(username__iexact=username, is_active=True)[:2])
    if len(matches) != 1:
        return None
    access = Access.objects.filter(user=matches[0]).first()
    if not access:
        return None
    try:
        if normalize_ghana_phone(access.recovery_phone) == normalized:
            return matches[0]
    except ValidationError:
        pass
    return None


def request_recovery(kind, identifier, staff_username):
    account = _find_account(kind, identifier, staff_username)
    if not account:
        return None
    identity = TelegramIdentity.objects.filter(
        kind=kind, owner_id=account.pk, revoked_at__isnull=True, recovery_opt_in=True,
    ).first()
    if not identity:
        return None
    now = timezone.now()
    if TelegramRecoveryChallenge.objects.filter(
        identity=identity, created_at__gte=now - timedelta(hours=1),
    ).count() >= 3:
        return None
    code = f"{secrets.randbelow(1000000):06d}"
    with transaction.atomic():
        challenge = TelegramRecoveryChallenge.objects.create(
            identity=identity, kind=kind, owner_id=account.pk,
            code_digest="", password_stamp=_stamp(account, kind),
            expires_at=now + timedelta(minutes=10),
        )
        challenge.code_digest = _digest(str(challenge.pk) + ":recover", code)
        challenge.save(update_fields=["code_digest"])
        TelegramOutbound.objects.create(
            source_key="tg:recovery:" + str(challenge.pk), identity=identity,
            category="recovery",
            body=(
                "KOFAD security — password reset code:\n" + code
                + "\n\nExpires in 10 minutes. If you didn't request this, ignore it. "
                "Never share this code with anyone."
            ),
        )
    return challenge.pk


def complete_recovery(kind, challenge_id, code, new_password, confirm_password):
    try:
        pk = uuid.UUID(str(challenge_id))
    except (ValueError, TypeError):
        return False, "This recovery request is invalid or expired."
    if not new_password or new_password != confirm_password:
        return False, "The new passwords do not match."
    with transaction.atomic():
        challenge = TelegramRecoveryChallenge.objects.select_for_update().select_related("identity").filter(
            pk=pk, kind=kind, used_at__isnull=True, expires_at__gt=timezone.now(),
        ).first()
        if not challenge or challenge.attempts >= 5:
            return False, "The recovery code has expired or is unavailable."
        challenge.attempts += 1
        challenge.save(update_fields=["attempts"])
        identity = challenge.identity
        account = _account(kind, challenge.owner_id)
        if not account or identity.revoked_at or not identity.recovery_opt_in:
            return False, "This account can no longer use Telegram recovery."
        if identity.kind != kind or identity.owner_id != challenge.owner_id:
            return False, "This recovery account has changed."
        if _stamp(account, kind) != challenge.password_stamp:
            return False, "Your password has changed since this request."
        sent = TelegramOutbound.objects.filter(
            source_key="tg:recovery:" + str(challenge.pk), status="sent",
        ).exists()
        if not sent:
            return False, "Recovery delivery is not confirmed. Please try again."
        if not isinstance(code, str) or len(code.strip()) != 6 or not code.strip().isdigit():
            return False, "Incorrect or expired verification code."
        expected = _digest(str(challenge.pk) + ":recover", code.strip())
        if not hmac.compare_digest(expected, challenge.code_digest):
            return False, "Incorrect or expired verification code."
        try:
            validate_password(new_password, account if kind == "staff" else None)
        except ValidationError as exc:
            return False, "; ".join(exc.messages)
        account.set_password(new_password)
        if kind == "staff":
            account.save(update_fields=["password"])
            Access.objects.filter(user=account).update(session_version=F("session_version") + 1)
        else:
            account.save(update_fields=["password_hash"])
        challenge.used_at = timezone.now()
        challenge.save(update_fields=["used_at"])
        return True, "Your password has been changed. Sign in again to continue."


@never_cache
@require_http_methods(["GET", "POST"])
def staff_recovery(request):
    return _portal(request, "staff")


@never_cache
@require_http_methods(["GET", "POST"])
def customer_recovery(request):
    return _portal(request, "customer")


def _portal(request, kind):
    if not (settings.TELEGRAM_BOT_ENABLED and settings.TELEGRAM_RECOVERY_ENABLED):
        return render(request, "telegram_recovery.html", {
            "title": "Telegram recovery", "telegram_recovery_disabled": True, "kind": kind,
        }, status=503)
    session_key = "telegram_recovery_id_" + kind
    if request.method == "POST":
        action = request.POST.get("action", "request")
        if action == "request":
            challenge_id = request_recovery(kind, request.POST.get("phone", "")[:40],
                                            request.POST.get("username", "")[:150])
            # A dummy identifier gives exactly the same visible response for an unknown account.
            request.session[session_key] = str(challenge_id or uuid.uuid4())
            messages.info(request, "If Telegram recovery is enabled for that verified account, a code will be sent.")
        elif action == "complete":
            success, note = complete_recovery(
                kind, request.session.get(session_key), request.POST.get("code", ""),
                request.POST.get("password", ""), request.POST.get("password_confirm", ""),
            )
            if success:
                request.session.pop(session_key, None)
                messages.success(request, note)
                return redirect("/login/" if kind == "staff" else "/market/account/login/")
            messages.error(request, note)
        elif action == "cancel":
            request.session.pop(session_key, None)
        return redirect(request.path)
    return render(request, "telegram_recovery.html", {
        "title": "Telegram recovery", "kind": kind, "telegram_recovery_disabled": False,
        "challenge_started": bool(request.session.get(session_key)),
    })
