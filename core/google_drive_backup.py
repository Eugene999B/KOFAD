"""Export-only encrypted backup endpoint for a Google Apps Script weekly trigger.

This is deliberately NOT a scheduled task on Railway. Google invokes one signed
HTTPS POST, and the existing full-system backup code generates the encrypted file.
Manual administration, download, validation, restore and reset stay unchanged.
"""
import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.db import OperationalError, connection, transaction
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import maintenance

logger = logging.getLogger(__name__)
AUTH_WINDOW_SECONDS = 300
# Google Apps Script UrlFetchApp cannot reliably transfer a 100 MB response.
# Stop well below its 50 MB response-size limit instead of silently truncating.
GOOGLE_TRANSFER_LIMIT = 40 * 1024 * 1024
BACKUP_PATH = "/settings/backup/google/export/"


def _error(status):
    response = HttpResponse(status=status)
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _configured():
    return (
        getattr(settings, "KOFAD_GDRIVE_BACKUP_ENABLED", False)
        and len(getattr(settings, "KOFAD_GDRIVE_BACKUP_HMAC_KEY", "")) >= 32
        and len(getattr(settings, "KOFAD_GDRIVE_BACKUP_PASSPHRASE", "")) >= 24
    )


def _authenticated(request):
    # The server-to-server authorization is separate from user credentials.
    stamp = request.headers.get("X-Kofad-Backup-Timestamp", "")
    signature = request.headers.get("X-Kofad-Backup-Signature", "")
    if not (stamp.isascii() and stamp.isdecimal() and 9 <= len(stamp) <= 12):
        return False
    if len(signature) != 64 or any(c not in "0123456789abcdef" for c in signature):
        return False
    if abs(int(time.time()) - int(stamp)) > AUTH_WINDOW_SECONDS:
        return False
    message = f"v1\nPOST\n{BACKUP_PATH}\n{stamp}".encode("ascii")
    expected = hmac.new(
        settings.KOFAD_GDRIVE_BACKUP_HMAC_KEY.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


@csrf_exempt
@require_POST
def export_google_weekly_backup(request):
    """A signed one-way data export; never accepts a restore/reset command."""
    if not _configured():
        return _error(404)

    # Only canonical staff host can serve this endpoint in production. Avoid
    # leaking files through the root, market, or generated Railway origins.
    if request.get_host().split(":")[0].lower() != "staff.kofadimpex.com":
        return _error(404)
    if request.path != BACKUP_PATH or request.body:
        return _error(400)
    if not _authenticated(request):
        return _error(403)

    try:
        # A transaction-scoped lock prevents duplicate concurrent exports.
        # No periodic polling, new table, worker or scheduler is created.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
                    ["kofad_google_weekly_backup_v1"],
                )
                acquired = cursor.fetchone()[0]
            if not acquired:
                return _error(409)
            raw = maintenance.encrypted_backup_bytes(
                actor=None,
                passphrase=settings.KOFAD_GDRIVE_BACKUP_PASSPHRASE,
            )
    except maintenance.BackupError:
        logger.warning("Google weekly backup was not exported: backup size or validation limit.")
        return _error(413)
    except OperationalError:
        logger.warning("Google weekly backup could not acquire a consistent database snapshot.")
        return _error(503)
    except Exception:
        logger.exception("Google weekly backup generation failed.")
        return _error(503)

    if len(raw) > GOOGLE_TRANSFER_LIMIT:
        logger.warning("Google weekly backup exceeds the supported script transfer limit.")
        return _error(413)

    local = timezone.localtime()
    stamp = local.strftime("%Y-%m-%d-%H%M")
    response = HttpResponse(raw, content_type="application/octet-stream")
    response["Content-Disposition"] = (
        f'attachment; filename="KOFAD-FULL-BACKUP-{stamp}.kofad.enc"'
    )
    response["X-Kofad-Backup-SHA256"] = hashlib.sha256(raw).hexdigest()
    response["X-Kofad-Backup-Created"] = local.isoformat()
    response["Content-Length"] = str(len(raw))
    response["Cache-Control"] = "no-store, private, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response
