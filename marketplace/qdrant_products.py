"""Opt-in Qdrant Cloud search over KOFAD's PUBLISHED product descriptions.

PostgreSQL remains authoritative for price, inventory, permissions and listing
visibility. Only explicit enhanced product searches may go to Qdrant. Cluster
inference is bounded, and neither private customer nor finance data is indexed.
"""
import logging
import re
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger(__name__)
COLLECTION = "kofad_public_products_v1"
MODEL = "sentence-transformers/all-minilm-l6-v2"
DAILY_SEARCH_CAP = 80
MAX_SYNC_PRODUCTS = 250


def _cluster_url():
    url = getattr(settings, "KOFAD_QDRANT_URL", "").strip().rstrip("/")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return ""
    if (parsed.scheme != "https" or not parsed.hostname
            or not parsed.hostname.endswith(".cloud.qdrant.io")
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or port not in (None, 443, 6333)):
        return ""
    return url


def configured():
    return bool(
        getattr(settings, "KOFAD_QDRANT_ENABLED", False)
        and _cluster_url()
        and len(getattr(settings, "KOFAD_QDRANT_API_KEY", "")) >= 16
    )


def _headers():
    return {"api-key": settings.KOFAD_QDRANT_API_KEY, "Content-Type": "application/json"}


def _daily_slot():
    key = "kofad:qdrant:enhanced-queries:" + timezone.now().strftime("%Y%m%d")
    try:
        if cache.add(key, 1, timeout=86400):
            return True
        return cache.incr(key) <= DAILY_SEARCH_CAP
    except (ConnectionError, TypeError, ValueError):
        return False


def _public_search_term(text):
    if not isinstance(text, str):
        return ""
    text = " ".join(text.strip().split())
    if not 3 <= len(text) <= 100:
        return ""
    # Never send any identifiable contact, card or credential-like query.
    if re.search(r"(?i)@|\b(?:password|otp|pin|card|account|phone|email|address|order)\b|\d{7,}", text):
        return ""
    if not re.fullmatch(r"[\w\s,.'/&()+-]+", text, re.UNICODE):
        return ""
    return text


def semantic_product_ids(text):
    """Read-only enhanced search; fail closed to KOFAD's normal SQL search."""
    question = _public_search_term(text)
    if not configured() or not question or not _daily_slot():
        return []
    try:
        response = requests.post(
            _cluster_url() + f"/collections/{COLLECTION}/points/query",
            headers=_headers(),
            json={
                "query": {"text": question, "model": MODEL},
                "limit": 10, "with_payload": True,
            },
            timeout=(2, 5),
        )
        if response.status_code != 200:
            log.warning("Optional enhanced product search unavailable: HTTP %s", response.status_code)
            return []
        result = response.json()
        result_rows = result.get("result") if isinstance(result, dict) else None
        rows = result_rows.get("points", []) if isinstance(result_rows, dict) else []
        ids = []
        for point in rows[:10] if isinstance(rows, list) else []:
            identifier = point.get("payload", {}).get("listing_id") if isinstance(point, dict) else None
            if isinstance(identifier, int) and not isinstance(identifier, bool) and 0 < identifier < 2**63:
                if identifier not in ids:
                    ids.append(identifier)
        return ids
    except (requests.RequestException, ValueError, TypeError, KeyError):
        log.warning("Optional enhanced product search failed; SQL fallback retained.")
        return []


def sync_public_catalog():
    """Explicit management-only upsert; never run from a visitor request."""
    if not configured():
        raise ValueError("Qdrant is not configured or its flag is disabled.")
    from .models import MarketListing
    listings = list(
        MarketListing.objects.filter(enabled=True, product__active=True)
        .select_related("product").order_by("pk")[:MAX_SYNC_PRODUCTS]
    )
    base = _cluster_url() + f"/collections/{COLLECTION}"
    try:
        check = requests.get(base, headers=_headers(), timeout=(2, 5))
        if check.status_code == 404:
            create = requests.put(
                base, headers=_headers(),
                json={"vectors": {"size": 384, "distance": "Cosine"}},
                timeout=(2, 10),
            )
            create.raise_for_status()
        else:
            check.raise_for_status()
        for start in range(0, len(listings), 25):
            batch = listings[start:start + 25]
            points = []
            for row in batch:
                public_text = " ".join(filter(None, (
                    row.display_name[:100], row.product.category[:80],
                    row.tags[:200], row.description[:400],
                )))[:750]
                points.append({
                    "id": row.pk,
                    "vector": {"text": public_text, "model": MODEL},
                    "payload": {"listing_id": row.pk},
                })
            response = requests.put(
                base + "/points?wait=true", headers=_headers(),
                json={"points": points}, timeout=(3, 25),
            )
            response.raise_for_status()
        return len(listings)
    except (requests.RequestException, ValueError, TypeError):
        log.warning("Qdrant public catalogue sync unavailable; live PostgreSQL remains authoritative.")
        raise ValueError("Qdrant public catalogue sync failed.") from None
