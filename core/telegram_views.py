"""Telegram inbound webhook. Fail closed; accept only authenticated private chats."""
import hmac
import json
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .telegram_assistant import reply_for
from .telegram_channels import BOT_KINDS, bot_enabled, bot_secret
from .models import TelegramUpdate

WEBHOOK_PATH = "/telegram/webhook/"


@csrf_exempt
@require_POST
def webhook(request, bot_kind="official"):
    # Telegram's secret_token header is independent of the bot API credential.
    if bot_kind not in BOT_KINDS or not bot_enabled(bot_kind):
        return HttpResponse("Telegram integration not configured.", status=503)
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = bot_secret(bot_kind)
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        return HttpResponse("Unauthorized webhook.", status=403)
    if len(request.body) > settings.TELEGRAM_WEBHOOK_MAX_BYTES:
        return HttpResponse("Payload too large.", status=413)
    try:
        payload = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponse("Invalid JSON.", status=400)
    if not isinstance(payload, dict):
        return HttpResponse("Invalid update.", status=400)
    update_id = payload.get("update_id")
    if type(update_id) is not int or update_id < 0:
        return HttpResponse("Invalid update.", status=400)
    message = payload.get("message")
    if not isinstance(message, dict):
        return HttpResponse(status=200)
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return HttpResponse(status=200)
    chat_id, sender_id = chat.get("id"), sender.get("id")
    if (chat.get("type") != "private" or type(chat_id) is not int or chat_id <= 0
            or type(sender_id) is not int or sender_id != chat_id or sender.get("is_bot")):
        return HttpResponse(status=200)
    sent_at = message.get("date")
    if type(sent_at) is not int:
        return HttpResponse(status=200)
    message_time = datetime.fromtimestamp(sent_at, tz=timezone.utc) if 0 <= sent_at <= 4102444800 else None
    now = datetime.now(timezone.utc)
    if message_time is None or not now - timedelta(hours=23) <= message_time <= now + timedelta(minutes=3):
        return HttpResponse(status=200)
    text = message.get("text")
    if not isinstance(text, str) or not text:
        return HttpResponse(status=200)
    # Telegram resends updates when acknowledgement is lost. Only process once.
    _, created = TelegramUpdate.objects.get_or_create(bot_kind=bot_kind, update_id=update_id)
    if not created:
        return HttpResponse(status=200)
    # Telegram supports invoking sendMessage as the HTTP response to a webhook.
    # No token is sent to the client; no separate outbound provider request needed.
    return JsonResponse({
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": reply_for(text, chat_id, sender_id, bot_kind=bot_kind)[:4096],
        "disable_web_page_preview": True,
    })
