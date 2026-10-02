import copy
import json

from django.contrib.auth.models import Permission, User
from django.contrib.sessions.models import Session
from django.test import TestCase, TransactionTestCase

from . import maintenance
from .models import Audit, Branch, Company, Party, Product, Stock


class MaintenanceServiceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_superuser(
            "owner", "owner@example.test", "test-password-long-enough"
        )
        self.branch = Branch.objects.create(name="Main", code="main")
        Company.objects.create(name="KOFAD IMPEX ENTERPRISE")
        self.product = Product.objects.create(
            name="Hydraulic filter",
            sku="HYD-FILTER",
            pack_size=12,
            pack_name="box",
            retail_unit="60",
            retail_pack="680",
            wholesale_unit="55",
            wholesale_pack="620",
            cost="35",
        )
        Stock.objects.create(branch=self.branch, product=self.product, quantity=120)
        Party.objects.create(
            branch=self.branch,
            kind="customer",
            name="Ama Mensah",
            phone="+233241234567",
        )

    def test_signed_backup_rejects_tampering(self):
        bundle = maintenance.create_backup(self.user)
        maintenance.validate_backup(bundle)

        changed = copy.deepcopy(bundle)
        changed["created_by"] = "attacker"
        with self.assertRaisesRegex(maintenance.BackupError, "signature"):
            maintenance.validate_backup(changed)

        fixture_changed = copy.deepcopy(bundle)
        rows = json.loads(fixture_changed["fixture"])
        rows[-1]["fields"] = dict(rows[-1]["fields"])
        rows[-1]["fields"]["name"] = "Tampered"
        fixture_changed["fixture"] = json.dumps(rows)
        fixture_changed["signature"] = maintenance._sign(fixture_changed)
        with self.assertRaisesRegex(maintenance.BackupError, "checksum"):
            maintenance.validate_backup(fixture_changed)

    def test_reset_clears_business_data_but_preserves_access_and_configuration(self):
        bundle = maintenance.create_backup(self.user)
        self.assertGreater(bundle["record_count"], 0)

        result = maintenance.reset_business_data(self.user)

        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(Party.objects.count(), 0)
        self.assertEqual(Stock.objects.count(), 0)
        self.assertTrue(User.objects.filter(username="owner", is_superuser=True).exists())
        self.assertTrue(Branch.objects.filter(code="main", active=True).exists())
        self.assertEqual(Company.objects.get().name, "KOFAD IMPEX ENTERPRISE")
        self.assertEqual(Audit.objects.filter(action="system.business_data_reset").count(), 1)
        self.assertGreater(result["cleared_model_count"], 5)

    def test_full_backup_restore_round_trip(self):
        bundle = maintenance.create_backup(self.user)
        original_product_count = Product.objects.count()
        original_party_count = Party.objects.count()
        Session.objects.create(
            session_key="stale-session-before-restore",
            session_data="e30:1test:invalid",
            expire_date=timezone.now() + timezone.timedelta(days=1),
        )

        Product.objects.create(
            name="Temporary product",
            sku="TEMP-DELETE",
            retail_unit="1",
            cost="1",
        )
        Company.objects.update(name="Changed after backup")
        Party.objects.create(
            branch=self.branch,
            kind="customer",
            name="Temporary Customer",
            phone="+233201111111",
        )

        result = maintenance.restore_backup(bundle, actor_username="owner")

        self.assertEqual(result["record_count"], bundle["record_count"])
        self.assertEqual(Product.objects.count(), original_product_count)
        self.assertFalse(Product.objects.filter(sku="TEMP-DELETE").exists())
        self.assertEqual(Party.objects.count(), original_party_count)
        self.assertEqual(Company.objects.get().name, "KOFAD IMPEX ENTERPRISE")
        self.assertFalse(Session.objects.exists())
        restored = User.objects.get(username="owner")
        self.assertTrue(restored.check_password("test-password-long-enough"))
        self.assertTrue(Audit.objects.filter(action="system.restore.completed").exists())


class MaintenanceViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin", "admin@example.test", "test-password-long-enough"
        )
        self.staff = User.objects.create_user(
            "staff", password="staff-password-long-enough"
        )
        manage = Permission.objects.get(codename="manage_company")
        self.staff.user_permissions.add(manage)
        self.branch = Branch.objects.create(name="Main", code="main")
        self.admin.access.branches.add(self.branch)
        self.staff.access.branches.add(self.branch)
        Company.objects.create()

    def _login(self, user):
        self.client.force_login(user)
        user.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = user.access.session_version
        session["mfa_ok"] = True
        session["branch"] = self.branch.pk
        session.save()

    def test_recovery_center_is_system_administrator_only(self):
        self._login(self.staff)
        self.assertEqual(self.client.get("/settings/backup/").status_code, 403)

        self.client.logout()
        self._login(self.admin)
        response = self.client.get("/settings/backup/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Backup, restore &amp; reset")

    def test_download_marks_recent_safety_backup(self):
        self._login(self.admin)
        response = self.client.get("/settings/backup/download/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "no-store")
        parsed = maintenance.parse_backup(response.content)
        self.assertEqual(parsed["format"], maintenance.BACKUP_FORMAT)
        self.assertTrue(maintenance.recent_backup_downloaded(self.client.session))

    def test_reset_requires_recent_backup_and_exact_confirmation(self):
        self._login(self.admin)
        response = self.client.post("/settings/backup/", {
            "action": "reset",
            "password": "test-password-long-enough",
            "confirmation": maintenance.RESET_CONFIRMATION,
            "understand": "yes",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Download a fresh backup before resetting")
