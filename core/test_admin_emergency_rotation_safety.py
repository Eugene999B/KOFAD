"""Emergency ADMIN rotation cannot replay on subsequent deployments."""
import os
import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from .models import Access, Audit


TEMP_PASSWORD = "Kofad-Temporary-Recovery-Passphrase-2026!"
SECOND_PASSWORD = "Kofad-Second-Recovery-Passphrase-2026!"


class EmergencyRotationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "ADMIN", "admin@example.test", "old-admin-password-2026"
        )
        self.access, _ = Access.objects.get_or_create(user=self.admin)
        self.access.totp_secret = "test-enrolled-totp-secret"
        self.access.totp_last_step = 456
        self.access.force_password_change = False
        self.access.session_version = 4
        self.access.save()

    def run_initialize(self, **vars):
        # Leave real migrations/showcase commands alone; no production context
        # is ever used in these isolated Django tests.
        with patch.dict(os.environ, {
            "KOFAD_ADMIN_ROTATION_PASSWORD": vars.get("password", ""),
            "KOFAD_ADMIN_ROTATION_REQUEST_ID": vars.get("request_id", ""),
            "KOFAD_INITIAL_ADMIN_PASSWORD": "",
            "KOFAD_OWNER_ADMIN_PHONE": "",
            "KOFAD_LOAD_SHOWCASE_DATA": "",
        }), patch("core.management.commands.initialize_deployment.call_command"):
            call_command("initialize_deployment")
        self.admin.refresh_from_db()
        self.access.refresh_from_db()

    def events(self):
        return Audit.objects.filter(
            action__in=[
                "admin.emergency_credential_rotated",
                "admin.emergency_credential_rotation_consumed",
            ],
            reference=str(self.admin.pk),
        )

    def test_stale_password_without_unique_request_id_does_not_reset_admin(self):
        self.run_initialize(password=TEMP_PASSWORD)
        self.assertTrue(self.admin.check_password("old-admin-password-2026"))
        self.assertEqual(self.access.totp_secret, "test-enrolled-totp-secret")
        self.assertEqual(self.access.session_version, 4)
        self.assertEqual(self.events().count(), 0)

        self.run_initialize(password=TEMP_PASSWORD, request_id="not-a-uuid")
        self.assertTrue(self.admin.check_password("old-admin-password-2026"))
        self.assertEqual(self.events().count(), 0)

    def test_request_rotates_once_and_does_not_reset_later_changed_password(self):
        key = str(uuid.uuid4())
        self.run_initialize(password=TEMP_PASSWORD, request_id=key)
        self.assertTrue(self.admin.check_password(TEMP_PASSWORD))
        self.assertEqual(self.access.totp_secret, "")
        self.assertEqual(self.access.totp_last_step, -1)
        self.assertEqual(self.access.session_version, 5)
        self.assertTrue(self.access.force_password_change)
        self.assertEqual(self.events().count(), 1)
        self.assertEqual(self.events().first().detail["request_id"], key)

        self.admin.set_password("Admin-New-Private-Owner-Selected-Password!")
        self.admin.save(update_fields=["password"])
        self.access.totp_secret = "new-totp-after-recovery"
        self.access.session_version = 9
        self.access.save(update_fields=["totp_secret", "session_version"])

        self.run_initialize(password=TEMP_PASSWORD, request_id=key)
        self.assertTrue(self.admin.check_password("Admin-New-Private-Owner-Selected-Password!"))
        self.assertEqual(self.access.totp_secret, "new-totp-after-recovery")
        self.assertEqual(self.access.session_version, 9)
        self.assertEqual(self.events().count(), 1)

    def test_new_unique_request_can_perform_new_authorized_rotation(self):
        self.run_initialize(password=TEMP_PASSWORD, request_id=str(uuid.uuid4()))
        self.run_initialize(password=SECOND_PASSWORD, request_id=str(uuid.uuid4()))
        self.assertTrue(self.admin.check_password(SECOND_PASSWORD))
        self.assertEqual(self.access.session_version, 6)
        self.assertEqual(self.events().count(), 2)

    def test_matching_password_consumes_id_without_erasing_mfa(self):
        self.admin.set_password(TEMP_PASSWORD)
        self.admin.save(update_fields=["password"])
        # A normal password change already bumps the session_version through
        # core.signals; consuming an already-matching request must not bump it.
        self.access.refresh_from_db()
        version_before = self.access.session_version
        key = str(uuid.uuid4())
        self.run_initialize(password=TEMP_PASSWORD, request_id=key)
        self.assertTrue(self.admin.check_password(TEMP_PASSWORD))
        self.assertEqual(self.access.totp_secret, "test-enrolled-totp-secret")
        self.assertEqual(self.access.session_version, version_before)
        self.assertEqual(self.events().count(), 1)
        self.assertEqual(self.events().first().action, "admin.emergency_credential_rotation_consumed")

        self.admin.set_password("Admin-Own-Password-Updated-2026!")
        self.admin.save(update_fields=["password"])
        self.run_initialize(password=TEMP_PASSWORD, request_id=key)
        self.assertTrue(self.admin.check_password("Admin-Own-Password-Updated-2026!"))
        self.assertEqual(self.access.session_version, version_before + 1)
        self.assertEqual(self.events().count(), 1)

    def test_disabled_or_demoted_admin_is_not_permission_repaired(self):
        self.admin.is_active = False
        self.admin.is_superuser = False
        self.admin.save(update_fields=["is_active", "is_superuser"])
        self.run_initialize(password=TEMP_PASSWORD, request_id=str(uuid.uuid4()))
        self.assertTrue(self.admin.check_password("old-admin-password-2026"))
        self.assertEqual(self.access.totp_secret, "test-enrolled-totp-secret")
        self.assertEqual(self.events().count(), 0)
