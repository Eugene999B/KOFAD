import os

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection


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
            if os.environ.get("KOFAD_LOAD_SHOWCASE_DATA", "").strip() == "1":
                call_command("load_showcase_data", confirm_live_showcase=True)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(734001620)")
