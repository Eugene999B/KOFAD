"""Bounded WhatsApp commands and staff handoff; no arbitrary tool or financial execution."""
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import quote

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import WhatsAppBotContact, WhatsAppBotReply
from .whatsapp_delivery import configuration_error

MARKET = "https://market.kofadimpex.com"
COMPANY = "https://kofadimpex.com"
MENU = (
    "Welcome to KOFAD IMPEX ENTERPRISE. I’m the automated assistant.\n"
    "SHOP — browse products\nSEARCH product name — find an item\n"
    "ORDERS — track orders and payment\nRECEIPTS — view receipts\n"
    "DELIVERY — delivery and pickup information\nRETURNS — returns and refunds\n"
    "ACCOUNT — manage your account\nSTAFF — staff workspace\n"
    "HUMAN — speak with our team\nSTOP — pause this assistant; START — resume.\n"
    "Never send a PIN, password, OTP or card details."
)


def enabled():
    return bool(
        settings.WHATSAPP_BOT_ENABLED and not configuration_error()
        and settings.WHATSAPP_BUSINESS_ACCOUNT_ID
    )


def command_reply(text):
    """Only public information. Identity or authority claimed in a message grants no access."""
    command = text.strip().casefold()
    links = {
        "shop": ("Browse products and prices", MARKET + "/market/"),
        "orders": ("Sign in to see your own orders, delivery updates and verified payment status", MARKET + "/market/orders/"),
        "receipts": ("Sign in and open an order to view your payment and receipt", MARKET + "/market/orders/"),
        "delivery": ("Delivery areas, charges and pickup information", COMPANY + "/delivery/"),
        "returns": ("Read our returns policy; request a return from your signed-in account", COMPANY + "/returns-policy/"),
        "account": ("Sign in to manage your account and contact details", MARKET + "/market/account/"),
        "privacy": ("Privacy and data deletion requests", COMPANY + "/privacy/"),
        "payment": ("Check payment in your signed-in order. If debited, do not pay again while verification is pending", MARKET + "/market/orders/"),
        "staff": ("Staff sign-in: your usual permissions apply", "https://staff.kofadimpex.com/workspace/"),
    }
    if command in links:
        title, url = links[command]
        return title + ":\n" + url
    if command.startswith("search "):
        from marketplace.models import MarketListing
        query = text.strip()[7:].strip()[:80]
        if len(query) < 2:
            return "Type SEARCH followed by at least two letters of the product name."
        rows = MarketListing.objects.filter(enabled=True, product__active=True).filter(
            Q(title__icontains=query) | Q(product__name__icontains=query)
        ).select_related("product")[:5]
        items = [
            f"{row.display_name[:100]} — GHS {row.market_price:.2f} / {row.selling_label[:40]}\n"
            + MARKET + f"/market/products/{row.pk}/"
            for row in rows
        ]
        return ("\n\n".join(items) if items else "No matching products found.") + (
            "\nPrices and availability are confirmed at checkout.\n"
            + MARKET + "/market/?q=" + quote(query)
        )
    return MENU


@transaction.atomic
def receive(event):
    if not enabled() or event.event_type != "message":
        return
    if (event.waba_id != settings.WHATSAPP_BUSINESS_ACCOUNT_ID
            or event.phone_number_id != settings.WHATSAPP_PHONE_NUMBER_ID
            or not re.fullmatch(r"[0-9]{8,15}", event.wa_id)
            or not event.provider_message_id):
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    try:
        timestamp = datetime.fromtimestamp(float(payload.get("timestamp", 0)), tz=dt_timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return
    now = timezone.now()
    if not now - timedelta(hours=24) < timestamp <= now + timedelta(minutes=2):
        return
    contact, _ = WhatsAppBotContact.objects.get_or_create(
        wa_id=event.wa_id, phone_number_id=event.phone_number_id,
    )
    contact = WhatsAppBotContact.objects.select_for_update().get(pk=contact.pk)
    source = "inbound:" + event.provider_message_id[:180]
    if WhatsAppBotReply.objects.filter(source_key=source).exists():
        return
    contact.last_inbound_at = max(contact.last_inbound_at or timestamp, timestamp)
    text = payload.get("text", {}).get("body", "") if isinstance(payload.get("text"), dict) else ""
    text = text[:2000] if isinstance(text, str) else ""
    command = text.strip().casefold()
    status = "queued"
    if command == "stop":
        contact.opted_out = True
        contact.handoff = False
        body = "The KOFAD assistant is paused. Send START to resume. For help visit " + COMPANY + "/contact/"
    elif command == "start":
        contact.opted_out = False
        contact.handoff = False
        body = MENU
    elif contact.opted_out:
        body, status = "", "suppressed"
    elif command == "human" or contact.handoff:
        from marketplace.models import Conversation, ConversationMessage
        if not contact.conversation_id or contact.conversation.status != "open":
            contact.conversation = Conversation.objects.create(
                public_name="WhatsApp customer", public_phone="+" + event.wa_id,
                subject="WhatsApp support · verify identity before sharing account information",
            )
        ConversationMessage.objects.create(
            conversation=contact.conversation, sender_type="customer",
            body=text or "[Non-text WhatsApp message: ask the customer to describe the issue.]",
            read_by_customer=True,
        )
        Conversation.objects.filter(pk=contact.conversation_id).update(updated_at=now)
        body = "Your message is in the KOFAD support inbox. A team member will reply when available. Send START to return to the menu."
        if contact.handoff:
            body, status = "", "handoff"
        contact.handoff = True
    else:
        body = command_reply(text)
    # Bounded auto-response cost: a recipient cannot make an unlimited reply loop.
    if command not in {"stop", "start"} and WhatsAppBotReply.objects.filter(
        contact=contact, created_at__gte=now - timedelta(minutes=1),
    ).count() >= 6:
        body, status = "", "rate_limited"
    contact.save()
    WhatsAppBotReply.objects.create(source_key=source, contact=contact, body=body[:4000], status=status)


def queue_staff_reply(message):
    if message.sender_type != "staff" or not message.body:
        return False
    contact = WhatsAppBotContact.objects.filter(conversation_id=message.conversation_id).first()
    if not contact:
        return False
    WhatsAppBotReply.objects.get_or_create(
        source_key=f"staff:{message.pk}",
        defaults={"contact": contact, "body": "KOFAD support: " + message.body[:3900]},
    )
    return True


def process_replies(limit=10):
    if not enabled():
        return 0
    now = timezone.now()
    # Interrupted submissions are uncertain, never automatically sent twice.
    WhatsAppBotReply.objects.filter(
        status="sending", updated_at__lt=now - timedelta(minutes=5),
    ).update(status="unknown", error="Interrupted submission; check delivery before retrying.")
    ids = list(WhatsAppBotReply.objects.filter(status="queued").order_by("pk").values_list("pk", flat=True)[:limit])
    for pk in ids:
        with transaction.atomic():
            reply = WhatsAppBotReply.objects.select_for_update().select_related("contact").get(pk=pk)
            if reply.status != "queued":
                continue
            contact = WhatsAppBotContact.objects.select_for_update().get(pk=reply.contact_id)
            if (not contact.last_inbound_at or contact.last_inbound_at <= timezone.now() - timedelta(hours=24)
                    or contact.phone_number_id != settings.WHATSAPP_PHONE_NUMBER_ID):
                reply.status, reply.error = "expired", "Customer conversation window closed."
                reply.save()
                continue
            if contact.opted_out and not reply.body.startswith("The KOFAD assistant is paused."):
                reply.status = "suppressed"
                reply.save()
                continue
            reply.status = "sending"
            reply.save()
        status, provider_id, error = "unknown", "", "Delivery outcome is uncertain."
        try:
            response = requests.post(
                f"https://graph.facebook.com/{settings.WHATSAPP_GRAPH_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
                headers={"Authorization": "Bearer " + settings.WHATSAPP_ACCESS_TOKEN},
                json={"messaging_product": "whatsapp", "to": contact.wa_id, "type": "text",
                      "text": {"preview_url": False, "body": reply.body}},
                timeout=settings.WHATSAPP_TIMEOUT_SECONDS, allow_redirects=False,
            )
            data = response.json()
            if 200 <= response.status_code < 300 and isinstance(data, dict):
                rows = data.get("messages")
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    provider_id = str(rows[0].get("id") or "")[:180]
                    if provider_id:
                        status, error = "accepted", ""
            elif 400 <= response.status_code < 500:
                status, error = "failed", "Meta rejected the message. Check account configuration."
        except (requests.RequestException, ValueError, TypeError):
            pass
        WhatsAppBotReply.objects.filter(pk=pk, status="sending").update(
            status=status, provider_id=provider_id, error=error, updated_at=timezone.now(),
        )
        if provider_id:
            from .models import WhatsAppWebhookEvent
            for event in WhatsAppWebhookEvent.objects.filter(
                event_type="status", provider_message_id=provider_id,
                phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
            ).order_by("received_at"):
                reconcile_reply(provider_id, event.status)
    return len(ids)


@transaction.atomic
def reconcile_reply(provider_id, status):
    from .whatsapp import _transition
    reply = WhatsAppBotReply.objects.select_for_update().filter(provider_id=provider_id).first()
    if reply:
        reply.status = _transition(reply.status, status)
        reply.save(update_fields=["status", "updated_at"])
