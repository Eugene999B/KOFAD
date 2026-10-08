import logging
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.db import OperationalError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from . import maintenance

logger = logging.getLogger(__name__)


def system_administrator(view):
    @login_required
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_superuser:
            raise PermissionDenied("System administrator access is required.")
        return view(request, *args, **kwargs)
    return inner


def _uploaded_backup(request):
    uploaded = request.FILES.get("backup_file")
    if not uploaded:
        raise maintenance.BackupError("Choose a KOFAD backup file.")
    if uploaded.size > maintenance.MAX_ENCRYPTED_BACKUP_BYTES:
        raise maintenance.BackupError("Backup file is larger than the supported limit.")
    return maintenance.parse_uploaded_backup(
        uploaded.read(), request.POST.get("backup_passphrase", "")
    )


def _password_ok(request):
    password = request.POST.get("password", "")
    return bool(password) and request.user.check_password(password)


@system_administrator
@sensitive_post_parameters("password", "backup_passphrase", "backup_passphrase_confirm")
def backup_restore(request):
    validation = None
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "validate":
                bundle = _uploaded_backup(request)
                validation = maintenance.backup_summary(bundle)
                messages.success(request, "Backup signature, checksum and schema are valid.")
            elif action == "restore":
                if not maintenance.recent_backup_downloaded(request.session):
                    raise maintenance.BackupError(
                        "Download a fresh backup of the current system before restoring. "
                        "The safety backup must be downloaded within the last 30 minutes."
                    )
                if not _password_ok(request):
                    raise maintenance.BackupError("Your administrator password is incorrect.")
                if request.POST.get("confirmation", "").strip() != maintenance.RESTORE_CONFIRMATION:
                    raise maintenance.BackupError(
                        f'Type exactly: {maintenance.RESTORE_CONFIRMATION}'
                    )
                if request.POST.get("understand") != "yes":
                    raise maintenance.BackupError("Confirm that restore replaces the current KOFAD data.")
                actor_username = request.user.username
                bundle = _uploaded_backup(request)
                maintenance.restore_backup(bundle, actor_username=actor_username)
                request.session.flush()
                return redirect("/login/?restored=1")
            elif action == "reset":
                if not maintenance.recent_backup_downloaded(request.session):
                    raise maintenance.BackupError(
                        "Download a fresh backup before resetting business data. "
                        "The safety backup must be downloaded within the last 30 minutes."
                    )
                if not _password_ok(request):
                    raise maintenance.BackupError("Your administrator password is incorrect.")
                if request.POST.get("confirmation", "").strip() != maintenance.RESET_CONFIRMATION:
                    raise maintenance.BackupError(
                        f'Type exactly: {maintenance.RESET_CONFIRMATION}'
                    )
                if request.POST.get("understand") != "yes":
                    raise maintenance.BackupError("Confirm that the business data will be permanently cleared.")
                maintenance.reset_business_data(request.user)
                request.session.flush()
                return redirect("/login/?fresh_start=1")
            else:
                raise maintenance.BackupError("Choose a valid maintenance action.")
        except maintenance.BackupError as exc:
            messages.error(request, str(exc))
        except Exception:
            logger.exception("KOFAD maintenance action failed and was rolled back: action=%s user=%s", action, request.user.username)
            messages.error(
                request,
                "The maintenance operation did not finish cleanly. Check the current system state and server logs "
                "before attempting another destructive action. Database restore/reset work is transaction-protected."
            )

    return render(request, "backup_restore.html", {
        "title": "Backup, restore & reset",
        "restore_phrase": maintenance.RESTORE_CONFIRMATION,
        "reset_phrase": maintenance.RESET_CONFIRMATION,
        "recent_backup": maintenance.recent_backup_downloaded(request.session),
        "recent_backup_at": request.session.get("kofad_recent_backup_at"),
        "validation": validation,
        "stats": maintenance.maintenance_stats(),
    })


@system_administrator
@require_POST
@sensitive_post_parameters("password", "backup_passphrase", "backup_passphrase_confirm")
def download_backup(request):
    if not _password_ok(request):
        messages.error(request, "Your administrator password is incorrect.")
        return redirect("backup_restore")
    passphrase = request.POST.get("backup_passphrase", "")
    if passphrase != request.POST.get("backup_passphrase_confirm", ""):
        messages.error(request, "The two backup passphrases do not match.")
        return redirect("backup_restore")
    try:
        raw = maintenance.encrypted_backup_bytes(request.user, passphrase)
    except maintenance.BackupError as exc:
        messages.error(request, str(exc))
        return redirect("backup_restore")
    except OperationalError:
        logger.exception("Could not obtain a consistent backup snapshot.")
        messages.error(request, "The database is busy. No partial backup was downloaded. Please try again shortly.")
        return redirect("backup_restore")
    maintenance.mark_backup_downloaded(request.session)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    response = HttpResponse(raw, content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="KOFAD-full-backup-{stamp}.kofad.enc"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-store"
    return response


@system_administrator
@require_GET
def backup_status(request):
    recent = maintenance.recent_backup_downloaded(request.session)
    raw = request.session.get("kofad_recent_backup_at")
    try:
        timestamp = float(raw)
    except (TypeError, ValueError):
        timestamp = None
    remaining = 0
    if recent and timestamp is not None:
        remaining = max(
            0,
            int(maintenance.RECENT_BACKUP_SECONDS - (timezone.now().timestamp() - timestamp))
        )
    response = JsonResponse({
        "recent": recent,
        "downloaded_at": timestamp,
        "expires_in_seconds": remaining,
    })
    response["Cache-Control"] = "no-store"
    return response
