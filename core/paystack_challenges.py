"""Provider challenge transport. Never persist codes or treat a charge as settlement."""
import re
import requests
from django.conf import settings
from django.core.exceptions import ValidationError


def charge_step(reference, *, otp=None):
    if not settings.PAYSTACK_SECRET_KEY or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(reference)):
        raise ValidationError("Invalid payment request.")
    headers = {"Authorization": "Bearer " + settings.PAYSTACK_SECRET_KEY, "Content-Type": "application/json"}
    try:
        if otp is None:
            response = requests.get("https://api.paystack.co/charge/" + reference,
                headers=headers, timeout=settings.PAYSTACK_TIMEOUT_SECONDS, allow_redirects=False)
        else:
            if not re.fullmatch(r"[0-9]{4,8}", str(otp)):
                raise ValidationError("Enter the one-time payment code sent to your phone. Never enter your MoMo PIN.")
            response = requests.post("https://api.paystack.co/charge/submit_otp",
                headers=headers, json={"reference": reference, "otp": otp},
                timeout=settings.PAYSTACK_TIMEOUT_SECONDS, allow_redirects=False)
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError("Payment verification is temporarily unavailable. Do not pay again.") from exc
    data = body.get("data") if isinstance(body, dict) else None
    if (not 200 <= response.status_code < 300 or not isinstance(body, dict) or body.get("status") is not True
            or not isinstance(data, dict) or data.get("reference") != reference):
        raise ValidationError("The payment code could not be verified. Check the code or contact KOFAD; do not pay again.")
    return data


def challenge_message(data):
    state = str(data.get("status", ""))
    if state == "send_otp":
        return "Enter the one-time payment code sent to the payer's phone. Never enter a Mobile Money PIN."
    if state.startswith("send_") or state == "open_url":
        return "This payment requires provider support. Do not pay again; contact KOFAD."
    return "Approve the request on your phone. KOFAD is checking payment automatically."
