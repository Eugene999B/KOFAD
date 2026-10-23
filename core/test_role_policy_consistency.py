"""Canonical role templates must be least-privilege and non-destructive."""
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase

from core.management.commands.bootstrap import ROLE_PERMISSIONS
from core.role_catalog import FRESH_START_ROLES, ROLE_PERMISSION_CODES


class RoleTemplateConsistencyTests(TestCase):
    def test_default_manager_cannot_edit_company_configuration(self):
        self.assertEqual(ROLE_PERMISSIONS, FRESH_START_ROLES)
        self.assertEqual(set(FRESH_START_ROLES["Owner"]), set(ROLE_PERMISSION_CODES))
        self.assertNotIn("manage_company", FRESH_START_ROLES["Manager"])
        self.assertIn("approve_operations", FRESH_START_ROLES["Manager"])

    def test_existing_group_grants_are_preserved_during_bootstrap(self):
        # Existing production grants are deliberately not reset during deploy.
        manager = Group.objects.create(name="Manager")
        custom = Permission.objects.get(
            content_type__app_label="core", codename="manage_company"
        )
        manager.permissions.add(custom)
        call_command("bootstrap")
        manager.refresh_from_db()
        self.assertEqual(set(manager.permissions.values_list("codename", flat=True)), {"manage_company"})

    def test_new_default_manager_uses_canonical_least_privilege_policy(self):
        call_command("bootstrap")
        manager = Group.objects.get(name="Manager")
        self.assertNotIn("manage_company", manager.permissions.values_list("codename", flat=True))
