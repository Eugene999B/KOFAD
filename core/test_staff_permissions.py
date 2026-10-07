from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase

from .models import Branch, Company


class StaffPermissionTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Main", code="main")
        Company.objects.create()
        self.owner = User.objects.create_superuser("owner", password="owner-test-password")
        self.role = Group.objects.create(name="Counter")
        self.sales = Permission.objects.get(codename="operate_sales")
        self.reports = Permission.objects.get(codename="view_reports")
        self.role.permissions.add(self.sales)
        self.staff = User.objects.create_user("counter", password="staff-test-password")
        self.staff.groups.add(self.role)
        self.staff.access.branches.add(self.branch)

    def login(self, user):
        user.access.refresh_from_db()
        self.client.force_login(user)
        session = self.client.session
        session["access_version"] = user.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    def payload(self, **changes):
        data = {"username": self.staff.username, "role": str(self.role.pk),
                "active": "on", "branches": [str(self.branch.pk)]}
        data.update(changes)
        return data

    def test_additional_permission_can_be_granted_and_removed(self):
        self.login(self.owner)
        before = self.staff.access.session_version
        url = f"/administration/users/{self.staff.pk}/"
        self.assertEqual(self.client.post(url, self.payload(extra_permissions=[str(self.reports.pk)])).status_code, 302)
        fresh = User.objects.get(pk=self.staff.pk)
        self.assertTrue(fresh.has_perm("core.view_reports"))
        self.assertTrue(fresh.has_perm("core.operate_sales"))
        self.assertGreater(fresh.access.session_version, before)
        self.assertEqual(self.client.post(url, self.payload()).status_code, 302)
        self.assertFalse(User.objects.get(pk=self.staff.pk).has_perm("core.view_reports"))

    def test_company_settings_permission_cannot_change_security(self):
        self.staff.user_permissions.add(Permission.objects.get(codename="manage_company"))
        self.login(self.staff)
        for url in (f"/administration/users/{self.owner.pk}/", "/administration/users/new/",
                    f"/administration/roles/{self.role.pk}/"):
            self.assertEqual(self.client.post(url, self.payload()).status_code, 403)

    def test_invalid_role_and_unlisted_permissions_do_not_change_access(self):
        self.login(self.owner)
        url = f"/administration/users/{self.staff.pk}/"
        for changes in ({"role": "invalid"}, {"extra_permissions": ["999999"]},
                        {"branches": ["999999"]}):
            self.assertEqual(self.client.post(url, self.payload(**changes)).status_code, 200)
            self.assertFalse(self.staff.user_permissions.exists())
            self.assertTrue(self.staff.groups.filter(pk=self.role.pk).exists())
