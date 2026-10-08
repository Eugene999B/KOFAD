import hashlib
import re
import secrets
import uuid
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from .models import Access, LoginAttempt, PasswordRecovery
from .services import audit
from .sms.providers import get_provider
from .sms.service import normalize_phone


def digest(value):
    return salted_hmac("kofad-password-recovery", value, algorithm="sha256").hexdigest()


def sms_ready():
    if not settings.SMS_ENABLED or settings.SMS_SANDBOX:
        return False
    try:
        get_provider(settings.SMS_PROVIDER).validate()
        return True
    except ValidationError:
        return False


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]


class RecoveryPhoneForm(forms.Form):
    recovery_phone = forms.CharField(max_length=40, required=False, label="Recovery phone number",
        help_text="Use the number belonging to this account, for example +233241234567. Leave blank to disable SMS recovery.",
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}))
    current_password = forms.CharField(label="Current password", widget=forms.PasswordInput(attrs={"autocomplete":"current-password"}))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_recovery_phone(self):
        raw = self.cleaned_data["recovery_phone"].strip()
        return normalize_phone(raw) if raw else ""

    def clean_current_password(self):
        value = self.cleaned_data["current_password"]
        if not self.user.check_password(value):
            raise ValidationError("Your current password is incorrect.")
        return value


@login_required
@sensitive_post_parameters("current_password")
def account(request):
    access = request.user.access
    action = request.POST.get("action", "recovery")
    profile_form = ProfileForm(request.POST if request.method == "POST" and action == "profile" else None, instance=request.user)
    if request.method == "POST" and action == "profile" and profile_form.is_valid():
        profile_form.save()
        audit(request.user, None, "account.profile_updated", request.user.pk)
        messages.success(request, "Your profile was updated.")
        return redirect("account")
    form = RecoveryPhoneForm(request.user, request.POST if request.method == "POST" and action == "recovery" else None, initial={"recovery_phone":access.recovery_phone})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            current = User.objects.select_for_update().get(pk=request.user.pk)
            if not current.check_password(form.cleaned_data["current_password"]):
                form.add_error("current_password", "Your password changed. Enter the current password.")
                return render(request, "account.html", {"title":"My account", "form":form, "sms_ready":sms_ready(), "profile_form":profile_form})
            locked = Access.objects.select_for_update().get(pk=access.pk)
            locked.recovery_phone = form.cleaned_data["recovery_phone"]
            locked.save(update_fields=["recovery_phone"])
            PasswordRecovery.objects.filter(user=request.user, used=False).update(used=True)
            audit(request.user, None, "account.recovery_phone_updated", request.user.pk)
        messages.success(request, "Recovery phone saved.")
        return redirect("account")
    return render(request, "account.html", {"title":"My account", "form":form, "sms_ready":sms_ready(), "profile_form":profile_form})


def consume_budget(username):
    now = timezone.now()
    with transaction.atomic():
        # Consistent lock order across requests; limit both account and aggregate sends.
        for key, limit in (("global", 100), ("user:"+username.casefold(), 3)):
            row, _ = LoginAttempt.objects.get_or_create(key=digest("recovery-rate:"+key))
            row = LoginAttempt.objects.select_for_update().get(pk=row.pk)
            if not row.blocked_until or row.blocked_until <= now:
                row.failures, row.blocked_until = 0, now + timedelta(hours=1)
            if row.failures >= limit:
                return False
            row.failures += 1
            row.save()
    return True


@never_cache
@sensitive_post_parameters()
def forgot_password(request):
    ready = sms_ready()
    if request.method == "POST" and ready:
        username = request.POST.get("username", "").strip()[:150]
        challenge_id = uuid.uuid4()
        request.session["recovery_id"] = str(challenge_id)
        if username and consume_budget(username):
            users = list(User.objects.filter(username__iexact=username, is_active=True)[:2])
            if len(users) == 1:
                user = users[0]
                access, _ = Access.objects.get_or_create(user=user)
                try:
                    phone = normalize_phone(access.recovery_phone)
                except ValidationError:
                    phone = ""
                if phone:
                    code = f"{secrets.randbelow(1000000):06d}"
                    with transaction.atomic():
                        user = User.objects.select_for_update().get(pk=user.pk)
                        current_access = Access.objects.select_for_update().get(pk=access.pk)
                        phone = current_access.recovery_phone
                        if not phone or not user.is_active:
                            return redirect("reset_password")
                        recent = PasswordRecovery.objects.filter(
                            user=user, used=False, phone=phone,
                            created_at__gte=timezone.now() - timedelta(seconds=60),
                        ).order_by("-created_at").first()
                        if recent:
                            request.session["recovery_id"] = str(recent.pk)
                            return redirect("reset_password")
                        PasswordRecovery.objects.filter(user=user, used=False).update(used=True)
                        challenge = PasswordRecovery.objects.create(id=challenge_id, user=user, phone=phone,
                            code_digest=digest(str(challenge_id)+":"+code), password_stamp=digest(user.password),
                            expires_at=timezone.now()+timedelta(minutes=10))
                    try:
                        result = get_provider(settings.SMS_PROVIDER).submit(phone,
                            f"KOFAD password reset code: {code}. Expires in 10 minutes. Do not share this code.",
                            settings.SMS_SENDER_ID, "", False)
                        sent = result.status in ("accepted", "delivered", "unknown")
                    except Exception:
                        sent = False
                    update = {"sent": sent}
                    if sent:
                        update["expires_at"] = timezone.now() + timedelta(minutes=10)
                    PasswordRecovery.objects.filter(pk=challenge.pk).update(**update)
                    audit(None, None, "password.recovery_requested", user.pk, {"accepted":sent})
        return redirect("reset_password")
    return render(request, "forgot_password.html", {"sms_ready":ready})


@never_cache
@sensitive_post_parameters("code", "new_password1", "new_password2")
def reset_password(request):
    challenge_id = request.session.get("recovery_id")
    if not challenge_id:
        return redirect("forgot_password")
    error = ""
    form = SetPasswordForm(User(), request.POST or None)
    if request.method == "POST":
        with transaction.atomic():
            # User, access, challenge lock order matches authenticated credential changes.
            hint = PasswordRecovery.objects.filter(pk=challenge_id).values("user_id").first()
            user = User.objects.select_for_update().filter(pk=hint["user_id"]).first() if hint else None
            access = Access.objects.select_for_update().filter(user_id=hint["user_id"]).first() if hint else None
            challenge = PasswordRecovery.objects.select_for_update().filter(pk=challenge_id).first()
            valid = bool(challenge and access and user and user.is_active and challenge.sent and
                not challenge.used and challenge.attempts < 5 and challenge.expires_at > timezone.now() and
                access.recovery_phone == challenge.phone and constant_time_compare(challenge.password_stamp, digest(user.password)))
            if valid:
                challenge.attempts += 1
                challenge.save(update_fields=["attempts"])
                raw_code = request.POST.get("code", "").strip()
                code = re.sub(r"[\s-]", "", raw_code)
                valid = bool(
                    re.fullmatch(r"[0-9]{6}", code)
                    and constant_time_compare(
                        challenge.code_digest, digest(str(challenge.pk)+":"+code)
                    )
                )
            if valid:
                form = SetPasswordForm(user, request.POST)
                if form.is_valid():
                    form.save()
                    Access.objects.filter(user=user).update(totp_secret="", totp_last_step=-1)
                    PasswordRecovery.objects.filter(user=user, used=False).update(used=True)
                    LoginAttempt.objects.filter(key=hashlib.sha256(user.username.casefold().encode()).hexdigest()).update(failures=0, blocked_until=None)
                    audit(user, None, "password.recovered", user.pk)
                    audit(user, None, "mfa.recovery_reset", user.pk)
                    request.session.pop("recovery_id", None)
                    messages.success(request, "Password reset. Sign in with your new password.")
                    return redirect("login")
            else:
                error = "The code is invalid, expired or unavailable. Request a new code or contact your administrator."
    return render(request, "reset_password.html", {"form":form, "error":error})
