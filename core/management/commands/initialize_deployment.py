import os

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from core.models import Access
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
                admin = User.objects.filter(username__iexact="ADMIN").first()
                if admin:
                    validate_password(rotation_password, user=admin)
                    with transaction.atomic():
                        admin = User.objects.select_for_update().get(pk=admin.pk)
                        admin.set_password(rotation_password)
                        admin.save(update_fields=["password"])
                        access, _ = Access.objects.select_for_update().get_or_create(user=admin)
                        access.force_password_change = True
                        access.totp_secret = ""
                        access.totp_last_step = -1
                        access.save(update_fields=[
                            "force_password_change", "totp_secret", "totp_last_step"
                        ])
                        audit(
                            None, None, "admin.emergency_credential_rotated", admin.pk,
                            {"force_password_change": True},
                            category="security", severity="critical",
                            entity_type="user", entity_id=str(admin.pk),
                        )
                    self.stdout.write(
                        "ADMIN credential rotation applied; first sign-in must replace the temporary credential."
                    )
                else:
                    self.stdout.write("ADMIN credential rotation requested, but no ADMIN account exists.")
            if os.environ.get("KOFAD_LOAD_SHOWCASE_DATA", "").strip() == "1":
                call_command("load_showcase_data", confirm_live_showcase=True)
            # Existing showcase environments must keep pace with newly added KOFAD modules.
            # This command is a guarded no-op unless the SHOWCASE-DATA-V1 marker exists.
            call_command("complete_showcase_data", confirm_live_showcase=True)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(734001620)")
