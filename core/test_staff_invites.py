from datetime import timedelta
from importlib import import_module
from unittest.mock import patch
from urllib.parse import urlparse

from django.apps import apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core import staff_invites
from core.models import StaffInvitation


class StaffInvitationSecurityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="invitation-owner", password="VeryStrongOwnerPassword2026!"
        )
        self.newcomer = User.objects.create_user(username="new-staff", is_active=False)
        self.newcomer.set_unusable_password()
        self.newcomer.save(update_fields=["password"])

    def invite(self):
        invite, url = staff_invites.issue(
            self.newcomer, self.owner, "sms", "+233241234567"
        )
        self.assertEqual(len(invite.token_digest), 64)
        self.assertNotIn(invite.token_digest, url)
        return invite, urlparse(url).path

    def test_one_time_activation_requires_valid_token_and_never_shares_password(self):
        invite, url = self.invite()
        self.assertFalse(self.newcomer.is_active)
        self.assertFalse(self.newcomer.has_usable_password())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/staff-invite/complete/")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(self.client.get(response["Location"]).status_code, 200)
        password = "PrivateNewStaffPassword2026!3905"
        complete = self.client.post(response["Location"], {
            "password": password, "password_confirm": password,
        })
        self.assertEqual(complete.status_code, 302)
        self.newcomer.refresh_from_db()
        invite.refresh_from_db()
        self.assertTrue(self.newcomer.is_active)
        self.assertTrue(self.newcomer.check_password(password))
        self.assertIsNotNone(invite.consumed_at)
        self.assertEqual(self.client.get(url).status_code, 410)

    def test_new_staff_invitation_expires_after_exactly_one_hour(self):
        start = timezone.now()
        invitation, url = self.invite()
        self.assertLess(abs((invitation.expires_at - start).total_seconds() - 3600), 10)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.get("/staff-invite/complete/").status_code, 200)
        self.assertEqual(self.client.get(url + "incorrect").status_code, 410)
        self.assertEqual(self.client.get("/staff-invite/complete/").status_code, 410)

    def test_opened_invitation_cannot_finish_at_one_hour_deadline(self):
        invitation, url = self.invite()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.get("/staff-invite/complete/").status_code, 200)
        # Expiry is checked again at submission, not just when a link is clicked.
        StaffInvitation.objects.filter(pk=invitation.pk).update(expires_at=timezone.now())
        password = "NewStaffPrivatePasswordPass27!"
        denied = self.client.post("/staff-invite/complete/", {
            "password": password, "password_confirm": password,
        })
        self.assertEqual(denied.status_code, 410)
        self.assertEqual(self.client.get(url).status_code, 410)
        self.newcomer.refresh_from_db()
        self.assertFalse(self.newcomer.is_active)
        self.assertFalse(self.newcomer.has_usable_password())

    def test_link_consumed_once_even_with_second_browser_and_replay(self):
        invitation, url = self.invite()
        first_browser = self.client
        second_browser = Client()
        self.assertEqual(first_browser.get(url).status_code, 302)
        self.assertEqual(second_browser.get(url).status_code, 302)
        password = "NewStaffPrivatePasswordPass27!"
        successful = first_browser.post("/staff-invite/complete/", {
            "password": password, "password_confirm": password,
        })
        self.assertEqual(successful.status_code, 302)
        failed = second_browser.post("/staff-invite/complete/", {
            "password": "AnotherPrivatePasswordPass27!",
            "password_confirm": "AnotherPrivatePasswordPass27!",
        })
        self.assertEqual(failed.status_code, 410)
        self.assertEqual(first_browser.get(url).status_code, 410)
        self.assertEqual(second_browser.get(url).status_code, 410)
        invitation.refresh_from_db()
        self.newcomer.refresh_from_db()
        self.assertIsNotNone(invitation.consumed_at)
        self.assertTrue(self.newcomer.check_password(password))
        self.assertFalse(self.newcomer.check_password("AnotherPrivatePasswordPass27!"))

    def test_renewal_invalidates_previously_opened_activation_form(self):
        first, old_url = self.invite()
        self.assertEqual(self.client.get(old_url).status_code, 302)
        self.assertEqual(self.client.get("/staff-invite/complete/").status_code, 200)
        renewed, new_url = self.invite()
        self.assertEqual(first.pk, renewed.pk)
        password = "NewStaffPrivatePasswordPass27!"
        self.assertEqual(self.client.post("/staff-invite/complete/", {
            "password": password, "password_confirm": password,
        }).status_code, 410)
        self.assertEqual(self.client.get(old_url).status_code, 410)
        self.assertEqual(self.client.get(new_url).status_code, 302)

    def test_prior_24h_invites_are_shrunk_by_deployment_migration(self):
        invitation, old_url = self.invite()
        fake_legacy_expiry = timezone.now() + timedelta(hours=24)
        StaffInvitation.objects.filter(pk=invitation.pk).update(expires_at=fake_legacy_expiry)
        migration = import_module("core.migrations.0038_shorten_staff_invitation_expiry")
        migration.shorten_legacy_invites(apps, connection.schema_editor())
        invitation.refresh_from_db()
        self.assertLess(abs((invitation.expires_at - fake_legacy_expiry + timedelta(hours=23)).total_seconds()), 1)
        # Even an already opened old link cannot be activated after its now-shortened deadline.
        StaffInvitation.objects.filter(pk=invitation.pk).update(
            expires_at=timezone.now() - timedelta(seconds=10),
        )
        self.assertEqual(self.client.get(old_url).status_code, 410)

    @patch("core.staff_invites.get_provider")
    def test_sms_instructions_state_one_hour(self, provider_factory):
        invitation, url = staff_invites.issue(
            self.newcomer, self.owner, "sms", "+233241234567"
        )
        provider_factory.return_value.submit.return_value.status = "accepted"
        staff_invites.deliver(invitation, url)
        body = provider_factory.return_value.submit.call_args.args[1]
        self.assertIn("expires in 1 hour", body)
        self.assertNotIn("24 hours", body)

    def test_expired_or_invalid_invitation_cannot_activate(self):
        invite, url = self.invite()
        StaffInvitation.objects.filter(pk=invite.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(self.client.get(url).status_code, 410)
        self.assertFalse(self.newcomer.is_active)

    def test_regenerated_link_rejects_old_token(self):
        _, old_url = self.invite()
        renewed, new_url = self.invite()
        self.assertEqual(self.client.get(old_url).status_code, 410)
        self.assertEqual(self.client.get(new_url).status_code, 302)
        self.assertEqual(renewed.user_id, self.newcomer.pk)

    def test_activation_page_has_isolated_css_and_secure_form(self):
        _, url = self.invite()
        self.assertEqual(self.client.get(url).status_code, 302)
        response = self.client.get("/staff-invite/complete/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "staff-activation")
        self.assertContains(response, "staff-activation")
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'name="password_confirm"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'autocomplete="new-password"')
        self.assertNotContains(response, 'app.css')

    def test_expired_invitation_uses_the_same_isolated_layout(self):
        response = self.client.get("/staff-invite/complete/")
        self.assertEqual(response.status_code, 410)
        self.assertContains(response, "staff-activation", status_code=410)
        self.assertContains(response, "Back to staff sign in", status_code=410)
        self.assertNotContains(response, 'app.css', status_code=410)

    @override_settings(SMS_ENABLED=False, KOFAD_EMAIL_ENABLED=False)
    def test_unavailable_delivery_methods_are_blocked(self):
        with self.assertRaises(ValidationError):
            staff_invites.validate_delivery("sms", "+233241234567")
        with self.assertRaises(ValidationError):
            staff_invites.validate_delivery("email", "person@example.com")
