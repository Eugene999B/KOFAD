"""Strict address parsing for explicit business email CC/BCC.

Only plain email addresses are accepted, not display-name or header syntax.
Keep hidden BCC values out of customer-visible subjects and CSV exports.
"""
import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


def parse_copies(value, *, max_count=5):
    if isinstance(value, (tuple, list)):
        raw = ",".join(str(part) for part in value)
    elif isinstance(value, str):
        raw = value
    else:
        raise ValidationError("Enter valid email addresses separated by commas.")
    if len(raw) > 1600 or any(symbol in raw for symbol in ("\r", "\n", "\x00")):
        raise ValidationError("Email recipient list is invalid.")
    if not raw.strip():
        return []
    parts = [entry.strip().lower() for entry in re.split(r"[,;]", raw)]
    if not 1 <= len(parts) <= max_count or any(not part for part in parts):
        raise ValidationError("Use up to five email addresses in each CC or BCC field.")
    for addr in parts:
        if len(addr) > 254 or any(c in addr for c in '<>"\\'):
            raise ValidationError("Use plain email addresses only, without names or headers.")
        validate_email(addr)
    if len(set(parts)) != len(parts):
        raise ValidationError("Repeated copied recipients are not allowed.")
    return parts


def validate_copies(primary, cc="", bcc=""):
    copied = parse_copies(cc)
    hidden = parse_copies(bcc)
    main = primary.strip().lower()
    combined = [main, *copied, *hidden]
    if len(combined) > 11 or len(set(combined)) != len(combined):
        raise ValidationError("Every recipient must appear only once.")
    return ",".join(copied), ",".join(hidden)
