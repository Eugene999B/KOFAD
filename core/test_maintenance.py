import copy
import json
from datetime import timedelta

from django.contrib.auth.models import Group, Permission, User
from django.contrib.sessions.models import Session
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from . import maintenance
from .models import Access, Audit, Branch, Company, Party, Product, Stock
from marketplace.models import Conversation, CustomerAccount, MarketListing, MarketListingImage, OtpThrottle


class MaintenanceServiceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_superuser(
            "owner", "owner@example.test", "test-password-long-enough"
        )
        self.branch = Branch.objects.create(name="Main", code="main")
        Company.objects.create(
            name="Demo Company To Clear",
            phone="+233241111111",
            secondary_phone="+233242222222",
            email="demo@example.test",
            whatsapp_phone="+233243333333",
            address="Demo address",
        )
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
        self.staff = User.objects.create_user(
            "demo-staff", password="demo-staff-password-long"
        )
        self.staff.access.branches.add(self.branch)
        self.market_customer = CustomerAccount.objects.create(
            phone="+233245550099",
            full_name="Demo Market Customer",
            email="market@example.test",
            password_hash="not-used-in-maintenance-test",
            verified_at=None,
        )
        self.listing = MarketListing.objects.create(
            product=self.product,
            enabled=True,
            title="Online Hydraulic Filter",
            description="Marketplace demo record",
            price_source="retail_unit",
            image_data=b"full-image-bytes",
            image_thumb=b"thumb-image-bytes",
            image_mime="image/webp",
        )
        MarketListingImage.objects.create(
            listing=self.listing,
            image_data=b"gallery-image-bytes",
            image_thumb=b"gallery-thumb-bytes",
            image_mime="image/webp",
            alt_text="Gallery evidence",
        )
        Conversation.objects.create(
            customer=self.market_customer,
            public_name=self.market_customer.full_name,
            public_phone=self.market_customer.phone,
            subject="Demo support conversation",
        )
        OtpThrottle.objects.create(
            phone="+233245550088",
            purpose="register",
            send_count=1,
            code_digest="a" * 64,
        )
        Group.objects.create(name="Demo Custom Role")
        Session.objects.create(
            session_key="demo-session",
            session_data="e30:1test:signature",
            expire_date=timezone.now() + timedelta(hours=1),
        )

    def test_full_backup_tracks_every_managed_business_model(self):
        expected = {
            model._meta.label_lower for model in maintenance.reset_models()
        } - maintenance.EXCLUDED_BACKUP_MODELS
        expected.update({
            "contenttypes.contenttype", "auth.permission", "auth.group",
            "auth.user", "admin.logentry",
        })
        self.assertEqual(maintenance.backup_model_labels(), expected)

    def test_signed_backup_with_malformed_records_fails_cleanly(self):
        import hashlib
        bundle = maintenance.create_backup(self.user)
        bundle["fixture"] = json.dumps([None])
        bundle["fixture_sha256"] = hashlib.sha256(bundle["fixture"].encode()).hexdigest()
        bundle["signature"] = maintenance._sign(bundle)
        with self.assertRaisesMessage(maintenance.BackupError, "malformed records"):
            maintenance.validate_backup(bundle)

    def test_encrypted_backup_requires_the_correct_passphrase(self):
        raw = maintenance.encrypted_backup_bytes(
            self.user, "correct-horse-battery-staple-1234"
        )
        restored = maintenance.parse_uploaded_backup(
            raw, "correct-horse-battery-staple-1234"
        )
        self.assertEqual(restored["format"], maintenance.BACKUP_FORMAT)
        with self.assertRaisesRegex(maintenance.BackupError, "incorrect|altered"):
            maintenance.parse_uploaded_backup(raw, "wrong-but-long-passphrase-1234")

        changed = bytearray(raw)
        changed[-1] ^= 1
        with self.assertRaisesRegex(maintenance.BackupError, "incorrect|altered"):
            maintenance.parse_uploaded_backup(
                bytes(changed), "correct-horse-battery-staple-1234"
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

    def test_reset_returns_core_and_marketplace_to_clean_launch_state(self):
        bundle = maintenance.create_backup(self.user)
        self.assertGreater(bundle["record_count"], 0)
        self.assertIn("marketplace.customeraccount", bundle["model_counts"])
        self.assertIn("marketplace.marketlisting", bundle["model_counts"])

        result = maintenance.reset_business_data(self.user)

        expected_shell_counts = {"core.company": 1, "core.branch": 1, "core.access": 1}
        for model in maintenance.reset_models():
            self.assertEqual(
                model._default_manager.count(),
                expected_shell_counts.get(model._meta.label_lower, 0),
                f"{model._meta.label_lower} unexpectedly survived the fresh-start reset",
            )

        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(Party.objects.count(), 0)
        self.assertEqual(Stock.objects.count(), 0)
        self.assertEqual(CustomerAccount.objects.count(), 0)
        self.assertEqual(MarketListing.objects.count(), 0)
        self.assertEqual(MarketListingImage.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(OtpThrottle.objects.count(), 0)
        self.assertEqual(Audit.objects.count(), 0)
        self.assertEqual(Session.objects.count(), 0)

        self.assertEqual(User.objects.count(), 1)
        owner = User.objects.get(username="owner")
        self.assertTrue(owner.is_superuser)
        self.assertFalse(User.objects.filter(username="demo-staff").exists())
        self.assertEqual(
            set(Group.objects.values_list("name", flat=True)),
            set(maintenance.FRESH_START_ROLES),
        )

        company = Company.objects.get()
        self.assertEqual(company.name, "KOFAD IMPEX ENTERPRISE")
        self.assertEqual(company.phone, "")
        self.assertEqual(company.secondary_phone, "")
        self.assertEqual(company.email, "")
        self.assertEqual(company.whatsapp_phone, "")
        self.assertEqual(company.address, "")

        self.assertEqual(Branch.objects.count(), 1)
        branch = Branch.objects.get(code="main")
        self.assertTrue(branch.active)
        self.assertEqual(branch.name, "Main branch")
        self.assertEqual(branch.address, "")
        access = Access.objects.get(user=owner)
        self.assertEqual(list(access.branches.all()), [branch])
        self.assertGreater(result["cleared_model_count"], 20)

    def test_full_backup_restore_round_trip_includes_marketplace_and_media(self):
        bundle = maintenance.create_backup(self.user)
        original_product_count = Product.objects.count()
        original_party_count = Party.objects.count()
        original_market_customer_count = CustomerAccount.objects.count()

        Product.objects.create(
            name="Temporary product",
            sku="TEMP-DELETE",
            retail_unit="1",
            cost="1",
        )
        Company.objects.update(name="Changed after backup", phone="+233209999999")
        Party.objects.create(
            branch=self.branch,
            kind="customer",
            name="Temporary Customer",
            phone="+233201111111",
        )
        CustomerAccount.objects.create(
            phone="+233245551234",
            full_name="Temporary Online Customer",
            password_hash="temporary",
        )
        MarketListing.objects.filter(pk=self.listing.pk).update(
            title="Changed listing",
            image_data=b"changed-image",
        )

        result = maintenance.restore_backup(bundle, actor_username="owner")

        self.assertEqual(result["record_count"], bundle["record_count"])
        self.assertEqual(Product.objects.count(), original_product_count)
        self.assertFalse(Product.objects.filter(sku="TEMP-DELETE").exists())
        self.assertEqual(Party.objects.count(), original_party_count)
        self.assertEqual(CustomerAccount.objects.count(), original_market_customer_count)
        self.assertFalse(CustomerAccount.objects.filter(phone="+233245551234").exists())

        company = Company.objects.get()
        self.assertEqual(company.name, "Demo Company To Clear")
        self.assertEqual(company.email, "demo@example.test")
        listing = MarketListing.objects.get(product__sku="HYD-FILTER")
        self.assertEqual(listing.title, "Online Hydraulic Filter")
        self.assertEqual(bytes(listing.image_data), b"full-image-bytes")
        gallery = listing.gallery_images.get()
        self.assertEqual(bytes(gallery.image_data), b"gallery-image-bytes")

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
        before = self.client.get("/settings/backup/status/")
        self.assertEqual(before.status_code, 200)
        self.assertFalse(before.json()["recent"])
        self.assertEqual(before["Cache-Control"], "no-store")

        response = self.client.post("/settings/backup/download/", {
            "password": "test-password-long-enough",
            "backup_passphrase": "test-backup-passphrase-1234",
            "backup_passphrase_confirm": "test-backup-passphrase-1234",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn(".kofad.enc", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "no-store")
        parsed = maintenance.parse_uploaded_backup(
            response.content, "test-backup-passphrase-1234"
        )
        self.assertEqual(parsed["format"], maintenance.BACKUP_FORMAT)
        self.assertTrue(maintenance.recent_backup_downloaded(self.client.session))

        after = self.client.get("/settings/backup/status/")
        self.assertEqual(after.status_code, 200)
        self.assertTrue(after.json()["recent"])
        self.assertGreater(after.json()["expires_in_seconds"], 0)

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


class MaintenanceDestructiveViewTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.admin = User.objects.create_superuser(
            "reset-admin", "reset-admin@example.test", "test-password-long-enough"
        )
        self.branch = Branch.objects.create(name="Main", code="main")
        self.admin.access.branches.add(self.branch)
        Company.objects.create(name="Business Before Reset", phone="+233241234567")
        Product.objects.create(
            name="Product To Clear",
            sku="RESET-ME",
            retail_unit="10",
            cost="5",
        )

    def _login(self):
        self.client.force_login(self.admin)
        self.admin.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.admin.access.session_version
        session["mfa_ok"] = True
        session["branch"] = self.branch.pk
        session.save()

    def test_reset_view_completes_after_safety_backup(self):
        self._login()
        backup = self.client.post("/settings/backup/download/", {
            "password": "test-password-long-enough",
            "backup_passphrase": "safety-backup-passphrase-1234",
            "backup_passphrase_confirm": "safety-backup-passphrase-1234",
        })
        self.assertEqual(backup.status_code, 200)

        response = self.client.post("/settings/backup/", {
            "action": "reset",
            "password": "test-password-long-enough",
            "confirmation": maintenance.RESET_CONFIRMATION,
            "understand": "yes",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login/?fresh_start=1")
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(User.objects.get(username="reset-admin").is_superuser)
        company = Company.objects.get()
        self.assertEqual(company.name, "KOFAD IMPEX ENTERPRISE")
        self.assertEqual(company.phone, "")

    def test_restore_view_round_trip_completes_after_safety_backup(self):
        self._login()
        original = maintenance.backup_bytes(self.admin)
        Company.objects.update(name="Changed After Backup", phone="+233209999999")
        Product.objects.create(
            name="Temporary Product",
            sku="TEMP-RESTORE",
            retail_unit="1",
            cost="1",
        )

        safety = self.client.post("/settings/backup/download/", {
            "password": "test-password-long-enough",
            "backup_passphrase": "safety-backup-passphrase-1234",
            "backup_passphrase_confirm": "safety-backup-passphrase-1234",
        })
        self.assertEqual(safety.status_code, 200)
        upload = SimpleUploadedFile(
            "original.kofad.json",
            original,
            content_type="application/json",
        )
        response = self.client.post("/settings/backup/", {
            "action": "restore",
            "password": "test-password-long-enough",
            "confirmation": maintenance.RESTORE_CONFIRMATION,
            "understand": "yes",
            "backup_file": upload,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login/?restored=1")
        self.assertFalse(Product.objects.filter(sku="TEMP-RESTORE").exists())
        company = Company.objects.get()
        self.assertEqual(company.name, "Business Before Reset")
        self.assertEqual(company.phone, "+233241234567")
