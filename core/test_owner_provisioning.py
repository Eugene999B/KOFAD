"""Deployment must never resurrect a disabled or demoted owner account."""
import os
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from core.models import Branch


class OwnerProvisioningSafetyTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Main", code="main")
        self.other_branch = Branch.objects.create(name="Warehouse", code="wh")
        self.variables = {
            "KOFAD_OWNER_ADMIN_NAME": "Example Owner",
            "KOFAD_OWNER_ADMIN_PHONE": "0249998877",
            "KOFAD_OWNER_ADMIN_INITIAL_PASSWORD": "initial-value-used-only-at-setup",
        }
        self.user = User.objects.create_superuser(
            "0249998877", password="a-separate-owner-password"
        )
        self.user.first_name = "Current"
        self.user.last_name = "Profile"
        self.user.save(update_fields=["first_name", "last_name"])
        self.user.access.recovery_phone = "+233241234567"
        self.user.access.save(update_fields=["recovery_phone"])
        self.user.access.branches.set([self.branch])

    def invoke(self):
        with patch.dict(os.environ, self.variables):
            call_command("provision_owner_admin", confirm_owner_admin=True)

    def test_existing_owner_name_phone_password_and_scopes_remain_untouched(self):
        self.invoke()
        self.user.refresh_from_db()
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), "Current Profile")
        self.assertTrue(self.user.check_password("a-separate-owner-password"))
        self.assertEqual(self.user.access.recovery_phone, "+233241234567")
        self.assertEqual(
            list(self.user.access.branches.values_list("id", flat=True)),
            [self.branch.pk],
        )
        self.assertTrue(self.user.is_superuser)

    def test_disabled_account_cannot_be_automatically_reactivated(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.invoke()
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_demoted_account_cannot_be_automatically_repromoted(self):
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])
        self.invoke()
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)

    def test_removed_staff_rights_cannot_be_automatically_restored(self):
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])
        self.invoke()
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
