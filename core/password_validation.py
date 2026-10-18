"""Privacy-preserving password exposure screening for customer and staff accounts.

Only the first five characters of the password's SHA-1 digest are transmitted
to Have I Been Pwned. The full digest comparison happens on KOFAD's server.
This is a breach-corpus lookup; Django still stores passwords with its normal
password hasher, not SHA-1.
"""
import hashlib
import logging

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)
_RANGE_URL = "https://api.pwnedpasswords.com/range/"


class PwnedPasswordValidator:
    """Reject known-exposed passwords when explicitly enabled for this service."""

    def validate(self, password, user=None):
        if not password or not getattr(settings, "KOFAD_PWNED_PASSWORDS_ENABLED", False):
            return

        digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        # Never send the raw password, full hash, hash suffix, user data, or
        # password on each keystroke. Check the completed password server-side.
        try:
            response = requests.get(
                _RANGE_URL + digest[:5],
                headers={"User-Agent": "KOFAD-Password-Security/1.0", "Add-Padding": "true"},
                timeout=(1.0, 2.0),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            # Availability trade-off: keep the existing length/common-password
            # validators working if the external API is temporarily unavailable.
            # Never log the hash prefix, URL, request payload, or password.
            logger.warning(
                "Pwned Passwords check unavailable (%s); local validators remain active",
                type(exc).__name__,
            )
            return

        target_suffix = digest[5:]
        for line in response.text.splitlines():
            suffix, separator, count = line.partition(":")
            if (
                separator
                and suffix.upper() == target_suffix
                and count.strip().isdigit()
                and int(count) > 0  # Ignore HIBP padding entries with zero count.
            ):
                raise ValidationError(
                    _("This password has appeared in a known data breach. Choose a different password."),
                    code="password_compromised",
                )

    def get_help_text(self):
        return _("Your password must not appear in known data breaches.")
