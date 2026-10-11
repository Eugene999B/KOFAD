"""Cost-conscious, explicit-consent FCM HTTP v1 order notifications.

Uses the existing communications worker, NOT a new always-on Railway service.
Provider credentials come only from protected deployment environment settings.
Claim-once sends are never retried blindly after network uncertainty.
"""
import base64
import json
import logging
import re
import time
from datetime import timedelta

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .mobile_identity import _valid_device
from .mobile_push import _cipher
from .mobile_push_models import MobilePushOutbox, MobilePushSubscription

logger=logging.getLogger(__name__)
SCOPE="https://www.googleapis.com/auth/firebase.messaging"
GOOGLE_TOKEN_URL="https://oauth2.googleapis.com/token"
PROJECT=re.compile(r"^[a-z0-9][a-z0-9-]{4,64}$")
MAX_BATCH=10
MAX_AGE=timedelta(hours=24)


def delivery_ready():
    gates = bool(
        getattr(settings,"KOFAD_NATIVE_AUTH_ENABLED",False)
        and getattr(settings,"KOFAD_NATIVE_PUSH_ENABLED",False)
        and getattr(settings,"KOFAD_NATIVE_PUSH_DELIVERY_ENABLED",False)
        and PROJECT.fullmatch(getattr(settings,"KOFAD_FCM_PROJECT_ID","") or "")
        and getattr(settings,"KOFAD_FCM_SERVICE_ACCOUNT_B64","")
    )
    if not gates:
        return False
    try:
        # Never advertise background delivery for malformed project or key data.
        config = _credentials()
        key = serialization.load_pem_private_key(
            config["private_key"].encode("utf8"), password=None,
        )
        return key.key_size >= 2048
    except (ValueError, TypeError, KeyError, OSError):
        return False


def _encode_json(value):
    raw=json.dumps(value,separators=(",",":"),sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _credentials():
    encoded=settings.KOFAD_FCM_SERVICE_ACCOUNT_B64
    if not isinstance(encoded,str) or len(encoded)<100 or len(encoded)>35000:
        raise ValueError("FCM credentials not configured")
    try:
        raw=base64.b64decode(encoded,validate=True)
        data=json.loads(raw)
    except (ValueError,UnicodeDecodeError,TypeError):
        raise ValueError("FCM credentials invalid") from None
    if not isinstance(data,dict) or data.get("type")!="service_account":
        raise ValueError("FCM server credentials not a service account")
    if data.get("project_id")!=settings.KOFAD_FCM_PROJECT_ID:
        raise ValueError("FCM project mismatch")
    if data.get("token_uri")!=GOOGLE_TOKEN_URL:
        raise ValueError("Untrusted FCM OAuth endpoint")
    email=data.get("client_email")
    private=data.get("private_key")
    if not isinstance(email,str) or not email.endswith(".iam.gserviceaccount.com"):
        raise ValueError("FCM service identity missing")
    if not isinstance(private,str) or not private.startswith("-----BEGIN PRIVATE KEY-----"):
        raise ValueError("FCM signing key missing")
    return data


def _access_token():
    cred=_credentials()
    now=int(time.time())
    header=_encode_json({"alg":"RS256","typ":"JWT"})
    body=_encode_json({
        "iss":cred["client_email"],"scope":SCOPE,"aud":GOOGLE_TOKEN_URL,
        "iat":now,"exp":now+1200,
    })
    unsigned=(header+"."+body).encode("ascii")
    key=serialization.load_pem_private_key(cred["private_key"].encode(),password=None)
    signature=key.sign(unsigned,padding.PKCS1v15(),hashes.SHA256())
    assertion=header+"."+body+"."+base64.urlsafe_b64encode(signature).decode().rstrip("=")
    response=requests.post(GOOGLE_TOKEN_URL,data={
        "grant_type":"urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion":assertion,
    },timeout=(3,8))
    response.raise_for_status()
    result=response.json()
    token=result.get("access_token")
    if not isinstance(token,str) or len(token)<20 or len(token)>8192:
        raise ValueError("Google OAuth response had no usable access token")
    return token


def _send(subscription,title,body):
    if not delivery_ready():
        raise ValueError("FCM delivery disabled")
    if subscription.device_session.channel!="customer":
        raise ValueError("Service sender is restricted to customer orders")
    decrypted=_cipher().decrypt(subscription.encrypted_token.encode()).decode("ascii")
    if not 20<=len(decrypted)<=512:
        raise ValueError("Invalid FCM registration token")
    if not 1<=len(title)<=90 or not 1<=len(body)<=200:
        raise ValueError("Invalid bounded notification content")
    bearer=_access_token()
    url=f"https://fcm.googleapis.com/v1/projects/{settings.KOFAD_FCM_PROJECT_ID}/messages:send"
    response=requests.post(url,json={
        "message":{
            "token":decrypted,
            "notification":{"title":title,"body":body},
            "android":{"priority":"NORMAL",
                       "notification":{"channel_id":"kofad_updates"}},
        },
    },headers={"Authorization":"Bearer "+bearer},timeout=(3,8))
    if response.status_code==200:
        value=response.json().get("name","")
        if isinstance(value,str) and value.startswith("projects/"):
            return "sent"
        return "uncertain"
    # Only Firebase's explicit token revocation means that a *device* is gone.
    # A misconfigured Firebase project must never erase legitimate subscribers.
    if response.status_code in (400,404):
        try:
            parts=response.json().get("error",{}).get("details",[])
            if any(isinstance(p,dict) and p.get("errorCode")=="UNREGISTERED" for p in parts):
                return "unregistered"
        except (ValueError,TypeError,AttributeError):
            pass
    return "uncertain" if response.status_code>=500 or response.status_code==429 else "failed"


def queue_order_push(order,event):
    """Never sends before order commit or exposes customer/amount on lock screen."""
    if not delivery_ready():
        return 0
    from .mobile_auth_models import MobileDeviceSession
    sessions=MobileDeviceSession.objects.filter(
        channel="customer",customer_id=order.customer_id,
        revoked_at__isnull=True,refresh_expires_at__gt=timezone.now(),
    )
    subs=MobilePushSubscription.objects.filter(
        device_session__in=sessions,service_opt_in=True,
    ).select_related("device_session","device_session__customer")
    queued=0
    for sub in subs[:10]:
        if not _valid_device(sub.device_session,"customer"):
            continue
        event_key=f"order:{order.pk}:{str(event)[:50]}:device:{sub.pk}"
        _,created=MobilePushOutbox.objects.get_or_create(
            event_key=event_key,defaults={
                "subscription":sub,"kind":"service",
                "title":"KOFAD Market order update",
                "body":"Your order has an update. Open KOFAD Market to see the latest status.",
            },
        )
        queued+=int(created)
    return queued


def process_due_push(limit=MAX_BATCH):
    if not delivery_ready():
        return 0
    finished=0
    for _ in range(max(0,min(int(limit),MAX_BATCH))):
        with transaction.atomic():
            row=MobilePushOutbox.objects.select_for_update(skip_locked=True).filter(
                status="queued",
            ).order_by("created_at","pk").first()
            if row is None:
                break
            row.status="sending"
            row.attempted_at=timezone.now()
            row.save(update_fields=["status","attempted_at"])
            item_id=row.pk
        # No row lock is held while performing any external network request.
        try:
            item=MobilePushOutbox.objects.select_related(
                "subscription","subscription__device_session",
                "subscription__device_session__customer",
            ).get(pk=item_id)
            sub=item.subscription
            opted=(sub.service_opt_in if item.kind=="service" else sub.marketing_opt_in)
            if (not opted or item.created_at<timezone.now()-MAX_AGE
                    or not _valid_device(sub.device_session,sub.device_session.channel)):
                result="failed"
            elif item.kind!="service" or sub.device_session.channel!="customer":
                # Promotions need a separate approved campaign budget/opt-in flow.
                result="failed"
            else:
                result=_send(sub,item.title,item.body)
            if result=="unregistered":
                sub.delete()  # Cascades the outbox; invalid token no longer receives sends.
                finished+=1
                continue
        except Exception:
            logger.warning("FCM attempt had an unknown outcome for push outbox id %s",item_id)
            result="uncertain"
        MobilePushOutbox.objects.filter(pk=item_id,status="sending").update(status=result)
        finished+=1
    return finished
