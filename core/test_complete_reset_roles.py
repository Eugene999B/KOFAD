"""Fresh reset and backup/restore must cover every role and grant KOFAD exposes."""
from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from core import maintenance
from core.models import Branch, Company
from core.role_catalog import FRESH_START_ROLES, ROLE_PERMISSION_CODES, PERMISSION_HELP


def functional_grant(code):
    return Permission.objects.get(content_type__app_label="core", codename=code)


class CompleteResetRoleTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.admin = User.objects.create_superuser(
            "reset-role-admin", "admin@example.test", "safe-password-123456"
        )
        self.branch = Branch.objects.create(name="Main", code="main", active=True)
        Company.objects.create(name="KOFAD original business")

    def test_reset_clears_custom_roles_and_grants_and_recreates_every_default(self):
        custom = Group.objects.create(name="Temporary inventory supervisor")
        custom.permissions.add(functional_grant("operate_inventory"))
        employee = User.objects.create_user("employee-before-reset")
        employee.groups.add(custom)
        employee.user_permissions.add(functional_grant("view_reports"))
        employee.access.branches.add(self.branch)

        result = maintenance.reset_business_data(self.admin)
        self.assertEqual(
            set(Group.objects.values_list("name", flat=True)),
            set(FRESH_START_ROLES),
        )
        for name, codes in FRESH_START_ROLES.items():
            with self.subTest(role=name):
                actual = set(
                    Group.objects.get(name=name).permissions.values_list("codename", flat=True)
                )
                self.assertEqual(actual, set(codes))
        self.assertEqual(
            set(Group.objects.get(name="Owner").permissions.values_list("codename", flat=True)),
            set(ROLE_PERMISSION_CODES),
        )
        self.assertFalse(User.objects.filter(username="employee-before-reset").exists())
        self.assertFalse(Group.objects.filter(name="Temporary inventory supervisor").exists())
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_superuser)
        self.assertEqual(result["company_id"], Company.objects.get().pk)
        self.assertEqual(result["branch_id"], Branch.objects.get().pk)

    def test_signed_full_backup_restores_custom_roles_users_and_direct_grants(self):
        role = Group.objects.create(name="Custom audit assistant")
        role.permissions.add(functional_grant("view_reports"))
        employee = User.objects.create_user(
            "backup-staff", "backup-staff@example.test", "staff-original-password-123"
        )
        employee.groups.add(role)
        employee.user_permissions.add(functional_grant("add_party"))
        employee.access.branches.add(self.branch)
        source = maintenance.create_backup(self.admin)
        self.assertEqual(source["version"], maintenance.BACKUP_VERSION)
        self.assertGreaterEqual(source["model_counts"]["auth.group"], 1)
        self.assertGreaterEqual(source["model_counts"]["auth.permission"], len(ROLE_PERMISSION_CODES))

        role.permissions.clear()
        employee.user_permissions.clear()
        Group.objects.create(name="Temporary post-backup role")
        maintenance.restore_backup(source, actor_username=self.admin.username)

        restored = User.objects.get(username="backup-staff")
        self.assertEqual(
            set(restored.groups.values_list("name", flat=True)),
            {"Custom audit assistant"},
        )
        self.assertTrue(restored.has_perm("core.view_reports"))
        self.assertTrue(restored.has_perm("core.add_party"))
        self.assertTrue(restored.access.branches.filter(code="main").exists())
        self.assertFalse(Group.objects.filter(name="Temporary post-backup role").exists())


class RoleEditorCompletenessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            "role-admin", "admin@example.test", "safe-password-123456"
        )
        self.branch = Branch.objects.create(name="Main", code="main", active=True)
        Company.objects.create()
        self.owner.access.branches.add(self.branch)
        # Branch membership changes revoke older sessions. Refresh the
        # signal-incremented access version before creating this test session.
        self.owner.access.refresh_from_db()
        self.client.force_login(self.owner)
        session = self.client.session
        session["branch"] = self.branch.pk
        session["access_version"] = self.owner.access.session_version
        session["mfa_ok"] = True
        session["mfa_verified_at"] = timezone.now().timestamp()
        session.save()

    def test_role_editor_exposes_every_enforced_grant_exactly_once(self):
        response = self.client.get("/administration/roles/new/")
        self.assertEqual(response.status_code, 200)
        choices = response.context["choices"]
        self.assertEqual({c["permission"].codename for c in choices},
                         set(ROLE_PERMISSION_CODES))
        self.assertEqual(len(choices), len(ROLE_PERMISSION_CODES))
        self.assertEqual(len(PERMISSION_HELP), len(ROLE_PERMISSION_CODES))

        # Every exposed grant is actually persisted for a custom role.
        valid_ids = [
            str(functional_grant(code).pk) for code in ROLE_PERMISSION_CODES
        ]
        create = self.client.post("/administration/roles/new/", {
            "name": "Complete Business Operator", "permissions": valid_ids,
        })
        self.assertEqual(create.status_code, 302)
        self.assertEqual(
            set(Group.objects.get(name="Complete Business Operator")
                .permissions.values_list("codename", flat=True)),
            set(ROLE_PERMISSION_CODES),
        )

    def test_role_editor_rejects_malformed_or_unsupported_grants(self):
        report_id = str(functional_grant("view_reports").pk)
        for payload in (
            [report_id, "not-a-number"],
            [report_id, report_id],
            [report_id, "999999999999999999999999999999999999"],
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/administration/roles/new/", {
                    "name": "Bad Grant Role", "permissions": payload,
                })
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Group.objects.filter(name="Bad Grant Role").exists())
