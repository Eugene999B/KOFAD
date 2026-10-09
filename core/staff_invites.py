"""Owner-provisioned staff onboarding. No temporary passwords or public registration."""

import hashlib
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from .models import Access, StaffInvitation
from .sms.providers import get_provider
from .sms.service import normalize_phone
from . import email_identity
from . import services as audit_services


def validate_delivery(channel, destination, *, whatsapp_opt_in=False):
    """Reject unusable channels before owner creates a dormant staff record."""
    if channel not in {"sms", "email", "whatsapp"}:
        raise ValidationError("Choose SMS, WhatsApp or company email for the invitation.")
    if channel == "email":
        recipient = email_identity.normalize_email(destination)
        if not email_identity.delivery_ready():
            raise ValidationError("KOFAD business email is not connected yet. Choose SMS or configure email first.")
        return recipient
    recipient = normalize_phone(destination)
    if channel == "sms":
        if not settings.SMS_ENABLED:
            raise ValidationError("KOFAD SMS is disabled. Enable the existing SMS provider first.")
        get_provider(settings.SMS_PROVIDER).validate()
    else:
        from .whatsapp_delivery import configuration_error
        template = getattr(settings, "KOFAD_STAFF_INVITE_WHATSAPP_TEMPLATE", "")
        error = configuration_error(template_name=template)
        if error:
            raise ValidationError(error)
        if not whatsapp_opt_in:
            raise ValidationError("Confirm the staff member opted in to WhatsApp invitations.")
    return recipient


def issue(user, owner, channel, destination):
    """Create a hashed, single-use grant; raw token exists only in delivery memory."""
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    invitation, _ = StaffInvitation.objects.update_or_create(
        user=user,
        defaults={
            "created_by": owner,
            "token_digest": digest,
            "channel": channel,
            "destination": destination,
            "expires_at": timezone.now() + timedelta(hours=24),
            "consumed_at": None, "delivered_at": None,
            "delivery_state": "pending",
        },
    )
    url = settings.KOFAD_STAFF_SITE_ORIGIN.rstrip("/") + reverse(
        "staff_invitation_open", args=[invitation.pk, token],
    )
    return invitation, url


def deliver(invitation, url):
    """Send an invitation; no raw token in database, audit events or logs."""
    body = (
        "KOFAD IMPEX ENTERPRISE invited you to activate your staff account. "
        "Set your own password with this private link (expires in 24 hours): " + url
        + " If unexpected, ignore this message."
    )
    try:
        if invitation.channel == "email":
            email_identity._send_kofad_mail(
                "Activate your KOFAD staff account", body,
                [invitation.destination], purpose="security",
            )
        elif invitation.channel == "sms":
            provider = get_provider(settings.SMS_PROVIDER)
            result = provider.submit(
                invitation.destination, body, settings.SMS_SENDER_ID, "", False,
            )
            if result.status not in {"accepted", "delivered", "unknown"}:
                raise ValidationError("The SMS provider did not accept the invitation.")
        elif invitation.channel == "whatsapp":
            template_name = getattr(settings, "KOFAD_STAFF_INVITE_WHATSAPP_TEMPLATE", "")
            from .whatsapp_delivery import configuration_error
            error = configuration_error(template_name=template_name)
            if error:
                raise ValidationError(error)
            payload = {
                "messaging_product": "whatsapp",
                "to": invitation.destination.lstrip("+"),
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": getattr(settings, "KOFAD_STAFF_INVITE_WHATSAPP_LANGUAGE", "en")},
                    "components": [{"type": "body", "parameters": [
                        {"type": "text", "text": url},
                    ]}],
                },
            }
            response = requests.post(
                f"https://graph.facebook.com/{settings.WHATSAPP_GRAPH_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
                headers={"Authorization": "Bearer " + settings.WHATSAPP_ACCESS_TOKEN},
                json=payload, timeout=settings.WHATSAPP_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            if not response.ok or not response.json().get("messages"):
                raise ValidationError("The WhatsApp provider did not accept this invitation.")
        else:
            raise ValidationError("Invalid invitation delivery channel.")
    except (ValidationError, requests.RequestException, ValueError, TypeError) as exc:
        StaffInvitation.objects.filter(pk=invitation.pk).update(delivery_state="failed")
        raise ValidationError(
            "Invitation could not be confirmed. The staff account stays inactive; "
            "check the provider before resending."
        ) from exc
    StaffInvitation.objects.filter(pk=invitation.pk).update(
        delivery_state="submitted", delivered_at=timezone.now(),
    )


def valid(invitation, digest):
    return bool(
        invitation and not invitation.consumed_at and invitation.expires_at > timezone.now()
        and not invitation.user.is_active
        and constant_time_compare(invitation.token_digest, digest)
    )


@never_cache
def open_invitation(request, pk, token):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    invitation = StaffInvitation.objects.select_related("user").filter(pk=pk).first()
    if not valid(invitation, digest):
        response = render(request, "staff_invitation_expired.html", status=410)
    else:
        request.session["staff_invitation_pending"] = {"pk": pk, "digest": digest}
        response = redirect("staff_invitation_complete")
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@never_cache
@sensitive_post_parameters("password", "password_confirm")
def complete_invitation(request):
    pending = request.session.get("staff_invitation_pending") or {}
    invitation = StaffInvitation.objects.select_related("user").filter(pk=pending.get("pk")).first()
    if not valid(invitation, pending.get("digest", "")):
        request.session.pop("staff_invitation_pending", None)
        response = render(request, "staff_invitation_expired.html", status=410)
    elif request.method == "POST":
        password = request.POST.get("password", "")
        confirm = request.POST.get("password_confirm", "")
        try:
            if password != confirm:
                raise ValidationError("The two passwords must match.")
            validate_password(password, user=invitation.user)
            with transaction.atomic():
                locked = StaffInvitation.objects.select_for_update().select_related("user").get(pk=invitation.pk)
                if not valid(locked, pending["digest"]):
                    raise ValidationError("This invitation is no longer valid.")
                user = locked.user
                user.set_password(password)
                user.is_active = True
                user.save(update_fields=["password", "is_active"])
                Access.objects.filter(user=user).update(force_password_change=False)
                locked.consumed_at = timezone.now()
                locked.save(update_fields=["consumed_at"])
                if locked.channel == "email":
                    from marketplace.models import EmailIdentity
                    EmailIdentity.objects.update_or_create(
                        kind="staff", owner_id=user.pk,
                        defaults={"email": locked.destination, "verified_at": timezone.now()},
                    )
                audit_services.audit(None, None, "staff.invitation_activated", user.pk)
            request.session.pop("staff_invitation_pending", None)
            messages.success(request, "Your KOFAD staff password is ready. Sign in to your private workspace.")
            return redirect("login")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        response = render(request, "staff_invitation_complete.html", {"invitation": invitation})
    elif request.method == "GET":
        response = render(request, "staff_invitation_complete.html", {"invitation": invitation})
    else:
        return HttpResponseNotAllowed(["GET", "POST"])
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response
