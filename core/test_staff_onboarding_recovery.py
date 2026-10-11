"""Recovered staff onboarding from owner-created accounts.

All email, WhatsApp, and SMS delivery is mocked: tests never incur provider charges.
"""
from unittest.mock import patch
from urllib.parse import urlparse

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core import staff_invites
from core.models import Branch, StaffInvitation


class StaffOnboardingReleaseRecoveryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="staff-owner", password="VeryStrongOwnerPassword2026!",
        )
        self.branch = Branch.objects.create(name="Main", code="main", active=True)
        self.role = Group.objects.create(name="Cashier")
        self.client.force_login(self.owner)

    def payload(self, channel="sms"):
        return {
            "username": "new-cashier",
            "first_name": "New",
            "last_name": "Cashier",
            "recovery_phone": "0241234567",
            "email": "new.staff@example.com",
            "role": str(self.role.pk),
            "invite_channel": channel,
            "active": "on",
            "email_daily_closing": "on",
        }

    def test_create_form_offers_three_channels_and_no_initial_password(self):
        page = self.client.get("/administration/users/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="invite_channel"')
        self.assertContains(page, 'value="sms"')
        self.assertContains(page, 'value="email"')
        self.assertContains(page, 'value="whatsapp"')
        self.assertNotContains(page, "Initial password")

    @patch("core.staff_invites.deliver")
    @patch("core.staff_invites.validate_delivery", return_value="+233241234567")
    def test_creating_staff_requires_private_invitation_not_shared_password(self, validation, deliver):
        payload = self.payload()
        payload["password"] = "UnsafeAdminSuppliedPassword123!"  # must be ignored
        result = self.client.post("/administration/users/new/", payload)
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], "/administration/users/")
        staff = User.objects.get(username="new-cashier")
        self.assertFalse(staff.is_active)
        self.assertFalse(staff.has_usable_password())
        self.assertEqual(staff.email, "new.staff@example.com")
        self.assertTrue(staff.access.email_daily_closing)
        self.assertEqual(list(staff.groups.values_list("name", flat=True)), ["Cashier"])
        invitation = StaffInvitation.objects.get(user=staff)
        self.assertEqual(invitation.channel, "sms")
        self.assertEqual(invitation.destination, "+233241234567")
        self.assertEqual(deliver.call_count, 1)
        self.assertEqual(deliver.call_args.args[0].pk, invitation.pk)
        self.assertEqual(self.client.get("/administration/users/").status_code, 200)

    @patch("core.staff_invites.deliver")
    @patch("core.staff_invites.validate_delivery", return_value="new.staff@example.com")
    def test_email_invitation_uses_staff_email_and_remains_inactive(self, validation, deliver):
        response = self.client.post("/administration/users/new/", self.payload("email"))
        self.assertEqual(response.status_code, 302)
        staff = User.objects.get(username="new-cashier")
        self.assertFalse(staff.is_active)
        invitation = StaffInvitation.objects.get(user=staff)
        self.assertEqual(invitation.channel, "email")
        self.assertEqual(invitation.destination, staff.email)
        validation.assert_called_once()
        self.assertEqual(deliver.call_count, 1)

    @patch("core.staff_invites.deliver")
    @patch("core.staff_invites.validate_delivery", side_effect=ValidationError("WhatsApp consent required"))
    def test_failed_channel_validation_does_not_create_staff(self, validation, deliver):
        response = self.client.post("/administration/users/new/", self.payload("whatsapp"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="new-cashier").exists())
        deliver.assert_not_called()

    @patch("core.staff_invites.email_identity._send_kofad_mail", return_value=0)
    def test_unaccepted_email_is_not_marked_delivered(self, mail):
        staff = User.objects.create_user(username="pending", is_active=False)
        staff.set_unusable_password()
        staff.save(update_fields=["password"])
        invitation, url = staff_invites.issue(staff, self.owner, "email", "pending@example.com")
        with self.assertRaises(ValidationError):
            staff_invites.deliver(invitation, url)
        invitation.refresh_from_db()
        self.assertEqual(invitation.delivery_state, "failed")
        self.assertIsNone(invitation.delivered_at)
        staff.refresh_from_db()
        self.assertFalse(staff.is_active)

    @patch("core.staff_invites.deliver")
    @patch("core.staff_invites.validate_delivery", return_value="+233241234567")
    def test_staff_can_set_own_password_only_with_one_time_link(self, validation, deliver):
        self.client.post("/administration/users/new/", self.payload())
        invitation = StaffInvitation.objects.select_related("user").get(user__username="new-cashier")
        link = deliver.call_args.args[1]
        self.assertEqual(self.client.get(urlparse(link).path).status_code, 302)
        password = "ChosenByEmployee2026!Secure"
        response = self.client.post("/staff-invite/complete/", {
            "password": password, "password_confirm": password,
        })
        self.assertEqual(response.status_code, 302)
        invitation.refresh_from_db()
        staff = invitation.user
        staff.refresh_from_db()
        self.assertTrue(staff.is_active)
        self.assertTrue(staff.check_password(password))
        self.assertIsNotNone(invitation.consumed_at)
        self.assertEqual(self.client.get(urlparse(link).path).status_code, 410)
