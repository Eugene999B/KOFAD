"""Provider adapters. Unknown send outcomes never trigger automatic failover."""
import json
import re
import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None


# Do not forward API credentials to a redirected host.
urlopen = build_opener(NoRedirect).open

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class Submission:
    status: str
    provider_id: str = ""
    http_status: int | None = None
    error_code: str = ""


def provider_id(data):
    if isinstance(data,dict):
        for key in ("sms_id","message_id","id"):
            value = data.get(key)
            if isinstance(value,(str,int)) and str(value):
                return str(value)[:180]
        return provider_id(data.get("data"))
    if isinstance(data,list) and len(data) == 1:
        return provider_id(data[0])
    return ""


class Arkesel:
    name = "arkesel"
    endpoint = "https://sms.arkesel.com/api/v2/sms/send"

    def validate(self):
        if not settings.ARKESEL_API_KEY:
            raise ValidationError("Arkesel API key is not configured.")
        if not re.fullmatch(r"[A-Za-z0-9 ]{1,11}",settings.SMS_SENDER_ID):
            raise ValidationError("Configure an approved sender ID of 1–11 letters, digits or spaces.")

    def submit(self, recipient, body, sender, callback_url, sandbox):
        self.validate()
        payload = {"sender":sender,"message":body,"recipients":[recipient.lstrip("+")],
                   "callback_url":callback_url,"sandbox":sandbox}
        request = Request(self.endpoint,data=json.dumps(payload).encode(),method="POST",
            headers={"api-key":settings.ARKESEL_API_KEY,"Content-Type":"application/json","Accept":"application/json"})
        try:
            with urlopen(request,timeout=settings.SMS_TIMEOUT_SECONDS) as response:
                status = response.status
                raw = response.read(65537)
        except HTTPError as exc:
            if exc.code == 429:
                return Submission("retry_wait",http_status=429,error_code="rate_limited")
            if exc.code in (400,401,403,404,405,413,415,422):
                return Submission("failed",http_status=exc.code,error_code="provider_rejected")
            return Submission("unknown",http_status=exc.code,error_code="uncertain_http_response")
        except (URLError,TimeoutError,socket.timeout,OSError):
            return Submission("unknown",error_code="uncertain_network_result")
        if len(raw) > 65536:
            return Submission("unknown",http_status=status,error_code="oversized_response")
        try:
            data = json.loads(raw)
        except (ValueError,UnicodeError):
            return Submission("unknown",http_status=status,error_code="unrecognized_response")
        if not isinstance(data,dict):
            return Submission("unknown",http_status=status,error_code="unrecognized_response")
        outcome = str(data.get("status","")).lower()
        if outcome in ("error","failed","failure") or data.get("success") is False:
            return Submission("failed",http_status=status,error_code="provider_rejected")
        reference = provider_id(data)
        if outcome not in ("success","successful","accepted","queued","submitted") or not reference:
            return Submission("unknown",http_status=status,error_code="acceptance_not_proven")
        # Send acceptance is not handset delivery; sandbox is never labelled delivered.
        return Submission("simulated" if sandbox else "accepted",reference,status)


def get_provider(name):
    adapter_path = settings.SMS_ADAPTERS.get(name)
    if not adapter_path:
        raise ValidationError("The selected SMS provider has no installed adapter.")
    return import_string(adapter_path)()
