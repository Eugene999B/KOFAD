import hashlib
import hmac
import re
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.models import Message, SmsAttempt, SmsEvent
from core.services import audit, permit, lock_branch
from .providers import get_provider

GSM_BASIC = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM_EXTRA = set("^{}\\[~]|€\f")
FINAL = {"delivered","undelivered","expired","failed","simulated"}
CALLBACK_STATUSES = {"DELIVERED":"delivered","SUBMITTED":"accepted","QUEUED":"accepted",
                     "NOT_DELIVERED":"undelivered","PROHIBITED":"undelivered","EXPIRED":"expired"}


def normalize_phone(raw):
    raw = str(raw).strip()
    if not re.fullmatch(r"[+0-9 ()-]+",raw):
        raise ValidationError("Enter one valid phone number.")
    digits = re.sub(r"[ ()-]","",raw)
    if digits.startswith("00"):
        digits = "+"+digits[2:]
    if digits.startswith("0") and len(digits)==10:
        digits = "+233"+digits[1:]
    elif digits.startswith("233") and len(digits)==12:
        digits = "+"+digits
    elif len(digits)==9 and digits.isdigit():
        digits = "+233"+digits
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}",digits):
        raise ValidationError("Use international format, such as +233241234567.")
    return digits


def estimate(body):
    if not body or len(body)>2000:
        raise ValidationError("SMS must contain between 1 and 2,000 characters.")
    gsm = all(c in GSM_BASIC or c in GSM_EXTRA for c in body)
    weights = [2 if c in GSM_EXTRA else 1 for c in body] if gsm else [len(c.encode("utf-16-be"))//2 for c in body]
    total = sum(weights)
    single, multipart = (160,153) if gsm else (70,67)
    count = 1
    if total > single:
        count,used = 1,0
        for weight in weights:
            if used+weight > multipart:
                count += 1
                used = 0
            used += weight
    if count > 10:
        raise ValidationError("This message exceeds the ten-segment limit.")
    return ("gsm7" if gsm else "utf16"),count


def validate_current_context(message):
    if message.source_key and message.source_key.startswith("debt:"):
        from core.models import Document
        from .templates import render_for_document
        reference = message.source_key.split(":")[1]
        document = Document.objects.get(pk=reference,branch=message.branch,party=message.party)
        if render_for_document(document,"debt") != message.body:
            raise ValidationError("The outstanding balance or template changed. Prepare the reminder again.")
    if message.source_key and message.source_key.startswith("auto:debt:"):
        if not message.party_id:
            raise ValidationError("Automatic debt reminder lost its customer account.")
        from core.automations import render_debt_account_message
        current = render_debt_account_message(message.party)
        if not current or current != message.body:
            raise ValidationError("The customer debt or reminder policy changed. Prepare a fresh reminder.")


def validate_config(provider):
    if not settings.SMS_ENABLED:
        raise ValidationError("SMS is disabled. Configure the service before queueing.")
    origin = urlsplit(settings.SMS_PUBLIC_ORIGIN)
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment or origin.path:
        raise ValidationError("SMS_PUBLIC_ORIGIN must be the application's HTTPS origin.")
    get_provider(provider).validate()


@transaction.atomic
def _create_draft_record(user, branch, body, channel="sms", source_key=None, party=None, management_contact=None):
    if bool(party) == bool(management_contact):
        raise ValidationError("Choose exactly one message recipient.")
    if channel not in ("sms", "whatsapp"):
        raise ValidationError("Unknown channel.")
    body = body.strip()
    if channel == "whatsapp":
        if not body or len(body) > 4096:
            raise ValidationError("WhatsApp messages must contain 1 to 4,096 characters.")
        encoding, segments = "unicode", 1
    else:
        encoding, segments = estimate(body)
    if party:
        if party.branch_id != branch.pk or not party.consent:
            raise ValidationError("Choose an opted-in contact at this location.")
        recipient = normalize_phone(party.phone)
    else:
        if not management_contact.active or (
            management_contact.branch_id and management_contact.branch_id != branch.pk
        ):
            raise ValidationError("This management contact is not active for the current location.")
        recipient = normalize_phone(management_contact.phone)

    if source_key:
        existing = Message.objects.filter(branch=branch, source_key=source_key).first()
        if existing:
            same_recipient = (
                existing.party_id == getattr(party, "pk", None)
                and existing.management_contact_id == getattr(management_contact, "pk", None)
            )
            if not same_recipient or existing.channel != channel:
                raise ValidationError("This draft key already belongs to another message.")
            if existing.status == "draft":
                existing.body, existing.encoding, existing.segments = body, encoding, segments
                existing.recipient = recipient
                existing.save(update_fields=["body", "encoding", "segments", "recipient"])
            return existing

    message = Message.objects.create(
        branch=branch, party=party, management_contact=management_contact, created_by=user,
        body=body, channel=channel, recipient=recipient, encoding=encoding, segments=segments,
        source_key=source_key,
    )
    audit(user, branch, "message.drafted", message.pk, {
        "channel": channel, "segments": segments,
        "recipient_type": "customer" if party else "management",
    })
    return message


@transaction.atomic
def create_draft(user, branch, party, body, channel="sms", source_key=None):
    permit(user, branch, "operate_sales" if not user.has_perm("core.send_messages") else "send_messages")
    lock_branch(branch)
    return _create_draft_record(user, branch, body, channel, source_key, party=party)


@transaction.atomic
def create_automatic_customer_draft(user, branch, party, body, source_key=None, channel="sms"):
    lock_branch(branch)
    return _create_draft_record(user, branch, body, channel, source_key, party=party)


@transaction.atomic
def create_internal_draft(user, branch, management_contact, body, source_key=None, channel="sms"):
    lock_branch(branch)
    return _create_draft_record(
        user, branch, body, channel, source_key, management_contact=management_contact
    )


@transaction.atomic
def create_direct_draft(user, branch, body, *, channel="sms", source_key=None, party=None, phone="", label=""):
    """Create an explicitly staff-directed message without the automation consent gate."""
    if channel not in ("sms", "whatsapp"):
        raise ValidationError("Unknown channel.")
    if channel == "sms":
        permit(user, branch, "send_messages")
    else:
        permit(user, branch, "operate_sales" if not user.has_perm("core.send_messages") else "send_messages")
    lock_branch(branch)

    body = str(body or "").strip()
    if channel == "whatsapp":
        if not body or len(body) > 4096:
            raise ValidationError("WhatsApp messages must contain 1 to 4,096 characters.")
        encoding, segments = "unicode", 1
    else:
        encoding, segments = estimate(body)
    if party:
        if party.branch_id != branch.pk or party.kind != "customer":
            raise ValidationError("Choose a customer from the current location.")
        recipient = normalize_phone(party.phone)
        recipient_name = party.name
    else:
        recipient = normalize_phone(phone)
        recipient_name = str(label or "Manual number").strip()[:120]

    existing = Message.objects.filter(branch=branch, source_key=source_key).first() if source_key else None
    if existing:
        return existing

    message = Message.objects.create(
        branch=branch,
        party=party,
        created_by=user,
        body=body,
        channel=channel,
        recipient=recipient,
        recipient_name=recipient_name,
        manual_override=True,
        encoding=encoding,
        segments=segments,
        source_key=source_key,
        status="draft",
    )
    audit(user, branch, "message.manual_prepared", message.pk, {
        "channel": channel,
        "segments": segments,
        "recipient": recipient,
        "recipient_type": "customer" if party else "manual",
    })
    return message


def _recipient_is_current(message):
    if message.manual_override:
        if message.party_id:
            return (
                message.party.branch_id == message.branch_id
                and normalize_phone(message.party.phone) == message.recipient
            )
        try:
            return normalize_phone(message.recipient) == message.recipient
        except ValidationError:
            return False
    if message.party_id:
        return (
            message.party.branch_id == message.branch_id
            and message.party.consent
            and normalize_phone(message.party.phone) == message.recipient
        )
    if message.management_contact_id:
        contact = message.management_contact
        return (
            contact.active
            and (not contact.branch_id or contact.branch_id == message.branch_id)
            and normalize_phone(contact.phone) == message.recipient
        )
    return False


def _automation_sender(actor):
    if actor and actor.is_active and actor.has_perm("core.send_messages"):
        return actor
    from django.contrib.auth.models import User
    return User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()


def send_automatic(message, actor=None):
    """Submit an eligible automatic SMS immediately when live SMS is enabled."""
    if message.status != "draft" or message.channel != "sms" or not settings.SMS_ENABLED:
        return message
    sender = _automation_sender(actor)
    if not sender:
        raise ValidationError("No active administrator is available to authorize automatic SMS.")
    return send_message_now(sender, message.branch, message.pk, automatic=True)


def _delivery_callback_token():
    return hmac.new(
        settings.SECRET_KEY.encode(),
        b"kofad-sms-delivery-v1",
        hashlib.sha256,
    ).hexdigest()


def _delivery_callback_url():
    origin = settings.SMS_PUBLIC_ORIGIN.rstrip("/")
    return origin + "/sms/delivery/?" + urlencode({"token": _delivery_callback_token()})


def _prepare_direct_attempt(user, message, retry=False):
    allowed = ("failed", "undelivered", "expired") if retry else ("draft",)
    if message.status not in allowed:
        raise ValidationError("Only a new message or a definitively failed SMS can be sent.")
    if message.channel != "sms":
        raise ValidationError("Only SMS can be submitted to Arkesel.")
    if not _recipient_is_current(message):
        raise ValidationError("Recipient details or sending eligibility changed. Prepare a new message.")
    validate_current_context(message)
    provider = message.provider or settings.SMS_PROVIDER
    validate_config(provider)
    if message.attempts >= settings.SMS_MAX_ATTEMPTS:
        raise ValidationError("This message reached its attempt limit.")

    message.provider = provider
    message.sender = message.sender or settings.SMS_SENDER_ID
    if not retry:
        # Online-order lifecycle notices are transactional receipts/status updates that the
        # customer explicitly expects. Keep general messaging in the configured sandbox,
        # but never silently simulate these verified-order notices.
        transactional_market_notice = str(message.source_key or "").startswith("market-event:")
        message.sandbox = False if transactional_market_notice else settings.SMS_SANDBOX
    message.attempts += 1
    message.status = "sending"
    message.submitted_by = user
    message.last_error = ""
    message.save(update_fields=[
        "provider", "sender", "sandbox", "attempts", "status",
        "submitted_by", "last_error",
    ])
    attempt = SmsAttempt.objects.create(
        message=message,
        number=message.attempts,
        provider=provider,
        callback_digest=hashlib.sha256(_delivery_callback_token().encode()).hexdigest(),
    )
    return attempt


def _finalize_direct_result(message_id, attempt_id, result, actor):
    with transaction.atomic():
        message = Message.objects.select_for_update().get(pk=message_id)
        attempt = SmsAttempt.objects.select_for_update().get(pk=attempt_id)
        incoming = result.status
        if incoming == "retry_wait":
            incoming = "failed"
            detail = result.error_detail or "Arkesel rate limit reached. Try again shortly."
        else:
            detail = result.error_detail
        attempt.status = incoming
        attempt.provider_id = result.provider_id or attempt.provider_id
        attempt.http_status = result.http_status
        attempt.error_code = result.error_code
        attempt.save()

        message.status = "simulated" if message.sandbox and incoming in ("accepted", "delivered") else incoming
        message.last_error = (detail or result.error_code or "")[:240]
        message.save(update_fields=["status", "last_error"])
        audit(actor, message.branch, "sms." + message.status, message.pk, {
            "attempt": str(attempt.pk),
            "provider": message.provider,
            "provider_id": attempt.provider_id,
            "http_status": attempt.http_status,
            "error_code": attempt.error_code,
            "direct": True,
        })
        return message


def send_messages_now(user, branch, message_ids, retry=False, automatic=False):
    """Submit staff/automatic SMS to Arkesel now. No intermediate queued state."""
    if not automatic:
        permit(user, branch, "send_messages")
    ids = list(dict.fromkeys(message_ids))
    if not ids:
        raise ValidationError("Choose at least one message to send.")

    prepared = []
    with transaction.atomic():
        messages = list(
            Message.objects.select_for_update(of=("self",))
            .select_related("party", "management_contact")
            .filter(pk__in=ids, branch=branch)
            .order_by("created_at", "pk")
        )
        if len(messages) != len(ids):
            raise ValidationError("One or more SMS messages could not be found.")
        for message in messages:
            attempt = _prepare_direct_attempt(user, message, retry=retry)
            prepared.append((message.pk, attempt.pk, message.provider, message.sender, message.sandbox, message.body, message.recipient))

    # Group identical message bodies so multi-recipient campaigns use one Arkesel API call.
    groups = {}
    for row in prepared:
        key = (row[2], row[3], row[4], row[5])
        groups.setdefault(key, []).append(row)

    results_by_message = {}
    callback_url = _delivery_callback_url()
    for (provider, sender, sandbox, body), rows in groups.items():
        recipients = list(dict.fromkeys(row[6] for row in rows))
        try:
            results = get_provider(provider).submit_many(recipients, body, sender, callback_url, sandbox)
        except Exception as exc:
            from .providers import Submission
            results = [
                Submission(
                    "unknown",
                    error_code="adapter_outcome_unknown",
                    error_detail=f"Provider result is unknown: {exc}",
                    recipient=recipient,
                )
                for recipient in recipients
            ]
        by_recipient = {result.recipient: result for result in results}
        for message_id, attempt_id, _, _, _, _, recipient in rows:
            result = by_recipient.get(recipient)
            if result is None:
                from .providers import Submission
                result = Submission(
                    "unknown",
                    error_code="missing_provider_result",
                    error_detail="Arkesel did not return a result for this recipient.",
                    recipient=recipient,
                )
            results_by_message[message_id] = _finalize_direct_result(
                message_id, attempt_id, result, user
            )

    return [results_by_message[pk] for pk in ids]


def send_message_now(user, branch, pk, retry=False, automatic=False):
    return send_messages_now(user, branch, [pk], retry=retry, automatic=automatic)[0]


def transition(current,incoming):
    if current == "delivered" or incoming == "delivered":
        return "delivered"
    if current in FINAL:
        return current
    return incoming


@transaction.atomic
def receive_callback(attempt_id,token,sms_id,status):
    # Lock order matches submission completion: message first, then its attempt.
    hint = SmsAttempt.objects.filter(pk=attempt_id).values("message_id","callback_digest").first()
    if not hint or not token or not hmac.compare_digest(hint["callback_digest"],hashlib.sha256(token.encode()).hexdigest()):
        return False
    message = Message.objects.select_for_update().get(pk=hint["message_id"])
    attempt = SmsAttempt.objects.select_for_update().get(pk=attempt_id)
    mapped = CALLBACK_STATUSES.get(status.upper())
    if not mapped or not sms_id or len(sms_id)>180:
        return False
    if attempt.provider_id and not hmac.compare_digest(attempt.provider_id,sms_id):
        return False
    fingerprint = hashlib.sha256(f"{attempt.pk}:{sms_id}:{mapped}".encode()).hexdigest()
    _,created = SmsEvent.objects.get_or_create(fingerprint=fingerprint,
        defaults={"attempt":attempt,"provider_id":sms_id,"status":mapped})
    if created:
        attempt.provider_id = sms_id
        attempt.status = "simulated" if message.sandbox else transition(attempt.status,mapped)
        attempt.save()
        if attempt.number == message.attempts:
            message.status = attempt.status
            message.save(update_fields=["status"])
        audit(None,message.branch,"sms.callback",message.pk,{"status":attempt.status,"attempt":str(attempt.pk)})
    return True


def _map_provider_status(value):
    status = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if status in {"DELIVERED", "DELIVERED_TO_HANDSET"}:
        return "delivered"
    if status in {"NOT_DELIVERED", "UNDELIVERED", "PROHIBITED", "REJECTED"}:
        return "undelivered"
    if status in {"EXPIRED"}:
        return "expired"
    if status in {"FAILED", "ERROR"}:
        return "failed"
    if status in {"SUBMITTED", "QUEUED", "SENT", "ACCEPTED", "SUCCESS"}:
        return "accepted"
    return ""


def receive_delivery_callback(token, sms_id, status):
    expected = _delivery_callback_token()
    if not token or not hmac.compare_digest(expected, token):
        return False
    sms_id = str(sms_id or "").strip()
    mapped = _map_provider_status(status)
    if not sms_id or len(sms_id) > 180 or not mapped:
        return False

    hint = SmsAttempt.objects.filter(provider_id=sms_id).values("pk", "message_id").order_by("-started_at").first()
    if not hint:
        return False
    with transaction.atomic():
        message = Message.objects.select_for_update().get(pk=hint["message_id"])
        attempt = SmsAttempt.objects.select_for_update().get(pk=hint["pk"])
        fingerprint = hashlib.sha256(f"global:{attempt.pk}:{sms_id}:{mapped}".encode()).hexdigest()
        _, created = SmsEvent.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={"attempt": attempt, "provider_id": sms_id, "status": mapped},
        )
        if created:
            attempt.status = "simulated" if message.sandbox else transition(attempt.status, mapped)
            attempt.save(update_fields=["status", "updated_at"])
            if attempt.number == message.attempts:
                message.status = attempt.status
                if message.status in {"delivered", "accepted"}:
                    message.last_error = ""
                message.save(update_fields=["status", "last_error"])
            audit(None, message.branch, "sms.callback", message.pk, {
                "status": attempt.status,
                "attempt": str(attempt.pk),
                "provider_id": sms_id,
            })
    return True


def sync_delivery_reports(limit=200):
    """Fallback to Arkesel message reports when callbacks are delayed or missed."""
    cutoff = timezone.now() - timedelta(seconds=8)
    attempts = list(
        SmsAttempt.objects.select_related("message")
        .filter(
            provider="arkesel",
            provider_id__gt="",
            status__in=["accepted", "sending"],
            updated_at__lte=cutoff,
            message__status__in=["accepted", "sending"],
        )
        .order_by("updated_at")[:limit]
    )
    if not attempts:
        return 0
    try:
        reports = get_provider("arkesel").reports([attempt.provider_id for attempt in attempts])
    except ValidationError:
        return 0

    changed = 0
    for attempt in attempts:
        entry = reports.get(attempt.provider_id)
        if not isinstance(entry, dict):
            # Touch the attempt so we do not hammer the provider every second.
            SmsAttempt.objects.filter(pk=attempt.pk).update(updated_at=timezone.now())
            continue
        raw_status = (
            entry.get("status") or entry.get("message_status") or entry.get("messageStatus")
            or entry.get("delivery_status") or entry.get("deliveryStatus")
            or entry.get("sms_status") or entry.get("state")
        )
        mapped = _map_provider_status(raw_status)
        if not mapped:
            SmsAttempt.objects.filter(pk=attempt.pk).update(updated_at=timezone.now())
            continue
        with transaction.atomic():
            message = Message.objects.select_for_update().get(pk=attempt.message_id)
            locked = SmsAttempt.objects.select_for_update().get(pk=attempt.pk)
            new_status = transition(locked.status, mapped)
            locked.status = "simulated" if message.sandbox else new_status
            locked.save(update_fields=["status", "updated_at"])
            if locked.number == message.attempts and message.status != locked.status:
                message.status = locked.status
                if message.status in {"delivered", "accepted"}:
                    message.last_error = ""
                message.save(update_fields=["status", "last_error"])
                changed += 1
                audit(None, message.branch, "sms.delivery_sync", message.pk, {
                    "status": message.status,
                    "attempt": str(locked.pk),
                    "provider_id": locked.provider_id,
                })
    return changed


@transaction.atomic
def recover_stale():
    old = timezone.now()-timedelta(minutes=5)
    count = 0
    for message in Message.objects.select_for_update(of=("self",),skip_locked=True).filter(status="sending",delivery_attempts__number=F("attempts"),delivery_attempts__started_at__lt=old):
        attempt = message.delivery_attempts.filter(number=message.attempts,status="sending",started_at__lt=old).first()
        if not attempt:
            continue
        attempt.status,attempt.error_code = "unknown","worker_interrupted"
        attempt.save()
        message.status,message.last_error = "unknown","Worker interrupted; verify with provider before any resend."
        message.save(update_fields=["status","last_error"])
        audit(None,message.branch,"sms.recovered_unknown",message.pk)
        count += 1
    return count
