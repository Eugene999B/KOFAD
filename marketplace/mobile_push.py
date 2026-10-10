"""Authenticated KOFAD mobile push subscription contract (delivery separately gated).

This intentionally does NOT call FCM or deliver any notification. Firebase
must be configured privately and the Android client must request OS consent.
No device tokens are returned to callers, including the device that registered.
"""
import base64
import hashlib
import hmac
import json
import re

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .mobile_identity import _bearer_session, _preflight, _public_response, _request_allowed
from .mobile_push_models import MobilePushSubscription

FCM_TOKEN = re.compile(r"^[A-Za-z0-9_:.-]{20,512}$")
MAX_BODY = 1500

def _cipher():
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf8"), b"kofad:native-push-token-fernet:v1", hashlib.sha256,
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))

def _message(request, value, code=200):
    response = _public_response(request, value, status=code)
    response["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response

@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE", "OPTIONS"])
def devices(request, channel):
    if channel not in ("staff", "customer"):
        return HttpResponse(status=404)
    if not (getattr(settings, "KOFAD_NATIVE_AUTH_ENABLED", False)
            and getattr(settings, "KOFAD_NATIVE_PUSH_ENABLED", False)):
        return HttpResponse(status=404)
    if request.method == "OPTIONS":
        response = _preflight(request)
        response["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        return response
    if not _request_allowed(request):
        return _message(request, {"error":"origin_not_allowed"},403)
    session = _bearer_session(request, channel)
    if session is None:
        return _message(request, {"error":"authentication_required"},401)

    if request.method == "GET":
        record = MobilePushSubscription.objects.filter(device_session=session).first()
        return _message(request, {
            "version":1, "channel":channel,
            "registered":bool(record),
            "service_opt_in":bool(record and record.service_opt_in),
            "marketing_opt_in":bool(record and record.marketing_opt_in) if channel=="customer" else False,
            "background_delivery_active":False,
        })
    if request.method == "DELETE":
        MobilePushSubscription.objects.filter(device_session=session).delete()
        return _message(request,{"revoked":True})

    if not request.content_type.startswith("application/json") or len(request.body)>MAX_BODY:
        return _message(request,{"error":"invalid_request"},400)
    try:
        data=json.loads(request.body)
    except (UnicodeDecodeError,ValueError):
        return _message(request,{"error":"invalid_request"},400)
    if not isinstance(data,dict) or set(data)!={"token","service_opt_in","marketing_opt_in"}:
        return _message(request,{"error":"invalid_request"},400)
    token=data["token"]
    service=data["service_opt_in"]
    marketing=data["marketing_opt_in"]
    if not isinstance(token,str) or not FCM_TOKEN.fullmatch(token):
        return _message(request,{"error":"invalid_token"},400)
    if type(service) is not bool or type(marketing) is not bool or (channel=="staff" and marketing):
        return _message(request,{"error":"invalid_consent"},400)
    if not service and not marketing:
        MobilePushSubscription.objects.filter(device_session=session).delete()
        return _message(request,{"revoked":True})

    digest=hashlib.sha256(token.encode("utf8")).hexdigest()
    # Transfer reused Firebase tokens away from previous owners/sessions, never
    # leave a token bound to two KOFAD accounts.
    with transaction.atomic():
        MobilePushSubscription.objects.filter(token_digest=digest).exclude(
            device_session=session,
        ).delete()
        record=MobilePushSubscription.objects.select_for_update().filter(
            device_session=session,
        ).first()
        if record is None:
            record=MobilePushSubscription(device_session=session)
        if (record.pk and record.token_digest==digest
                and record.service_opt_in==service
                and record.marketing_opt_in==marketing):
            return _message(request,{"registered":True,"background_delivery_active":False})
        record.token_digest=digest
        record.encrypted_token=_cipher().encrypt(token.encode("ascii")).decode("ascii")
        record.service_opt_in=service
        record.marketing_opt_in=marketing
        record.save()
    return _message(request,{"registered":True,"background_delivery_active":False})
