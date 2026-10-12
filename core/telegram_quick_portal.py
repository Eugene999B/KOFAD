"""One-page Telegram Mini App identity connection using existing KOFAD sessions."""
from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from urllib.parse import urlencode

from marketplace import services as market_services
from .telegram_channels import bot_enabled
from .telegram_quick_auth import TelegramMiniAppError, link_miniapp_identity, verify_miniapp_data
from .models import Access


@never_cache
@require_http_methods(["GET", "POST"])
def quick_link(request, kind, bot_kind):
    if kind not in {"staff", "customer"} or bot_kind not in {kind, "official"}:
        return render(request, "telegram_quick_link.html", {"invalid": True}, status=404)
    enabled = bool(settings.TELEGRAM_ACCOUNT_LINK_ENABLED and bot_enabled(bot_kind))
    route = reverse("telegram_quick_" + kind, kwargs={"bot_kind": bot_kind})
    if kind == "staff":
        account = request.user if request.user.is_authenticated and request.user.is_active else None
        if account:
            access = Access.objects.filter(user=account).first()
            if not access or access.must_change_password or access.force_password_change:
                account = None
        login_url = settings.STAFF_LOGIN_PATH + "?" + urlencode({"next": route})
    else:
        account = market_services.customer_from_session(request)
        login_url = reverse("market_login") + "?" + urlencode({"next": route})
    error = ""
    if request.method == "POST":
        if not enabled or account is None:
            error = "Sign in to KOFAD with your regular account before connecting Telegram."
        else:
            try:
                tg_user = verify_miniapp_data(bot_kind, request.POST.get("telegram_init_data", ""))
                linked = link_miniapp_identity(
                    kind, account.pk, bot_kind, tg_user["id"],
                    alerts=request.POST.get("alerts") == "on",
                    marketing=request.POST.get("marketing") == "on",
                    recovery=request.POST.get("recovery") == "on",
                )
                if linked is None:
                    raise TelegramMiniAppError("Connection could not be verified.")
                return render(request, "telegram_quick_link.html", {
                    "linked": True, "kind": kind, "bot_kind": bot_kind,
                })
            except TelegramMiniAppError as exc:
                error = str(exc)
    response = render(request, "telegram_quick_link.html", {
        "kind": kind, "bot_kind": bot_kind, "active": enabled, "account": account,
        "login_url": login_url, "error": error,
        "supports_alerts": bot_kind != "official",
        "supports_recovery": bot_kind == "official",
        "supports_marketing": kind == "customer" and bot_kind == "customer",
    })
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Cache-Control"] = "no-store, private"
    return response
