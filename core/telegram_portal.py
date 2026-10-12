"""KOFAD-authenticated Telegram pairing pages; never infer identity from phone or handle."""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from marketplace import services as market_services
from .telegram_integration import begin_link, confirm_link, linked_identity
from .telegram_channels import bot_enabled, bot_for_account, bot_link
from .models import Access, TelegramIdentity, TelegramLinkChallenge


def _portal(request, kind, account, bot_kind=None):
    bot_kind = bot_kind or bot_for_account(kind)
    identity = linked_identity(kind, account.pk, bot_kind=bot_kind)
    active = settings.TELEGRAM_ACCOUNT_LINK_ENABLED and bot_enabled(bot_kind) and bool(bot_link(bot_kind))
    if request.method == "POST":
        action = request.POST.get("action", "")
        entered_password = request.POST.get("current_password", "")
        # Changing a communication recovery channel is a security-sensitive action.
        if not active:
            messages.error(request, "Telegram account linking has not yet been enabled.")
        elif not entered_password or not account.check_password(entered_password):
            messages.error(request, "Enter your current KOFAD password to continue.")
        elif action == "start":
            # Phone is OPTIONAL. Telegram pairing works even for a superadmin
            # who has never recorded a recovery number.
            proposed_phone = request.POST.get("optional_phone", "").strip() if kind == "staff" else ""
            try:
                url = begin_link(kind, account.pk, bot_kind=bot_kind, proposed_phone=proposed_phone)
            except (ValueError, ValidationError) as exc:
                messages.error(request, str(exc))
                request.session.pop(f"telegram_pair_url_{kind}_{bot_kind}", None)
            else:
                request.session[f"telegram_pair_url_{kind}_{bot_kind}"] = url
                request.session[f"telegram_pair_has_phone_{kind}_{bot_kind}"] = bool(proposed_phone)
                messages.success(request, (
                    "Open the Telegram bot link and press Start. The six-digit code appears INSIDE "
                    "that bot's Telegram chat, not as a regular text message. "
                    + ("If you entered a mobile number, send /sharephone to confirm it first. "
                       if proposed_phone else "")
                    + "Return here with the code to complete linking."
                ))
        elif action == "confirm":
            if confirm_link(kind, account.pk, request.POST.get("confirmation_code", ""), bot_kind=bot_kind):
                request.session.pop(f"telegram_pair_url_{kind}_{bot_kind}", None)
                request.session.pop(f"telegram_pair_has_phone_{kind}_{bot_kind}", None)
                messages.success(request, "Telegram has been securely connected to your KOFAD account.")
            else:
                messages.error(request, "Confirmation failed or expired. Retry or start a new connection.")
        elif action == "disconnect":
            TelegramIdentity.objects.filter(kind=kind, owner_id=account.pk, bot_kind=bot_kind).delete()
            TelegramLinkChallenge.objects.filter(kind=kind, owner_id=account.pk, bot_kind=bot_kind).delete()
            request.session.pop(f"telegram_pair_url_{kind}_{bot_kind}", None)
            request.session.pop(f"telegram_pair_has_phone_{kind}_{bot_kind}", None)
            messages.success(request, "Your Telegram account was disconnected.")
        elif action == "preferences" and identity:
            # Never silently subscribe to messaging. Require explicit opt-in.
            allowed_management = kind == "staff" and (account.is_superuser or any(
                account.has_perm("core." + perm)
                for perm in ("manage_company", "view_reports", "operate_finance")
            ))
            identity.notifications_enabled = bot_kind != "official" and request.POST.get("notifications_enabled") == "on"
            identity.marketing_opt_in = bot_kind == "customer" and kind == "customer" and request.POST.get("marketing_opt_in") == "on"
            identity.closing_opt_in = bot_kind == "staff" and allowed_management and request.POST.get("closing_opt_in") == "on"
            identity.critical_opt_in = bot_kind == "staff" and allowed_management and request.POST.get("critical_opt_in") == "on"
            identity.stock_opt_in = bot_kind == "staff" and kind == "staff" and (account.is_superuser or
                account.has_perm("core.manage_company") or account.has_perm("core.operate_inventory")) and (
                request.POST.get("stock_opt_in") == "on")
            # Explicit separate opt-in; user has already reconfirmed KOFAD password.
            identity.recovery_opt_in = bot_kind == "official" and request.POST.get("recovery_opt_in") == "on" and bool(
                (account.phone and account.verified_at) if kind == "customer"
                else Access.objects.filter(user=account).values_list("recovery_phone", flat=True).first()
            )
            identity.save(update_fields=["notifications_enabled", "marketing_opt_in",
                                         "closing_opt_in", "critical_opt_in", "stock_opt_in", "recovery_opt_in"])
            messages.success(request, "Telegram messaging preferences saved.")
        else:
            messages.error(request, "Unsupported Telegram account action.")
        return redirect(request.path)
    context = {
        "title": "Telegram · KOFAD IMPEX",
        "kind": kind, "telegram_bot_kind": bot_kind,
        "telegram_bot_url": bot_link(bot_kind), "telegram_identity": identity,
        "telegram_active": active,
        "telegram_pair_url": request.session.get(f"telegram_pair_url_{kind}_{bot_kind}", ""),
        "telegram_pair_has_phone": request.session.get(f"telegram_pair_has_phone_{kind}_{bot_kind}", False),
        "staff_recovery_phone": (Access.objects.filter(user=account).values_list("recovery_phone", flat=True).first() or "") if kind == "staff" else "",
        "is_inventory": kind == "staff" and (account.is_superuser or
            account.has_perm("core.manage_company") or account.has_perm("core.operate_inventory")),
        "is_management": kind == "staff" and (account.is_superuser or any(
            account.has_perm("core." + p) for p in ("manage_company", "view_reports", "operate_finance")
        )),
    }
    if kind == "customer":
        from marketplace.views import _market_context
        context = _market_context(request, **context)
    return render(request, "telegram_integration.html" if kind == "staff"
                  else "marketplace/telegram_integration.html", context)


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



@never_cache
@require_http_methods(["GET", "POST"])
@login_required
def staff_security_portal(request):
    if not request.user.is_active:
        return redirect(settings.STAFF_LOGIN_PATH)
    return _portal(request, "staff", request.user, bot_kind="official")


@never_cache
@require_http_methods(["GET", "POST"])
def customer_security_portal(request):
    account = market_services.customer_from_session(request)
    if account is None:
        request.session["market_after_login"] = request.path
        return redirect("market_access")
    return _portal(request, "customer", account, bot_kind="official")
