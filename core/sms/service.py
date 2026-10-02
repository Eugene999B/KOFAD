import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.models import Message, SmsAttempt, SmsEvent
from core.services import audit, permit
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


def validate_config(provider):
    if not settings.SMS_ENABLED:
        raise ValidationError("SMS is disabled. Configure the service before queueing.")
    origin = urlsplit(settings.SMS_PUBLIC_ORIGIN)
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment or origin.path:
        raise ValidationError("SMS_PUBLIC_ORIGIN must be the application's HTTPS origin.")
    get_provider(provider).validate()


@transaction.atomic
def create_draft(user,branch,party,body,channel="sms",source_key=None):
    permit(user,branch,"operate_sales" if not user.has_perm("core.send_messages") else "send_messages")
    if party.branch_id != branch.pk or not party.consent:
        raise ValidationError("Choose an opted-in contact at this location.")
    if channel not in ("sms","whatsapp"):
        raise ValidationError("Unknown channel.")
    body = body.strip()
    encoding,segments = estimate(body)
    recipient = normalize_phone(party.phone)
    if source_key:
        existing = Message.objects.filter(branch=branch,source_key=source_key).first()
        if existing:
            return existing
    message = Message.objects.create(branch=branch,party=party,created_by=user,body=body,channel=channel,
        recipient=recipient,encoding=encoding,segments=segments,source_key=source_key)
    audit(user,branch,"message.drafted",message.pk,{"channel":channel,"segments":segments})
    return message


@transaction.atomic
def queue_message(user,branch,pk,retry=False):
    permit(user,branch,"send_messages")
    message = Message.objects.select_for_update().select_related("party").get(pk=pk,branch=branch)
    allowed = ("failed","undelivered","expired") if retry else ("draft",)
    if message.status not in allowed:
        raise ValidationError("Only drafts or definitively failed messages can be queued. Unknown outcomes require provider investigation.")
    if message.channel != "sms":
        raise ValidationError("WhatsApp delivery is not configured.")
    if not message.party.consent or normalize_phone(message.party.phone) != message.recipient:
        raise ValidationError("Consent or recipient details changed. Prepare a new draft.")
    provider = message.provider or settings.SMS_PROVIDER
    validate_config(provider)
    if message.attempts >= settings.SMS_MAX_ATTEMPTS:
        raise ValidationError("This message reached its attempt limit.")
    message.provider = provider
    message.sender = message.sender or settings.SMS_SENDER_ID
    if not retry:
        message.sandbox = settings.SMS_SANDBOX
    message.status = "queued"
    message.queued_by = user
    message.next_attempt_at = timezone.now()
    message.last_error = ""
    message.save()
    audit(user,branch,"sms.queued",message.pk,{"provider":provider,"sandbox":message.sandbox,"retry":retry})
    return message


def transition(current,incoming):
    if current == "delivered" or incoming == "delivered":
        return "delivered"
    if current in FINAL:
        return current
    return incoming


def process_one():
    # Validate before claiming. A misconfigured worker leaves the queue intact.
    with transaction.atomic():
        message = Message.objects.select_for_update(skip_locked=True).filter(status__in=["queued","retry_wait"],
            next_attempt_at__lte=timezone.now()).order_by("created_at").first()
        if not message:
            return False
        validate_config(message.provider)
        try:
            permit(message.queued_by,message.branch,"send_messages")
            if not message.party.consent or normalize_phone(message.party.phone) != message.recipient:
                raise ValidationError("Consent or recipient changed.")
        except Exception:
            message.status,message.last_error = "failed","Recipient consent or sender access no longer valid."
            message.save(update_fields=["status","last_error"])
            audit(None,message.branch,"sms.blocked",message.pk)
            return True
        token = secrets.token_urlsafe(32)
        message.attempts += 1
        attempt = SmsAttempt.objects.create(message=message,number=message.attempts,provider=message.provider,
            callback_digest=hashlib.sha256(token.encode()).hexdigest())
        message.status = "sending"
        message.save(update_fields=["status","attempts"])
    callback = settings.SMS_PUBLIC_ORIGIN + f"/sms/callback/{attempt.pk}/?" + urlencode({"token":token})
    try:
        result = get_provider(message.provider).submit(message.recipient,message.body,message.sender,callback,message.sandbox)
    except Exception:
        # Adapter bugs or interrupted transport cannot establish whether a charge occurred.
        from .providers import Submission
        result = Submission("unknown",error_code="adapter_outcome_unknown")
    with transaction.atomic():
        locked = Message.objects.select_for_update().get(pk=message.pk)
        current = SmsAttempt.objects.select_for_update().get(pk=attempt.pk)
        incoming = result.status
        if incoming == "retry_wait" and locked.attempts >= settings.SMS_MAX_ATTEMPTS:
            incoming = "failed"
        current.status = transition(current.status,incoming)
        if not current.provider_id:
            current.provider_id = result.provider_id
        current.http_status,current.error_code = result.http_status,result.error_code
        current.save()
        locked.status = "simulated" if locked.sandbox and current.status in ("accepted","delivered") else current.status
        locked.last_error = result.error_code
        if locked.status == "retry_wait":
            locked.next_attempt_at = timezone.now()+timedelta(seconds=60*2**(locked.attempts-1))
        locked.save(update_fields=["status","last_error","next_attempt_at"])
        audit(locked.queued_by,locked.branch,"sms."+locked.status,locked.pk,{"attempt":str(current.pk),"provider":locked.provider})
    return True


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
