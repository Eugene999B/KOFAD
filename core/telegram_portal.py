"""KOFAD-authenticated Telegram pairing pages; never infer identity from phone or handle."""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from marketplace import services as market_services
from .telegram_integration import begin_link, confirm_link, linked_identity
from .models import TelegramIdentity, TelegramLinkChallenge


def _portal(request, kind, account):
    identity = linked_identity(kind, account.pk)
    active = settings.TELEGRAM_ACCOUNT_LINK_ENABLED and settings.TELEGRAM_BOT_ENABLED
    if request.method == "POST":
        action = request.POST.get("action", "")
        entered_password = request.POST.get("current_password", "")
        # Changing a communication recovery channel is a security-sensitive action.
        if not active:
            messages.error(request, "Telegram account linking has not yet been enabled.")
        elif not entered_password or not account.check_password(entered_password):
            messages.error(request, "Enter your current KOFAD password to continue.")
        elif action == "start":
            url = begin_link(kind, account.pk)
            request.session[f"telegram_pair_url_{kind}"] = url
            messages.success(request, "Open the Telegram link, then return and enter the code provided by the bot.")
        elif action == "confirm":
            if confirm_link(kind, account.pk, request.POST.get("confirmation_code", "")):
                request.session.pop(f"telegram_pair_url_{kind}", None)
                messages.success(request, "Telegram has been securely connected to your KOFAD account.")
            else:
                messages.error(request, "Confirmation failed or expired. Retry or start a new connection.")
        elif action == "disconnect":
            TelegramIdentity.objects.filter(kind=kind, owner_id=account.pk).delete()
            TelegramLinkChallenge.objects.filter(kind=kind, owner_id=account.pk).delete()
            request.session.pop(f"telegram_pair_url_{kind}", None)
            messages.success(request, "Your Telegram account was disconnected.")
        elif action == "preferences" and identity:
            # Never silently subscribe to messaging. Require explicit opt-in.
            allowed_management = kind == "staff" and (account.is_superuser or any(
                account.has_perm("core." + perm)
                for perm in ("manage_company", "view_reports", "operate_finance")
            ))
            identity.notifications_enabled = request.POST.get("notifications_enabled") == "on"
            identity.marketing_opt_in = kind == "customer" and request.POST.get("marketing_opt_in") == "on"
            identity.closing_opt_in = allowed_management and request.POST.get("closing_opt_in") == "on"
            identity.critical_opt_in = allowed_management and request.POST.get("critical_opt_in") == "on"
            # Recovery remains disabled until dedicated recovery channel has been audited.
            identity.recovery_opt_in = False
            identity.save(update_fields=["notifications_enabled", "marketing_opt_in",
                                         "closing_opt_in", "critical_opt_in", "recovery_opt_in"])
            messages.success(request, "Telegram messaging preferences saved.")
        else:
            messages.error(request, "Unsupported Telegram account action.")
        return redirect(request.path)
    return render(request, "telegram_integration.html" if kind == "staff"
                  else "marketplace/telegram_integration.html", {
        "title": "Telegram · KOFAD IMPEX",
        "kind": kind, "telegram_identity": identity,
        "telegram_active": active,
        "telegram_pair_url": request.session.get(f"telegram_pair_url_{kind}", ""),
        "is_management": kind == "staff" and (account.is_superuser or any(
            account.has_perm("core." + p) for p in ("manage_company", "view_reports", "operate_finance")
        )),
    })


@never_cache
@require_http_methods(["GET", "POST"])
@login_required
def staff_portal(request):
    if not request.user.is_active:
        return redirect("login")
    return _portal(request, "staff", request.user)


@never_cache
@require_http_methods(["GET", "POST"])
def customer_portal(request):
    account = market_services.customer_from_session(request)
    if account is None:
        request.session["market_after_login"] = request.path
        return redirect("market_access")
    return _portal(request, "customer", account)
