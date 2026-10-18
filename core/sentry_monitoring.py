"""Conservative, opt-in Sentry error reporting for KOFAD's Django processes.

Export exception *types and code locations* only. Business payloads, account
identifiers, request details, log messages and exception values stay in KOFAD.
Performance traces, logging breadcrumbs and session replay remain disabled.
"""
import logging
import math
from urllib.parse import urlsplit

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

logger = logging.getLogger(__name__)
_FRAME_FIELDS = ("filename", "function", "lineno", "module", "in_app")
_EVENT_FIELDS = ("event_id", "timestamp", "platform", "level", "release", "environment")


def sanitized_error_event(event, hint):
    """Keep grouping-relevant error locations; discard all untrusted values.

    Returning a new allowlisted event (rather than deleting a few known keys)
    keeps new Sentry SDK fields from silently exporting sensitive application
    data after an SDK upgrade. Non-exception log/message events are dropped.
    """
    original_values = (event.get("exception") or {}).get("values") or []
    exceptions = []
    for entry in original_values:
        if not isinstance(entry, dict):
            continue
        original_frames = (entry.get("stacktrace") or {}).get("frames") or []
        frames = []
        for frame in original_frames:
            if isinstance(frame, dict):
                frames.append({key: frame[key] for key in _FRAME_FIELDS if key in frame})
        exceptions.append({
            "type": str(entry.get("type") or "Error")[:128],
            "value": "[redacted]",
            "stacktrace": {"frames": frames},
        })

    if not exceptions:
        return None

    cleaned = {key: event[key] for key in _EVENT_FIELDS if key in event}
    cleaned["exception"] = {"values": exceptions}
    return cleaned


def discard_breadcrumb(breadcrumb, hint):
    """Never retain request histories or log breadcrumbs."""
    return None


def safe_error_sample_rate(value):
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 0.25
    return rate if math.isfinite(rate) and 0.0 <= rate <= 1.0 else 0.25


def configure_sentry(dsn, *, environment="production", release=None, sample_rate=0.25):
    """Initialize for Django and error-level worker logs only with HTTPS DSNs."""
    try:
        parsed = urlsplit(dsn)
        valid = parsed.scheme == "https" and parsed.hostname and parsed.username and parsed.path.strip("/")
    except (TypeError, ValueError):
        valid = False
    if not valid:
        # Never log a DSN: it includes a project key and ingestion endpoint.
        logger.warning("Sentry configuration invalid; event reporting disabled.")
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        sample_rate=safe_error_sample_rate(sample_rate),
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        attach_stacktrace=False,
        max_breadcrumbs=0,
        before_send=sanitized_error_event,
        before_breadcrumb=discard_breadcrumb,
        before_send_transaction=lambda event, hint: None,
        traces_sample_rate=0.0,
        profiles_sample_rate=0.0,
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[
            DjangoIntegration(),
            LoggingIntegration(level=None, event_level=logging.ERROR),
        ],
    )
    return True
