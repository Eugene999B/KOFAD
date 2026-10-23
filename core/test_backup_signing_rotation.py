"""Offline compatibility contracts for rotating full-system backup signatures.

Never calls restore_backup or changes live users, sales, payments or ledgers.
"""
import json
from copy import deepcopy

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core import maintenance
from core.models import Branch, Company


OLD_DJANGO = "former-django-signing-secret-for-archived-backups-2026"
NEW_DJANGO = "new-django-signing-secret-for-archived-backups-2026"
FIRST = "dedicated-backup-key-id-one-2026-at-least-thirty-two"
SECOND = "dedicated-backup-key-id-two-2026-at-least-thirty-two"


class BackupSigningRotationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            "archive-admin", "archive@example.test", "long-safe-password-for-backup"
        )
        Branch.objects.create(name="Main", code="main")
        Company.objects.create()

    def test_old_archive_verifies_after_django_rotation_with_explicit_escrow(self):
        with override_settings(
            SECRET_KEY=OLD_DJANGO,
            KOFAD_BACKUP_SIGNING_KEY="",
            KOFAD_BACKUP_PREVIOUS_SIGNING_KEYS_JSON="",
        ):
            bundle = maintenance.create_backup(self.owner)
            self.assertNotIn("signature_key_id", bundle)
            self.assertEqual(maintenance.validate_backup(bundle), bundle)

        with override_settings(
            SECRET_KEY=NEW_DJANGO,
            KOFAD_BACKUP_SIGNING_KEY=SECOND,
            KOFAD_BACKUP_SIGNING_KEY_ID="backup-v2",
            KOFAD_BACKUP_PREVIOUS_SIGNING_KEYS_JSON=json.dumps({"legacy": OLD_DJANGO}),
        ):
            self.assertEqual(maintenance.validate_backup(bundle), bundle)
        with override_settings(
            SECRET_KEY=NEW_DJANGO,
            KOFAD_BACKUP_SIGNING_KEY=SECOND,
            KOFAD_BACKUP_SIGNING_KEY_ID="backup-v2",
            KOFAD_BACKUP_PREVIOUS_SIGNING_KEYS_JSON="",
        ):
            with self.assertRaises(maintenance.BackupError):
                maintenance.validate_backup(bundle)

    def test_dedicated_key_rotation_preserves_old_archive_with_key_id(self):
        with override_settings(
            KOFAD_BACKUP_SIGNING_KEY=FIRST,
            KOFAD_BACKUP_SIGNING_KEY_ID="backup-v1",
            KOFAD_BACKUP_PREVIOUS_SIGNING_KEYS_JSON="",
        ):
            old = maintenance.create_backup(self.owner)
            self.assertEqual(old["signature_key_id"], "backup-v1")
            self.assertEqual(maintenance.validate_backup(old), old)

        with override_settings(
            KOFAD_BACKUP_SIGNING_KEY=SECOND,
            KOFAD_BACKUP_SIGNING_KEY_ID="backup-v2",
            KOFAD_BACKUP_PREVIOUS_SIGNING_KEYS_JSON=json.dumps({"backup-v1": FIRST}),
        ):
            self.assertEqual(maintenance.validate_backup(old), old)
            new = maintenance.create_backup(self.owner)
            self.assertEqual(new["signature_key_id"], "backup-v2")
            self.assertEqual(maintenance.validate_backup(new), new)
            tampered = deepcopy(old)
            tampered["signature_key_id"] = "backup-v2"
            with self.assertRaises(maintenance.BackupError):
                maintenance.validate_backup(tampered)

    def test_invalid_or_short_key_fails_closed(self):
        with override_settings(KOFAD_BACKUP_SIGNING_KEY="short"):
            with self.assertRaisesRegex(maintenance.BackupError, "at least 32"):
                maintenance.create_backup(self.owner)
        with override_settings(KOFAD_BACKUP_PREVIOUS_SIGNING_KEYS_JSON="{invalid"):
            with self.assertRaises(maintenance.BackupError):
                maintenance.validate_backup({"signature": "abc", "format": maintenance.BACKUP_FORMAT,
                                             "version": maintenance.BACKUP_VERSION})
