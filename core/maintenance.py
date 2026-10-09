"""Signed full-system backups, exact-schema restore, and guarded business-data reset."""
import base64
import hashlib
import hmac
import json
import logging
import os
import zlib
from collections import Counter
from datetime import timezone as dt_timezone
from itertools import chain

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

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
MAX_BACKUP_BYTES = 256 * 1024 * 1024
MAX_ENCRYPTED_BACKUP_BYTES = MAX_BACKUP_BYTES + 1024 * 1024
ENCRYPTED_BACKUP_MAGIC = b"KOFAD-ENCRYPTED-BACKUP-V1\n"
ENCRYPTED_BACKUP_FORMAT = "kofad-encrypted-backup"
ENCRYPTED_BACKUP_VERSION = 1
BACKUP_KDF_N = 2 ** 15
BACKUP_KDF_R = 8
BACKUP_KDF_P = 1
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
    # A single snapshot prevents sales and payments changing between model reads.
    with transaction.atomic():
        if connection.vendor == "postgresql":
            tables = ", ".join(connection.ops.quote_name(name) for name in _table_names_for_restore())
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '10s'")
                cursor.execute(f"LOCK TABLE {tables} IN SHARE MODE")
        fixture = _serialize_fixture()
        migrations = current_migrations()
    records = json.loads(fixture)
    coverage = sorted(backup_model_labels())
    counts = Counter({label: 0 for label in coverage})
    counts.update(row["model"] for row in records)
    bundle = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": timezone.now().astimezone(dt_timezone.utc).isoformat(),
        "created_by": getattr(actor, "username", "") if actor else "",
        "database_engine": connection.vendor,
        "migrations": migrations,
        "record_count": len(records),
        "model_inventory": coverage,
        "model_counts": dict(sorted(counts.items())),
        "fixture_sha256": hashlib.sha256(fixture.encode("utf-8")).hexdigest(),
        "fixture": fixture,
    }
    bundle["signature"] = _sign(bundle)
    return bundle


def backup_bytes(actor=None):
    bundle = create_backup(actor)
    data = json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    if len(data) > MAX_BACKUP_BYTES:
        raise BackupError("Uncompressed backup exceeds the 256 MB safety limit. "
                          "Use a controlled database-level recovery process for a larger system.")
    return data


def validate_backup_passphrase(passphrase):
    passphrase = str(passphrase or "")
    if len(passphrase) < 16:
        raise BackupError("Use a backup passphrase of at least 16 characters.")
    if len(passphrase) > 1024:
        raise BackupError("The backup passphrase is too long.")
    return passphrase


def _backup_key(passphrase, salt):
    return Scrypt(
        salt=salt,
        length=32,
        n=BACKUP_KDF_N,
        r=BACKUP_KDF_R,
        p=BACKUP_KDF_P,
    ).derive(passphrase.encode("utf-8"))


def encrypted_backup_bytes(actor=None, passphrase=""):
    """Create a signed backup wrapped in passphrase-derived AES-256-GCM encryption."""
    passphrase = validate_backup_passphrase(passphrase)
    plaintext = backup_bytes(actor)
    if len(plaintext) > MAX_BACKUP_BYTES:
        raise BackupError("Backup content exceeds the supported 256 MB safety limit.")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    # Compress the signed snapshot before encryption. PostgreSQL-backed image
    # blobs and extensive communication history can make plaintext backups large.
    # Older encrypted files without the compression header still decrypt.
    compressed = zlib.compress(plaintext, level=6)
    use_compression = len(compressed) < len(plaintext)
    header = {
        "format": ENCRYPTED_BACKUP_FORMAT,
        "version": ENCRYPTED_BACKUP_VERSION,
        "cipher": "AES-256-GCM",
        "kdf": "scrypt",
        "n": BACKUP_KDF_N,
        "r": BACKUP_KDF_R,
        "p": BACKUP_KDF_P,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "compression": "zlib" if use_compression else "none",
    }
    aad = _canonical(header)
    ciphertext = AESGCM(_backup_key(passphrase, salt)).encrypt(
        nonce, compressed if use_compression else plaintext, aad
    )
    encoded_header = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    output = ENCRYPTED_BACKUP_MAGIC + encoded_header + b"\n" + ciphertext
    if len(output) > MAX_ENCRYPTED_BACKUP_BYTES:
        raise BackupError("Encrypted backup is larger than the supported limit.")
    return output


def _decrypt_backup(raw, passphrase):
    passphrase = validate_backup_passphrase(passphrase)
    if len(raw) > MAX_ENCRYPTED_BACKUP_BYTES:
        raise BackupError("Encrypted backup is larger than the supported limit.")
    try:
        remainder = raw[len(ENCRYPTED_BACKUP_MAGIC):]
        encoded_header, ciphertext = remainder.split(b"\n", 1)
        if not encoded_header or len(encoded_header) > 4096 or not ciphertext:
            raise ValueError
        header = json.loads(encoded_header.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("This encrypted KOFAD backup is malformed.") from exc
    if not isinstance(header, dict):
        raise BackupError("This encrypted KOFAD backup is malformed.")
    expected = {
        "format": ENCRYPTED_BACKUP_FORMAT,
        "version": ENCRYPTED_BACKUP_VERSION,
        "cipher": "AES-256-GCM",
        "kdf": "scrypt",
        "n": BACKUP_KDF_N,
        "r": BACKUP_KDF_R,
        "p": BACKUP_KDF_P,
    }
    if any(header.get(key) != value for key, value in expected.items()):
        raise BackupError("Unsupported encrypted KOFAD backup settings.")
    try:
        salt = base64.b64decode(header.get("salt", ""), validate=True)
        nonce = base64.b64decode(header.get("nonce", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise BackupError("This encrypted KOFAD backup is malformed.") from exc
    if len(salt) != 16 or len(nonce) != 12:
        raise BackupError("This encrypted KOFAD backup is malformed.")
    try:
        plaintext = AESGCM(_backup_key(passphrase, salt)).decrypt(
            nonce, ciphertext, _canonical(header)
        )
    except InvalidTag as exc:
        raise BackupError("Backup passphrase is incorrect or the encrypted file was altered.") from exc
    compression = header.get("compression", "none")
    if compression not in {"none", "zlib"}:
        raise BackupError("Unsupported backup compression.")
    if compression == "zlib":
        try:
            decoder = zlib.decompressobj()
            uncompressed = decoder.decompress(plaintext, MAX_BACKUP_BYTES + 1)
            if (len(uncompressed) > MAX_BACKUP_BYTES or not decoder.eof
                    or decoder.unconsumed_tail or decoder.unused_data):
                raise BackupError("Encrypted backup decompressed beyond the safety limit or is incomplete.")
            plaintext = uncompressed
        except zlib.error as exc:
            raise BackupError("Encrypted backup compression is damaged.") from exc
    if len(plaintext) > MAX_BACKUP_BYTES:
        raise BackupError("Decrypted backup is larger than the supported limit.")
    return plaintext


def parse_uploaded_backup(raw, passphrase=""):
    if not isinstance(raw, (bytes, bytearray)):
        raise BackupError("Backup content is missing.")
    raw = bytes(raw)
    if raw.startswith(ENCRYPTED_BACKUP_MAGIC):
        return parse_backup(_decrypt_backup(raw, passphrase))
    # Legacy signed plaintext backups remain restorable so recovery is not broken.
    return parse_backup(raw)


def parse_backup(raw):
    if not isinstance(raw, (bytes, bytearray)):
        raise BackupError("Backup content is missing.")
    if len(raw) > MAX_BACKUP_BYTES:
        raise BackupError("Backup file exceeds the supported 256 MB safety limit.")
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
    if any(not isinstance(row, dict) or not isinstance(row.get("fields"), dict) or "pk" not in row for row in records):
        raise BackupError("Backup contains malformed records.")
    allowed = backup_model_labels()
    unexpected = sorted({str(row.get("model", "")) for row in records if row.get("model") not in allowed})
    if unexpected:
        raise BackupError("Backup contains unsupported model data: " + ", ".join(unexpected[:5]))
    if not isinstance(bundle.get("record_count"), int) or len(records) != bundle["record_count"]:
        raise BackupError("Backup record count does not match its manifest.")

    actual_counts = Counter(row["model"] for row in records)
    manifest = bundle.get("model_counts")
    if not isinstance(manifest, dict) or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in manifest.values()):
        raise BackupError("Backup model counts are malformed.")
    inventory = bundle.get("model_inventory")
    if inventory is not None:
        if (not isinstance(inventory, list)
                or inventory != sorted(allowed)
                or set(manifest) != set(inventory)):
            raise BackupError("Backup coverage does not match the current KOFAD model inventory.")
        actual_counts.update({label: 0 for label in inventory})
    if dict(actual_counts) != manifest:
        raise BackupError("Backup model counts do not match its manifest.")

    if not actual_counts.get("auth.user"):
        raise BackupError("Backup contains no user accounts.")
    return bundle


def backup_summary(bundle):
    return {
        "created_at": bundle.get("created_at"),
        "created_by": bundle.get("created_by") or "Unknown",
        "record_count": bundle.get("record_count", 0),
        "model_count": len(bundle.get("model_inventory") or bundle.get("model_counts") or {}),
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


def _quarantine_restored_delivery_and_recovery():
    """A backup is evidence of the past, not permission to resend or log in.

    Restoring past queued messages could cause repeat financial/debt notices.
    Similarly, staff invitations and one-time codes must not become active
    again just because an older record was restored.
    """
    from marketplace.models import CustomerEmailRecovery, EmailIdentity, EmailNotice
    from .models import Message, SmsAttempt, StaffInvitation, WhatsAppAttempt, WhatsAppBotReply

    now = timezone.now()
    EmailIdentity.objects.exclude(code_digest="").update(
        code_digest="", pending_email="", requested_at=None, expires_at=None,
        last_sent_at=None, code_attempts=0,
    )
    CustomerEmailRecovery.objects.filter(used=False).update(
        used=True, expires_at=now, code_digest="",
    )
    StaffInvitation.objects.filter(consumed_at__isnull=True).update(expires_at=now)
    EmailNotice.objects.filter(status__in=["queued", "sending"]).update(
        status="failed", next_attempt_at=now,
    )
    Message.objects.filter(status__in=["queued", "sending"]).update(
        status="unknown", last_error="Restored snapshot: delivery status requires manager review.",
    )
    SmsAttempt.objects.filter(status__in=["queued", "sending"]).update(status="unknown")
    WhatsAppAttempt.objects.filter(status__in=["queued", "sending"]).update(status="unknown")
    WhatsAppBotReply.objects.filter(status__in=["queued", "sending"]).update(
        status="unknown", error="Restored snapshot: delivery status requires manual review.",
    )


def _verify_restored_rows(bundle):
    expected = bundle.get("model_counts") or {}
    for model in backup_models():
        name = model._meta.label_lower
        if model._default_manager.count() != expected.get(name, 0):
            raise BackupError(f"Restored {name} record count does not agree with its signed manifest.")


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
    _verify_restored_rows(bundle)
    _quarantine_restored_delivery_and_recovery()

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

    # Do not report a successful system reset if any old business row remains.
    # Everything is inside the same database transaction and will roll back.
    expected_shell = {"core.company": 1, "core.branch": 1, "core.access": 1}
    for model in models:
        expected = expected_shell.get(model._meta.label_lower, 0)
        if model._default_manager.count() != expected:
            raise BackupError(
                f"Full reset did not clear {model._meta.label_lower} completely. "
                "The reset was rolled back."
            )
    if User.objects.count() != 1 or not User.objects.filter(pk=actor_pk, is_active=True, is_superuser=True).exists():
        raise BackupError("Administrator access was not preserved; reset rolled back.")
    if Session.objects.exists():
        raise BackupError("Old login sessions survived; reset rolled back.")

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


def mark_backup_downloaded(session, encrypted_bytes=None):
    session["kofad_recent_backup_at"] = timezone.now().timestamp()
    session["kofad_recent_backup_sha256"] = (
        hashlib.sha256(encrypted_bytes).hexdigest() if encrypted_bytes else ""
    )
    # A newer download always requires fresh round-trip validation.
    session.pop("kofad_verified_backup_sha256", None)
    session.modified = True


def mark_backup_verified(session, file_sha256):
    """Prove the operator possesses the exact file downloaded this session."""
    if not recent_backup_downloaded(session):
        return False
    digest = session.get("kofad_recent_backup_sha256", "")
    if not digest or not hmac.compare_digest(digest, str(file_sha256 or "")):
        return False
    session["kofad_verified_backup_sha256"] = digest
    session.modified = True
    return True


def recent_backup_verified(session):
    if not recent_backup_downloaded(session):
        return False
    digest = session.get("kofad_recent_backup_sha256", "")
    return bool(digest and hmac.compare_digest(
        digest, session.get("kofad_verified_backup_sha256", "")
    ))


COVERAGE_GROUPS = (
    ("Operations & stock", {"branch", "company", "product", "stock", "party", "document",
                            "line", "payment", "allocation", "movement", "operation", "closing",
                            "heldsale", "idempotency", "audit", "correction"}),
    ("Financial governance & payroll", {"manualjournal", "manualjournalline", "worker",
                                         "workerdocument", "payrollrule", "payrollperiod",
                                         "payrollentry", "payrollpayment", "stockcount",
                                         "stockcountline", "supplierreturn", "quarantineitem"}),
    ("Business messages & access", {"access", "message", "smsattempt", "smsevent",
                                     "whatsappattempt", "whatsappwebhookevent",
                                     "messagetemplate", "staffinvitation",
                                     "whatsappbotreply", "whatsappbotcontact",
                                     "debtsettings", "communicationsettings",
                                     "managementcontact", "customerservicecontact"}),
)


def backup_coverage(stats=None):
    """Include zero-row models so newly added settings are not silently missed."""
    if stats is None:
        stats = maintenance_stats()
    rows = []
    for label in sorted(backup_model_labels()):
        if label in {"contenttypes.contenttype", "auth.permission", "auth.group",
                     "auth.user", "admin.logentry"}:
            category = "Staff identities, roles & permissions"
        elif label.startswith("marketplace."):
            category = "Online Market, payments, files & customer conversations"
        else:
            name = label.split(".", 1)[-1]
            group = next((title for title, names in COVERAGE_GROUPS if name in names), None)
            category = group or "Core business, finance & integrations"
        rows.append({
            "label": label,
            "area": category,
            "records": stats.get(label) if stats.get(label) is not None else 0,
        })
    areas = {}
    for row in rows:
        area = areas.setdefault(row["area"], {"name": row["area"], "models": 0, "records": 0})
        area["models"] += 1
        area["records"] += row["records"]
    return {
        "model_count": len(rows),
        "record_count": sum(x["records"] for x in rows),
        "areas": sorted(areas.values(), key=lambda x: x["name"]),
        "models": rows,
        "excluded_temporary": sorted(EXCLUDED_BACKUP_MODELS),
    }


def maintenance_stats():
    counts = {}
    for app_label in BACKUP_APP_LABELS:
        for model in apps.get_app_config(app_label).get_models():
            if _managed(model):
                try:
                    counts[model._meta.label_lower] = model._default_manager.count()
                except Exception:
                    counts[model._meta.label_lower] = None
    # Identity, role, and Django admin logs are part of encrypted backups too.
    for model in (ContentType, Permission, Group, User, LogEntry):
        counts[model._meta.label_lower] = model._default_manager.count()
    return counts
