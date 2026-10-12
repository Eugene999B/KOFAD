"""Fast Telegram Mini App account association through existing KOFAD sessions."""

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods


@never_cache
@require_http_methods(["GET", "POST"])
def quick_link(request, kind, bot_kind):
    return render(request, "telegram_quick_link.html", {"kind": kind, "bot_kind": bot_kind})
