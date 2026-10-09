from datetime import timedelta
from urllib.parse import urlparse

from django.contrib.auth.models import User
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

    @override_settings(SMS_ENABLED=False, KOFAD_EMAIL_ENABLED=False)
    def test_unavailable_delivery_methods_are_blocked(self):
        with self.assertRaises(ValidationError):
            staff_invites.validate_delivery("sms", "+233241234567")
        with self.assertRaises(ValidationError):
            staff_invites.validate_delivery("email", "person@example.com")
