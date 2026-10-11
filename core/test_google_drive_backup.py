"""Security, integrity and disable-by-default checks for Google weekly backups."""
import hashlib
import hmac
import time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from . import google_drive_backup, maintenance


KEY = "test-only-hmac-authorization-key-which-is-over-32-chars"
PHRASE = "test-only-offline-backup-passphrase-123456789"
PATH = google_drive_backup.BACKUP_PATH


def signed_headers(*, timestamp=None, key=KEY):
    timestamp = int(time.time()) if timestamp is None else int(timestamp)
    payload = f"v1\nPOST\n{PATH}\n{timestamp}".encode("ascii")
    digest = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
    return {
        "HTTP_HOST": "staff.kofadimpex.com",
        "HTTP_X_KOFAD_BACKUP_TIMESTAMP": str(timestamp),
        "HTTP_X_KOFAD_BACKUP_SIGNATURE": digest,
    }


@override_settings(ALLOWED_HOSTS=["testserver", "staff.kofadimpex.com", "kofadimpex.com"])
class GoogleWeeklyExportTests(TestCase):
    def test_export_is_off_by_default_and_reveals_nothing(self):
        response = self.client.post(PATH, data=b"", content_type="application/octet-stream", **signed_headers())
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.content)
        self.assertEqual(response["Cache-Control"], "no-store")

    @override_settings(KOFAD_GDRIVE_BACKUP_ENABLED=True)
    def test_misconfigured_secrets_stay_closed(self):
        response = self.client.post(PATH, data=b"", content_type="application/octet-stream", **signed_headers())
        self.assertEqual(response.status_code, 404)

    @override_settings(
        KOFAD_GDRIVE_BACKUP_ENABLED=True,
        KOFAD_GDRIVE_BACKUP_HMAC_KEY=KEY,
        KOFAD_GDRIVE_BACKUP_PASSPHRASE=PHRASE,
    )
    def test_signature_timestamp_host_and_method_must_be_correct(self):
        with patch.object(maintenance, "encrypted_backup_bytes") as produce:
            self.assertEqual(self.client.post(PATH, data=b"", content_type="application/octet-stream", HTTP_HOST="staff.kofadimpex.com").status_code, 403)
            old = signed_headers(timestamp=int(time.time()) - 1200)
            self.assertEqual(self.client.post(PATH, data=b"", content_type="application/octet-stream", **old).status_code, 403)
            bad = signed_headers(key="different-signing-key-with-minimum-length")
            self.assertEqual(self.client.post(PATH, data=b"", content_type="application/octet-stream", **bad).status_code, 403)
            host = signed_headers()
            host["HTTP_HOST"] = "kofadimpex.com"
            self.assertEqual(self.client.post(PATH, data=b"", content_type="application/octet-stream", **host).status_code, 404)
            self.assertEqual(self.client.get(PATH, **signed_headers()).status_code, 405)
            self.assertEqual(self.client.post(PATH, data="not-empty", content_type="text/plain", **signed_headers()).status_code, 400)
            produce.assert_not_called()

    @override_settings(
        KOFAD_GDRIVE_BACKUP_ENABLED=True,
        KOFAD_GDRIVE_BACKUP_HMAC_KEY=KEY,
        KOFAD_GDRIVE_BACKUP_PASSPHRASE=PHRASE,
    )
    def test_valid_request_returns_encrypted_restorable_file_and_digest(self):
        User.objects.create_superuser("backup-restore-test", "backups@example.test", "long-password-123456")
        response = self.client.post(PATH, data=b"", content_type="application/octet-stream", **signed_headers())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(maintenance.ENCRYPTED_BACKUP_MAGIC))
        self.assertEqual(
            response["X-Kofad-Backup-SHA256"],
            hashlib.sha256(response.content).hexdigest(),
        )
        self.assertIn("KOFAD-FULL-BACKUP-", response["Content-Disposition"])
        self.assertIn(".kofad.enc", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", response["Cache-Control"])
        bundle = maintenance.parse_uploaded_backup(response.content, PHRASE)
        self.assertEqual(bundle["format"], maintenance.BACKUP_FORMAT)

    @override_settings(
        KOFAD_GDRIVE_BACKUP_ENABLED=True,
        KOFAD_GDRIVE_BACKUP_HMAC_KEY=KEY,
        KOFAD_GDRIVE_BACKUP_PASSPHRASE=PHRASE,
    )
    def test_transfer_size_limit_does_not_leak_backup(self):
        with patch.object(maintenance, "encrypted_backup_bytes", return_value=b"x" * (google_drive_backup.GOOGLE_TRANSFER_LIMIT + 1)):
            response = self.client.post(PATH, data=b"", content_type="application/octet-stream", **signed_headers())
        self.assertEqual(response.status_code, 413)
        self.assertFalse(response.content)
