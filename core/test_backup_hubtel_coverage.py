"""Hubtel Direct financial state must survive a full backup and restore."""
import copy
import hashlib
import json
from collections import Counter
from decimal import Decimal
from django.utils import timezone

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TransactionTestCase

from core import maintenance
from core.models import Branch, Company, Party
from hubtel_direct.models import DirectTransaction, DebtMandate, DebtDebitAttempt


class HubtelFullBackupCoverageTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = User.objects.create_superuser(
            "kofad-backup-owner", "backup@example.test", "test-password-sufficiently-long"
        )
        self.branch = Branch.objects.create(name="Main", code="main")
        Company.objects.create()

    def receive(self, ref, amount="12.50"):
        return DirectTransaction.objects.create(
            client_reference=ref,
            kind="receive",
            amount=Decimal(amount),
            requested_by=self.owner,
            destination_encrypted="encrypted-provider-wallet-token-fixture",
            destination_suffix="0001",
            provider_status="Paid",
            status="paid",
        )

    def test_all_managed_hubtel_models_included_and_receipt_restored(self):
        managed = {
            m._meta.label_lower
            for m in apps.get_app_config("hubtel_direct").get_models()
            if maintenance._managed(m)
        }
        self.assertGreater(len(managed), 5)
        self.assertTrue(managed.issubset(maintenance.backup_model_labels()))

        original = self.receive("hubtel-backup-original-0001")
        customer = Party.objects.create(
            branch=self.branch, kind="customer", name="Mandate Test Customer", phone="+233240000001",
        )
        today = timezone.localdate()
        mandate = DebtMandate.objects.create(
            branch=self.branch, party=customer, created_by=self.owner,
            phone_encrypted="encrypted-wallet-fixture",
            phone_fingerprint="test-wallet-hash-0001",
            phone_suffix="0001", channel="mtn-gh",
            client_reference="hubtel-backup-mandate-0001",
            status="approved", frequency="weekly",
            first_due_date=today, next_due_date=today,
            amount_per_period=Decimal("5.00"), maximum_total=Decimal("25.00"),
        )
        attempt = DebtDebitAttempt.objects.create(
            mandate=mandate, reference="hubtel-backup-charge-0001",
            period_due_date=today, amount=Decimal("5.00"), status="planned",
        )
        bundle = maintenance.create_backup(self.owner)
        self.assertEqual(bundle["version"], 3)
        self.assertEqual(bundle["model_counts"]["hubtel_direct.directtransaction"], 1)
        self.assertEqual(bundle["model_counts"]["hubtel_direct.debtmandate"], 1)
        self.assertEqual(bundle["model_counts"]["hubtel_direct.debtdebitattempt"], 1)
        self.assertTrue(
            any(app == "hubtel_direct" for app, _name in bundle["migrations"])
        )
        self.receive("hubtel-backup-temporary-0002")
        self.assertEqual(DirectTransaction.objects.count(), 2)

        maintenance.restore_backup(bundle, actor_username=self.owner.username)
        self.assertEqual(DirectTransaction.objects.count(), 1)
        restored = DirectTransaction.objects.get(pk=original.pk)
        self.assertEqual(restored.client_reference, original.client_reference)
        self.assertEqual(restored.amount, Decimal("12.50"))
        self.assertEqual(restored.provider_status, "Paid")
        restored_mandate = DebtMandate.objects.get(pk=mandate.pk)
        self.assertEqual(restored_mandate.party.name, "Mandate Test Customer")
        # Post-restore state must not automatically authorize another wallet
        # debit based on an old snapshot of customer/provider state.
        self.assertEqual(restored_mandate.status, "suspended")
        self.assertEqual(restored_mandate.maximum_total, Decimal("25.00"))
        self.assertEqual(DebtDebitAttempt.objects.get(pk=attempt.pk).status, "finance_review")

    def test_legacy_archive_is_recognized_but_cannot_erase_live_hubtel_records(self):
        self.receive("hubtel-old-backup-0003")
        bundle = maintenance.create_backup(self.owner)
        legacy = copy.deepcopy(bundle)
        legacy["version"] = maintenance.LEGACY_BACKUP_VERSION
        legacy["migrations"] = maintenance.current_migrations(legacy=True)
        rows = [
            row for row in json.loads(legacy["fixture"])
            if not row["model"].startswith("hubtel_direct.")
        ]
        legacy["fixture"] = json.dumps(rows)
        legacy["fixture_sha256"] = hashlib.sha256(legacy["fixture"].encode()).hexdigest()
        legacy["record_count"] = len(rows)
        legacy["model_counts"] = dict(sorted(Counter(r["model"] for r in rows).items()))
        legacy["signature"] = maintenance._sign(legacy)
        self.assertEqual(maintenance.validate_backup(legacy), legacy)

        with self.assertRaisesMessage(maintenance.BackupError, "excludes Hubtel Direct"):
            maintenance.restore_backup(legacy, actor_username=self.owner.username)
        self.assertEqual(DirectTransaction.objects.count(), 1)
        self.assertTrue(Company.objects.exists())

        # Old v2 archives remain usable in an isolated clean recovery database.
        DirectTransaction.objects.all().delete()
        maintenance.restore_backup(legacy, actor_username=self.owner.username)
        self.assertEqual(DirectTransaction.objects.count(), 0)
        self.assertTrue(Company.objects.exists())

    def test_fresh_start_clears_hubtel_direct_state_together_with_business_data(self):
        self.receive("hubtel-reset-example-0004")
        self.assertEqual(DirectTransaction.objects.count(), 1)
        maintenance.reset_business_data(self.owner)
        self.assertEqual(DirectTransaction.objects.count(), 0)
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(Company.objects.exists())
