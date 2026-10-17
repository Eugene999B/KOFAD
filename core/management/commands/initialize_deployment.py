import os
import uuid

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from core.models import Access, Audit
from core.services import audit


class Command(BaseCommand):
    help = "Apply migrations and explicitly bootstrap a fresh deployment when the one-time password is supplied."

    def handle(self, *args, **options):
        # Web and worker releases share a database. Serialize all migration runners.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(734001620)")
        try:
            call_command("migrate", interactive=False)
            if os.environ.get("KOFAD_INITIAL_ADMIN_PASSWORD"):
                if User.objects.filter(username__iexact="ADMIN").exists():
                    self.stdout.write("ADMIN already exists; credentials and permissions were preserved.")
                else:
                    call_command("bootstrap_admin", confirm_initial_setup=True)
            if os.environ.get("KOFAD_OWNER_ADMIN_PHONE", "").strip():
                call_command("provision_owner_admin", confirm_owner_admin=True)

            rotation_password = os.environ.get("KOFAD_ADMIN_ROTATION_PASSWORD", "")
            if rotation_password:
                # A password left in Railway variables must never act as an
                # indefinite reset command across web/SMS deployments.
                # Operators must supply a fresh unique ID for each recovery.
                raw_request_id = os.environ.get("KOFAD_ADMIN_ROTATION_REQUEST_ID", "").strip()
                try:
                    rotation_id = str(uuid.UUID(raw_request_id)) if raw_request_id else ""
                except (ValueError, AttributeError):
                    rotation_id = ""
                if not rotation_id:
                    self.stderr.write(
                        "ADMIN rotation ignored: set a new UUID in KOFAD_ADMIN_ROTATION_REQUEST_ID."
                    )
                else:
                    admin = User.objects.filter(username__iexact="ADMIN").first()
                    if not admin:
                        self.stderr.write("ADMIN rotation ignored: ADMIN does not exist.")
                    elif not (admin.is_active and admin.is_staff and admin.is_superuser):
                        self.stderr.write(
                            "ADMIN rotation ignored: account is not an active superuser."
                        )
                    else:
                        with transaction.atomic():
                            admin = User.objects.select_for_update().get(pk=admin.pk)
                            already_used = Audit.objects.filter(
                                action__in=[
                                    "admin.emergency_credential_rotated",
                                    "admin.emergency_credential_rotation_consumed",
                                ],
                                reference=str(admin.pk),
                                detail__request_id=rotation_id,
                            ).exists()
                            if already_used:
                                self.stdout.write(
                                    "ADMIN emergency rotation request already consumed; credentials and MFA preserved."
                                )
                            else:
                                validate_password(rotation_password, user=admin)
                                if admin.check_password(rotation_password):
                                    # Consume even an already-matching password:
                                    # a later password change cannot reactivate it.
                                    audit(
                                        None, None, "admin.emergency_credential_rotation_consumed",
                                        admin.pk, {"request_id": rotation_id, "changed": False},
                                        category="security", severity="critical",
                                        entity_type="user", entity_id=str(admin.pk),
                                    )
                                    self.stdout.write(
                                        "ADMIN rotation request consumed; existing MFA preserved."
                                    )
                                else:
                                    admin.set_password(rotation_password)
                                    admin.save(update_fields=["password"])
                                    access, _ = Access.objects.select_for_update().get_or_create(user=admin)
                                    access.force_password_change = True
                                    access.totp_secret = ""
                                    access.totp_last_step = -1
                                    # User.save(password=...) already increments
                                    # access.session_version in core.signals;
                                    # doing it again needlessly invalidates it twice.
                                    access.save(update_fields=[
                                        "force_password_change", "totp_secret", "totp_last_step",
                                    ])
                                    audit(
                                        None, None, "admin.emergency_credential_rotated", admin.pk,
                                        {
                                            "request_id": rotation_id,
                                            "force_password_change": True,
                                            "sessions_invalidated": True,
                                        },
                                        category="security", severity="critical",
                                        entity_type="user", entity_id=str(admin.pk),
                                    )
                                    self.stdout.write(
                                        "ADMIN emergency credential rotation applied once; "
                                        "replace temporary credential at first sign-in."
                                    )
            if os.environ.get("KOFAD_LOAD_SHOWCASE_DATA", "").strip() == "1":
                call_command("load_showcase_data", confirm_live_showcase=True)
            # Existing showcase environments must keep pace with newly added KOFAD modules.
            # This command is a guarded no-op unless the SHOWCASE-DATA-V1 marker exists.
            call_command("complete_showcase_data", confirm_live_showcase=True)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(734001620)")
