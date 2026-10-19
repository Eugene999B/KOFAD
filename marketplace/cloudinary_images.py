"""Optional signed Cloudinary mirror for newly uploaded PUBLIC product photos.

KOFAD keeps the compressed database copy as recovery evidence. Never transfer
customer attachments, identity documents or support messages.
"""
import hashlib
import logging
import re
import secrets
import time
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger(__name__)
DAILY_CAP = 40
MAX_IMAGE_BYTES = 3 * 1024 * 1024


def configured():
    cloud = getattr(settings, "CLOUDINARY_CLOUD_NAME", "")
    return bool(
        getattr(settings, "KOFAD_CLOUDINARY_ENABLED", False)
        and re.fullmatch(r"[A-Za-z0-9_-]{3,64}", cloud)
        and getattr(settings, "CLOUDINARY_API_KEY", "").strip()
        and getattr(settings, "CLOUDINARY_API_SECRET", "").strip()
    )


def _reserve_upload():
    day = timezone.now().strftime("%Y%m%d")
    key = "kofad:cloudinary:public-uploads:" + day
    try:
        if cache.add(key, 1, timeout=86400):
            return True
        return cache.incr(key) <= DAILY_CAP
    except (ConnectionError, TypeError, ValueError):
        return False


def mirror_public_image(compressed_webp):
    """Return a validated secure CDN URL or an empty string; never raise."""
    if (not configured() or not isinstance(compressed_webp, bytes)
            or not (0 < len(compressed_webp) <= MAX_IMAGE_BYTES)
            or not _reserve_upload()):
        return ""
    cloud = settings.CLOUDINARY_CLOUD_NAME
    timestamp = str(int(time.time()))
    public_id = "kofad-products/" + secrets.token_hex(16)
    fields = {
        "overwrite": "false", "public_id": public_id, "timestamp": timestamp,
    }
    signature_text = "&".join(f"{key}={fields[key]}" for key in sorted(fields))
    signature = hashlib.sha1(
        (signature_text + settings.CLOUDINARY_API_SECRET).encode("utf-8")
    ).hexdigest()
    try:
        response = requests.post(
            f"https://api.cloudinary.com/v1_1/{cloud}/image/upload",
            data={**fields, "api_key": settings.CLOUDINARY_API_KEY, "signature": signature},
            files={"file": ("kofad-product.webp", compressed_webp, "image/webp")},
            timeout=(3, 12),
        )
        if response.status_code != 200:
            log.warning("Optional public image mirror unavailable: HTTP %s", response.status_code)
            return ""
        body = response.json()
        url = body.get("secure_url") if isinstance(body, dict) else None
        if not isinstance(url, str) or len(url) > 1024:
            return ""
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "res.cloudinary.com"
                or not parsed.path.startswith(f"/{cloud}/image/upload/")
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            return ""
        return url
    except (requests.RequestException, ValueError, TypeError):
        log.warning("Optional public image mirror failed; database image retained.")
        return ""
