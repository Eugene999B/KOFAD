"""Signed full-system backups, exact-schema restore, and guarded business-data reset."""
import hashlib
import hmac
import json
import logging
from collections import Counter
from datetime import timezone as dt_timezone
from itertools import chain

from django.apps import apps
from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core import serializers
from django.core.management.color import no_style
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from .models import Access, Audit, Branch, Company


BACKUP_FORMAT = "kofad-full-system-backup"
BACKUP_VERSION = 2
MAX_BACKUP_BYTES = 100 * 1024 * 1024
RECENT_BACKUP_SECONDS = 30 * 60
RESTORE_CONFIRMATION = "RESTORE KOFAD FULL BACKUP"
RESET_CONFIRMATION = "RESET KOFAD BUSINESS DATA"

EXCLUDED_BACKUP_MODELS = {
    # Ephemeral security material is deliberately not resurrected by restore.
    "core.loginattempt",
    "core.passwordrecovery",
    "marketplace.otpthrottle",
}

BACKUP_APP_LABELS = ("core", "marketplace")

FRESH_START_ROLES = {
    "Owner": [
        "operate_sales", "operate_inventory", "operate_finance", "approve_operations",
        "view_reports", "manage_company", "send_messages",
        "add_product", "change_product", "add_party", "change_party",
    ],
    "Manager": [
        "send_messages", "operate_sales", "operate_inventory", "operate_finance",
        "approve_operations", "view_reports",
        "add_product", "change_product", "add_party", "change_party",
    ],
    "Cashier": ["operate_sales", "add_party"],
    "Storekeeper": ["operate_inventory"],
    "Accountant": ["operate_finance", "view_reports", "change_party", "add_party"],
    "Auditor": ["view_reports"],
}

logger = logging.getLogger(__name__)


class BackupError(ValueError):
    pass


def _managed(model):
    return model._meta.managed and not model._meta.proxy and not model._meta.auto_created


def backup_models():
    ordered = [ContentType, Permission, Group, User, LogEntry]
    for app_label in BACKUP_APP_LABELS:
        ordered.extend(
            model for model in apps.get_app_config(app_label).get_models()
            if _managed(model) and model._meta.label_lower not in EXCLUDED_BACKUP_MODELS
        )
    seen = set()
    result = []
    for model in ordered:
        if model._meta.label_lower not in seen:
            result.append(model)
            seen.add(model._meta.label_lower)
    return result


def backup_model_labels():
    return {model._meta.label_lower for model in backup_models()}


def current_migrations():
    return [
        [app, name]
        for app, name in MigrationRecorder.Migration.objects.order_by("app", "name").values_list("app", "name")
        if app in {"contenttypes", "auth", "admin", "core", "marketplace"}
    ]


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _signature_payload(bundle):
    return {key: value for key, value in bundle.items() if key != "signature"}


def _sign(bundle):
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        _canonical(_signature_payload(bundle)),
        hashlib.sha256,
    ).hexdigest()


def _serialize_fixture():
    querysets = [model._default_manager.all().order_by(model._meta.pk.name) for model in backup_models()]
    return serializers.serialize(
        "json",
        chain.from_iterable(querysets),
        use_natural_foreign_keys=False,
        use_natural_primary_keys=False,
    )


def create_backup(actor=None):
    fixture = _serialize_fixture()
    records = json.loads(fixture)
    counts = Counter(row["model"] for row in records)
    bundle = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": timezone.now().astimezone(dt_timezone.utc).isoformat(),
        "created_by": getattr(actor, "username", "") if actor else "",
        "database_engine": connection.vendor,
        "migrations": current_migrations(),
        "record_count": len(records),
        "model_counts": dict(sorted(counts.items())),
        "fixture_sha256": hashlib.sha256(fixture.encode("utf-8")).hexdigest(),
        "fixture": fixture,
    }
    bundle["signature"] = _sign(bundle)
    return bundle


def backup_bytes(actor=None):
    bundle = create_backup(actor)
    return json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def parse_backup(raw):
    if not isinstance(raw, (bytes, bytearray)):
        raise BackupError("Backup content is missing.")
    if len(raw) > MAX_BACKUP_BYTES:
        raise BackupError("Backup file is larger than the supported 50 MB limit.")
    try:
        bundle = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("This is not a valid KOFAD backup file.") from exc
    return validate_backup(bundle)


def validate_backup(bundle):
    if not isinstance(bundle, dict):
        raise BackupError("Backup root must be an object.")
    if bundle.get("format") != BACKUP_FORMAT or bundle.get("version") != BACKUP_VERSION:
        raise BackupError("Unsupported backup format or version.")
    supplied = str(bundle.get("signature", ""))
    expected = _sign(bundle)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise BackupError("Backup signature is invalid. The file may be altered or from another KOFAD installation.")

    fixture = bundle.get("fixture")
    if not isinstance(fixture, str):
        raise BackupError("Backup fixture is missing.")
    if hashlib.sha256(fixture.encode("utf-8")).hexdigest() != bundle.get("fixture_sha256"):
        raise BackupError("Backup checksum does not match its data.")
    if bundle.get("migrations") != current_migrations():
        raise BackupError("Backup schema does not exactly match the currently deployed KOFAD schema.")

    try:
        records = json.loads(fixture)
    except json.JSONDecodeError as exc:
        raise BackupError("Backup fixture is corrupt.") from exc
    if not isinstance(records, list):
        raise BackupError("Backup fixture must contain a record list.")
    allowed = backup_model_labels()
    unexpected = sorted({str(row.get("model", "")) for row in records if row.get("model") not in allowed})
    if unexpected:
        raise BackupError("Backup contains unsupported model data: " + ", ".join(unexpected[:5]))
    if len(records) != int(bundle.get("record_count", -1)):
        raise BackupError("Backup record count does not match its manifest.")

    actual_counts = Counter(row["model"] for row in records)
    expected_counts = {str(k): int(v) for k, v in (bundle.get("model_counts") or {}).items()}
    if dict(actual_counts) != expected_counts:
        raise BackupError("Backup model counts do not match its manifest.")

    if not actual_counts.get("auth.user"):
        raise BackupError("Backup contains no user accounts.")
    return bundle


def backup_summary(bundle):
    return {
        "created_at": bundle.get("created_at"),
        "created_by": bundle.get("created_by") or "Unknown",
        "record_count": bundle.get("record_count", 0),
        "model_count": len(bundle.get("model_counts") or {}),
        "checksum": bundle.get("fixture_sha256", ""),
    }


def _table_names_for_restore():
    existing = set(connection.introspection.table_names())
    names = {
        name for name in existing
        if (
            name in {"django_content_type", "django_admin_log", "django_session"}
            or name.startswith("auth_")
            or name.startswith("core_")
            or name.startswith("marketplace_")
        )
    }
    return sorted(names)


def _truncate_tables(table_names):
    if not table_names:
        return
    quoted = ", ".join(connection.ops.quote_name(name) for name in table_names)
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")


def _reset_sequences(models):
    sql = connection.ops.sequence_reset_sql(no_style(), models)
    if sql:
        with connection.cursor() as cursor:
            for statement in sql:
                cursor.execute(statement)


@transaction.atomic
def restore_backup(bundle, actor_username=""):
    validate_backup(bundle)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ["kofad_full_restore_v1"])
    _truncate_tables(_table_names_for_restore())

    restored_models = []
    seen = set()
    for obj in serializers.deserialize("json", bundle["fixture"], ignorenonexistent=False):
        obj.save()
        model = obj.object.__class__
        if model not in seen:
            restored_models.append(model)
            seen.add(model)
    _reset_sequences(restored_models)

    if not User.objects.filter(is_active=True, is_superuser=True).exists():
        raise BackupError("Restore would leave KOFAD without an active system administrator.")
    if not Company.objects.exists():
        raise BackupError("Restore contains no company configuration.")
    branch = Branch.objects.filter(active=True).first()
    if not branch:
        raise BackupError("Restore contains no active business location.")

    restored_actor = User.objects.filter(username=actor_username).first()
    Audit.objects.create(
        branch=branch,
        actor=restored_actor if restored_actor and restored_actor.is_active else None,
        action="system.restore.completed",
        reference=bundle.get("fixture_sha256", "")[:32],
        detail={
            "backup_created_at": bundle.get("created_at"),
            "backup_created_by": bundle.get("created_by"),
            "restored_records": bundle.get("record_count"),
            "schema_migrations": len(bundle.get("migrations") or []),
        },
    )
    return backup_summary(bundle)


def reset_models():
    result = []
    for app_label in BACKUP_APP_LABELS:
        result.extend(
            model for model in apps.get_app_config(app_label).get_models()
            if _managed(model)
        )
    return result


def _fresh_start_tables():
    existing = set(connection.introspection.table_names())
    return sorted({
        name for name in existing
        if name.startswith("core_") or name.startswith("marketplace_")
        or name in {"django_admin_log", "django_session"}
    })


def _rebuild_standard_roles():
    Group.objects.all().delete()
    for name, codes in FRESH_START_ROLES.items():
        group = Group.objects.create(name=name)
        group.permissions.set(
            Permission.objects.filter(
                content_type__app_label="core",
                codename__in=codes,
            )
        )


@transaction.atomic
def reset_business_data(actor):
    if not actor or not actor.is_active or not actor.is_superuser:
        raise BackupError("Only an active system administrator can reset business data.")

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ["kofad_business_reset_v2"])

    actor_pk = actor.pk
    actor_username = actor.username
    access = Access.objects.filter(user=actor).first()
    access_security = {
        "recovery_phone": access.recovery_phone if access else "",
        "totp_secret": access.totp_secret if access else "",
        "must_change_password": access.must_change_password if access else False,
        "force_password_change": access.force_password_change if access else False,
    }

    models = reset_models()
    _truncate_tables(_fresh_start_tables())

    # Staff/demo identities are business data. Keep only the system administrator
    # who deliberately initiated the fresh start.
    User.objects.exclude(pk=actor_pk).delete()
    actor = User.objects.get(pk=actor_pk)
    actor.groups.clear()
    actor.user_permissions.clear()
    actor.is_active = True
    actor.is_staff = True
    actor.is_superuser = True
    actor.save(update_fields=["is_active", "is_staff", "is_superuser"])

    _rebuild_standard_roles()

    # Recreate only the minimum clean shell KOFAD needs to boot.
    company = Company.objects.create()
    branch = Branch.objects.create(name="Main branch", code="main", address="", active=True)
    access = Access.objects.create(
        user=actor,
        recovery_phone=access_security["recovery_phone"],
        session_version=1,
        must_change_password=access_security["must_change_password"],
        force_password_change=access_security["force_password_change"],
        totp_secret=access_security["totp_secret"],
        totp_last_step=-1,
    )
    access.branches.add(branch)

    # TRUNCATE already clears sessions, but keep this explicit for non-PostgreSQL
    # test doubles and future storage changes.
    Session.objects.all().delete()

    logger.warning(
        "KOFAD fresh-start reset completed by administrator=%s; all core and marketplace data cleared",
        actor_username,
    )
    return {
        "cleared_model_count": len(models),
        "preserved": [
            "the administrator account used to perform the reset",
            "Django permission definitions",
            "fresh standard role templates",
        ],
        "company_id": company.pk,
        "branch_id": branch.pk,
    }


def recent_backup_downloaded(session):
    value = session.get("kofad_recent_backup_at")
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return False
    return timezone.now().timestamp() - timestamp <= RECENT_BACKUP_SECONDS


def mark_backup_downloaded(session):
    session["kofad_recent_backup_at"] = timezone.now().timestamp()
    session.modified = True


def maintenance_stats():
    counts = {}
    for app_label in BACKUP_APP_LABELS:
        for model in apps.get_app_config(app_label).get_models():
            if _managed(model):
                try:
                    counts[model._meta.label_lower] = model._default_manager.count()
                except Exception:
                    counts[model._meta.label_lower] = None
    return counts
