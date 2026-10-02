import os
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from .models import Access, Branch


class InitialAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("ADMIN", "", "ADMIN")
        Branch.objects.create(name="Main", code="main")
        Access.objects.filter(user=self.user).update(must_change_password=True)

    def test_initial_credentials_go_directly_to_dashboard(self):
        response = self.client.post("/login/", {"username": "ADMIN", "password": "ADMIN"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], "/")
        self.assertNotContains(response, "Private setup key")

    def test_username_case_and_outer_spaces_do_not_prevent_login(self):
        response = self.client.post("/login/", {"username": " admin ", "password": "ADMIN"}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/")

    def test_wrong_password_still_rejected(self):
        self.client.post("/login/", {"username": "admin", "password": "wrong"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_changed_password_still_opens_dashboard(self):
        Access.objects.filter(user=self.user).update(must_change_password=False)
        self.user.set_password("new-private-credential-example")
        self.user.save()
        response = self.client.post("/login/", {"username": "admin", "password": "new-private-credential-example"})
        self.assertEqual(response.url, "/")

    @patch("core.management.commands.initialize_deployment.call_command")
    @patch.dict(os.environ, {"KOFAD_INITIAL_ADMIN_PASSWORD": "ADMIN"})
    def test_existing_admin_is_never_reset_by_deployment(self, command):
        self.user.set_password("existing-private-password")
        self.user.save()
        call_command("initialize_deployment")
        command.assert_called_once_with("migrate", interactive=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("existing-private-password"))
