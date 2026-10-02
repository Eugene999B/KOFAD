import hashlib
import os
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from .models import Access


@override_settings(KOFAD_SETUP_KEY="private-test-setup-key-at-least-32-characters")
class SetupGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("ADMIN", "", "ADMIN")
        Access.objects.filter(user=self.user).update(must_change_password=True)

    def test_temporary_password_alone_cannot_sign_in(self):
        response = self.client.post("/login/", {"username": "ADMIN", "password": "ADMIN"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_non_ascii_setup_key_is_rejected_without_server_error(self):
        response = self.client.post("/login/", {"username": "ADMIN", "password": "ADMIN", "setup_key": "🔐"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_correct_setup_key_unlocks_password_change_only(self):
        response = self.client.post("/login/", {"username": "ADMIN", "password": "ADMIN",
            "setup_key": "private-test-setup-key-at-least-32-characters"})
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(self.client.get("/"), "/account/password/", fetch_redirect_response=False)

    def test_other_authentication_paths_cannot_bypass_setup(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["access_version"] = self.user.access.session_version
        session["mfa_ok"] = True
        session.save()
        self.assertRedirects(self.client.get("/account/password/"), "/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_setup_key_not_needed_after_password_replacement(self):
        Access.objects.filter(user=self.user).update(must_change_password=False)
        self.user.set_password("new-private-credential-example")
        self.user.save()
        response = self.client.post("/login/", {"username": "ADMIN", "password": "new-private-credential-example"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/mfa/")

    def test_key_rotation_invalidates_initial_setup_session(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["access_version"] = self.user.access.session_version
        session["setup_key_digest"] = hashlib.sha256(b"old-key").hexdigest()
        session.save()
        self.assertRedirects(self.client.get("/account/password/"), "/login/", fetch_redirect_response=False)


class DeploymentBootstrapTests(TestCase):
    @patch("core.management.commands.initialize_deployment.call_command")
    @patch.dict(os.environ, {"KOFAD_INITIAL_ADMIN_PASSWORD": "ADMIN"})
    @override_settings(KOFAD_SETUP_KEY="")
    def test_bootstrap_requires_private_setup_key(self, command):
        with self.assertRaises(CommandError):
            call_command("initialize_deployment")
        command.assert_called_once_with("migrate", interactive=False)

    @patch("core.management.commands.initialize_deployment.call_command")
    @patch.dict(os.environ, {"KOFAD_INITIAL_ADMIN_PASSWORD": "ADMIN"})
    @override_settings(KOFAD_SETUP_KEY="private-test-setup-key-at-least-32-characters")
    def test_existing_admin_is_never_reset_by_deployment(self, command):
        user = User.objects.create_superuser("ADMIN", "", "existing-private-password")
        call_command("initialize_deployment")
        command.assert_called_once_with("migrate", interactive=False)
        user.refresh_from_db()
        self.assertTrue(user.check_password("existing-private-password"))
