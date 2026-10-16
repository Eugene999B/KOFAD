"""Durable transactional order SMS; provider calls happen outside order transactions."""
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Message
from core.sms.service import estimate, normalize_phone, send_message_now, validate_config

logger = logging.getLogger(__name__)


@transaction.atomic
def queue_order_sms(order, event, body):
    """Queue each enabled channel once, independently, after a real order event.

    Nothing goes directly to a provider inside payment/ledger transactions.
    A missing phone must never suppress a verified email or linked Telegram chat.
    """
    if event == "paid" and order.payment_status != "paid":
        # Never announce funds received because of a prompt or callback.
        logger.warning("Refused unverified payment notice for order %s", order.pk)
        return None
    try:
        with transaction.atomic():
            from core.notification_engine import queue_customer_order_email
            queue_customer_order_email(order, event)
    except Exception:
        logger.exception("Transactional email could not be queued for order %s", order.pk)
    if getattr(settings, "TELEGRAM_NOTIFICATIONS_ENABLED", False):
        try:
            with transaction.atomic():
                from core.telegram_integration import queue_order_update
                queue_order_update(order, event)
        except Exception:
            logger.exception("Telegram order notice could not be queued for order %s", order.pk)
    if not settings.SMS_ENABLED:
        return None
    try:
        from .services import _system_actor
        encoding, segments = estimate(body)
        phone = normalize_phone(order.phone)
        with transaction.atomic():
            message, _ = Message.objects.get_or_create(
                branch=order.branch,
                source_key=f"market-event:{order.pk}:{event}",
                defaults={
                    "created_by": _system_actor(), "channel": "sms", "body": body,
                    "recipient": phone, "recipient_name": order.recipient_name,
                    "manual_override": True, "encoding": encoding, "segments": segments,
                    "status": "draft",
                },
            )
        return message
    except (ValidationError, ValueError):
        logger.warning("Order SMS unavailable for order %s: invalid recipient/message", order.pk)
    except Exception:
        logger.exception("Order SMS could not be queued for order %s", order.pk)
    return None


def process_order_sms(limit=10):
    if not settings.SMS_ENABLED:
        return 0
    try:
        validate_config(settings.SMS_PROVIDER)
    except ValidationError:
        return 0
    rows = list(Message.objects.filter(
        source_key__startswith="market-event:", status="draft",
        channel="sms", archived_at__isnull=True,
    ).select_related("branch", "created_by").order_by("created_at")[:limit])
    submitted = 0
    for message in rows:
        try:
            # The existing sender locks and claims draft -> sending before I/O.
            # Unknown submissions remain for reconciliation, never blind retries.
            send_message_now(message.created_by, message.branch, message.pk, automatic=True)
            submitted += 1
        except ValidationError:
            # Another worker or an operator may already have claimed this draft.
            continue
        except Exception:
            logger.exception("Order SMS processing failed for message %s", message.pk)
    return submitted
