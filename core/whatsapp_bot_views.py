from django.conf import settings
from django.core.paginator import Paginator
from django.shortcuts import render
from .models import WhatsAppBotReply
from .views import protected
from .whatsapp_bot import enabled


@protected("manage_company")
def dashboard(request, branch):
    rows = WhatsAppBotReply.objects.select_related("contact").order_by("-pk")
    return render(request, "whatsapp_bot.html", {
        "title": "WhatsApp assistant", "bot_ready": enabled(),
        "bot_enabled": settings.WHATSAPP_BOT_ENABLED,
        "page": Paginator(rows, 30).get_page(request.GET.get("page")),
    })
