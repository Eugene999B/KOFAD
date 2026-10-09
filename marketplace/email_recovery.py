"""Verified email recovery for customer accounts, with rate limits and one-use codes."""
import hmac
import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from core.email_identity import delivery_ready, _send_kofad_mail, normalize_email
from .models import CustomerAccount, CustomerEmailRecovery, EmailIdentity
from .services import _consume_customer_otp_budget


def _digest(challenge, code):
    return salted_hmac("kofad-customer-reset-email-v1",
                      f"{challenge.pk}:{code}", algorithm="sha256").hexdigest()


def _stamp(customer):
    return salted_hmac("kofad-customer-reset-password-v1",
                      customer.password_hash, algorithm="sha256").hexdigest()


def resolve_verified(email):
    address = normalize_email(email)
    matches = list(EmailIdentity.objects.filter(
        kind="customer", email=address, verified_at__isnull=False
    ).values_list("owner_id", flat=True)[:2])
    if len(matches) != 1:
        return None
    return CustomerAccount.objects.filter(pk=matches[0], active=True).first()


def issue(email, request):
    """Returns a challenge ID only if a verified customer email owns the account."""
    if not delivery_ready():
        raise ValidationError("Email recovery is not connected yet. Try SMS or customer care.")
    _consume_customer_otp_budget(request)
    address = normalize_email(email)
    customer = resolve_verified(address)
    if not customer:
        return None
    now = timezone.now()
    with transaction.atomic():
        customer = CustomerAccount.objects.select_for_update().get(pk=customer.pk)
        recent = CustomerEmailRecovery.objects.filter(
            customer=customer, sent=True, used=False,
            created_at__gte=now - timedelta(seconds=60),
        ).order_by("-created_at").first()
        if recent:
            return recent.pk
        CustomerEmailRecovery.objects.filter(customer=customer, used=False).update(used=True)
        code = f"{secrets.randbelow(1000000):06d}"
        row = CustomerEmailRecovery.objects.create(
            customer=customer, email=address, code_digest="",
            password_stamp=_stamp(customer),
            expires_at=now + timedelta(minutes=10),
        )
        row.code_digest = _digest(row, code)
        row.save(update_fields=["code_digest"])
    try:
        _send_kofad_mail(
            "KOFAD Market password recovery",
            f"Your KOFAD Market password reset code is {code}. It expires in 10 minutes. "
            "Do not share this code. If you did not request it, ignore this message.",
            [address], purpose="security",
        )
    except Exception as exc:
        CustomerEmailRecovery.objects.filter(pk=row.pk).update(used=True)
        raise ValidationError("We could not send a recovery code. Please try later.") from exc
    CustomerEmailRecovery.objects.filter(pk=row.pk).update(sent=True)
    return row.pk


def _eligible(row):
    if not row or not row.sent or row.used or row.attempts >= 5:
        return False
    if row.expires_at <= timezone.now() or not row.customer.active:
        return False
    if not hmac.compare_digest(row.password_stamp, _stamp(row.customer)):
        return False
    return EmailIdentity.objects.filter(
        kind="customer", owner_id=row.customer_id, email=row.email,
        verified_at__isnull=False,
    ).exists()


def verify(challenge_id, code):
    raw = "".join(ch for ch in (code or "").strip() if ch not in " -")
    if len(raw) != 6 or not raw.isascii() or not raw.isdecimal():
        raise ValidationError("Enter the six-digit email verification code.")
    with transaction.atomic():
        row = CustomerEmailRecovery.objects.select_for_update().select_related("customer").filter(
            pk=challenge_id).first()
        if not _eligible(row) or row.verified_at:
            raise ValidationError("That recovery code has expired or is unavailable.")
        row.attempts += 1
        if not hmac.compare_digest(row.code_digest, _digest(row, raw)):
            row.save(update_fields=["attempts"])
            raise ValidationError("That recovery code was not accepted.")
        row.verified_at = timezone.now()
        row.save(update_fields=["attempts", "verified_at"])
        return row.pk


def finish(challenge_id, password):
    with transaction.atomic():
        hint = CustomerEmailRecovery.objects.filter(pk=challenge_id).values("customer_id").first()
        if not hint:
            raise ValidationError("Recovery expired; request a fresh code.")
        customer = CustomerAccount.objects.select_for_update().filter(pk=hint["customer_id"]).first()
        row = CustomerEmailRecovery.objects.select_for_update().select_related("customer").filter(
            pk=challenge_id).first()
        if (not customer or not _eligible(row) or not row.verified_at
            or timezone.now() - row.verified_at > timedelta(minutes=10)):
            raise ValidationError("Recovery expired; request a fresh code.")
        customer.set_password(password)
        customer.save(update_fields=["password_hash"])
        CustomerEmailRecovery.objects.filter(customer=customer, used=False).update(used=True)
        return customer
