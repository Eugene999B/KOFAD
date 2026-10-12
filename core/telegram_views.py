"""Telegram inbound webhook. Fail closed; accept only authenticated private chats."""
import hmac
import json
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .telegram_assistant import reply_for
from .telegram_integration import accept_telegram_contact, awaiting_phone_contact
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
    contact = message.get("contact")
    if not (isinstance(text, str) and text) and not isinstance(contact, dict):
        return HttpResponse(status=200)
    reply_markup = None
    # Telegram resends updates when acknowledgement is lost. Only process once.
    with transaction.atomic():
        _, created = TelegramUpdate.objects.get_or_create(bot_kind=bot_kind, update_id=update_id)
        if not created:
            return HttpResponse(status=200)
        if isinstance(contact, dict):
            response_text = accept_telegram_contact(bot_kind, sender_id, chat_id, contact)
            reply_markup = {"remove_keyboard": True}
        elif text.strip().partition(" ")[0].partition("@")[0].lower() == "/sharephone":
            if awaiting_phone_contact(bot_kind, sender_id, chat_id):
                response_text = (
                    "Tap the button below to share your OWN Telegram account number. "
                    "The KOFAD website must already have the same number in the pending pairing."
                )
                reply_markup = {
                    "keyboard": [[{"text": "Share my Telegram number", "request_contact": True}]],
                    "one_time_keyboard": True,
                    "resize_keyboard": True,
                }
            else:
                response_text = (
                    "Phone verification is optional. First sign in to KOFAD, "
                    "enter a number on the staff Telegram linking page and open its bot link."
                )
        else:
            # Failed reply generation must roll back the replay marker for safe retry.
            response_text = reply_for(text, chat_id, sender_id, bot_kind=bot_kind)[:4096]
    result = {
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": response_text[:4096],
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        result["reply_markup"] = reply_markup
    return JsonResponse(result)
