"""SMS provider adapters with direct submission and delivery-report lookup."""
import json
import re
import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.module_loading import import_string


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Never forward provider credentials to a redirected host.
urlopen = build_opener(NoRedirect).open


@dataclass(frozen=True)
class Submission:
    status: str
    provider_id: str = ""
    http_status: int | None = None
    error_code: str = ""
    error_detail: str = ""
    recipient: str = ""


def _clean_detail(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:220]


def _read_json_bytes(raw):
    if not raw or len(raw) > 65536:
        return None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        return None


def _provider_error(data, fallback="Provider rejected the SMS request."):
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "description"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return _clean_detail(value)
        nested = data.get("data")
        if isinstance(nested, dict):
            return _provider_error(nested, fallback)
    return fallback


def _response_entries(data):
    if not isinstance(data, dict):
        return []
    payload = data.get("data")
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        # Arkesel has documented both one-object and list-shaped success payloads.
        if any(key in payload for key in ("id", "sms_id", "message_id", "recipient")):
            return [payload]
        return [item for item in payload.values() if isinstance(item, dict)]
    return []


def _entry_id(entry):
    for key in ("id", "sms_id", "message_id"):
        value = entry.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()[:180]
    return ""


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


class Arkesel:
    name = "arkesel"
    endpoint = "https://sms.arkesel.com/api/v2/sms/send"
    reports_endpoint = "https://sms.arkesel.com/api/v2/sms/message-reports"

    def validate(self):
        if not settings.ARKESEL_API_KEY:
            raise ValidationError("Arkesel API key is not configured.")
        if not re.fullmatch(r"[A-Za-z0-9 ]{1,11}", settings.SMS_SENDER_ID):
            raise ValidationError("Configure an approved sender ID of 1–11 letters, digits or spaces.")

    def submit_many(self, recipients, body, sender, callback_url, sandbox):
        self.validate()
        recipients = list(dict.fromkeys(str(value).strip() for value in recipients if str(value).strip()))
        if not recipients:
            raise ValidationError("Choose at least one SMS recipient.")
        payload = {
            "sender": sender,
            "message": body,
            "recipients": [value.lstrip("+") for value in recipients],
            "sandbox": sandbox,
        }
        if callback_url:
            payload["callback_url"] = callback_url
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "api-key": settings.ARKESEL_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=settings.SMS_TIMEOUT_SECONDS) as response:
                http_status = response.status
                raw = response.read(65537)
        except HTTPError as exc:
            raw = exc.read(65537)
            data = _read_json_bytes(raw)
            detail = _provider_error(data, f"Arkesel rejected the request (HTTP {exc.code}).")
            code = "rate_limited" if exc.code == 429 else (
                "provider_rejected" if exc.code in (400, 401, 403, 404, 405, 413, 415, 422)
                else "uncertain_http_response"
            )
            status = "failed" if code == "provider_rejected" else ("retry_wait" if code == "rate_limited" else "unknown")
            return [
                Submission(status, http_status=exc.code, error_code=code, error_detail=detail, recipient=value)
                for value in recipients
            ]
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            detail = _clean_detail(f"Arkesel network result is unknown: {exc}")
            return [
                Submission("unknown", error_code="uncertain_network_result", error_detail=detail, recipient=value)
                for value in recipients
            ]

        data = _read_json_bytes(raw)
        if not isinstance(data, dict):
            return [
                Submission("unknown", http_status=http_status, error_code="unrecognized_response",
                           error_detail="Arkesel returned an unreadable response.", recipient=value)
                for value in recipients
            ]

        outcome = str(data.get("status", "")).lower()
        if outcome in ("error", "failed", "failure") or data.get("success") is False:
            detail = _provider_error(data)
            return [
                Submission("failed", http_status=http_status, error_code="provider_rejected",
                           error_detail=detail, recipient=value)
                for value in recipients
            ]

        entries = _response_entries(data)
        by_digits = {}
        anonymous = []
        for entry in entries:
            identifier = _entry_id(entry)
            recipient_digits = _digits(entry.get("recipient") or entry.get("phone") or entry.get("number"))
            if recipient_digits:
                by_digits.setdefault(recipient_digits, []).append((identifier, entry))
            else:
                anonymous.append((identifier, entry))

        results = []
        for index, recipient in enumerate(recipients):
            match = None
            digits = _digits(recipient)
            candidates = by_digits.get(digits) or by_digits.get(digits.lstrip("0"))
            if candidates:
                match = candidates.pop(0)
            elif len(recipients) == 1 and entries:
                match = (_entry_id(entries[0]), entries[0])
            elif index < len(anonymous):
                match = anonymous[index]

            identifier, entry = match if match else ("", {})
            if identifier:
                results.append(Submission(
                    "simulated" if sandbox else "accepted",
                    provider_id=identifier,
                    http_status=http_status,
                    recipient=recipient,
                ))
            else:
                detail = _provider_error(entry, "Arkesel did not accept this recipient.")
                results.append(Submission(
                    "failed",
                    http_status=http_status,
                    error_code="recipient_rejected",
                    error_detail=detail,
                    recipient=recipient,
                ))
        return results

    def submit(self, recipient, body, sender, callback_url, sandbox):
        return self.submit_many([recipient], body, sender, callback_url, sandbox)[0]

    def reports(self, provider_ids):
        self.validate()
        ids = list(dict.fromkeys(str(value).strip() for value in provider_ids if str(value).strip()))
        if not ids:
            return {}
        request = Request(
            self.reports_endpoint,
            data=json.dumps({"msg_ids": ids}).encode(),
            method="POST",
            headers={
                "api-key": settings.ARKESEL_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=settings.SMS_TIMEOUT_SECONDS) as response:
                raw = response.read(65537)
                http_status = response.status
        except HTTPError as exc:
            detail = _provider_error(_read_json_bytes(exc.read(65537)), f"Delivery report request failed (HTTP {exc.code}).")
            raise ValidationError(detail)
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise ValidationError(_clean_detail(f"Delivery report network error: {exc}"))
        data = _read_json_bytes(raw)
        if http_status < 200 or http_status >= 300 or not isinstance(data, dict):
            raise ValidationError("Arkesel returned an invalid delivery-report response.")
        if str(data.get("status", "")).lower() not in ("success", "successful"):
            raise ValidationError(_provider_error(data, "Arkesel delivery-report lookup failed."))

        payload = data.get("data")
        if isinstance(payload, dict):
            return {str(key): value for key, value in payload.items()}
        if isinstance(payload, list):
            result = {}
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                identifier = _entry_id(entry)
                if identifier:
                    result[identifier] = entry
            return result
        return {}


def get_provider(name):
    adapter_path = settings.SMS_ADAPTERS.get(name)
    if not adapter_path:
        raise ValidationError("The selected SMS provider has no installed adapter.")
    return import_string(adapter_path)()
