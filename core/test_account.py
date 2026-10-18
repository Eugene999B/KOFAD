from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from .models import Access, Branch, PasswordRecovery
from .sms.providers import Submission


@override_settings(SMS_ENABLED=True, SMS_SANDBOX=False, ARKESEL_API_KEY="test-only", SMS_SENDER_ID="KOFAD")
class AccountRecoveryTests(TestCase):
    def setUp(self):
        Branch.objects.create(name="Main", code="main")
        self.user = User.objects.create_superuser("ADMIN", "", "ADMIN")
        Access.objects.filter(user=self.user).update(recovery_phone="+233241234567")
        self.provider_patch = patch("core.account.get_provider")
        self.provider = self.provider_patch.start().return_value
        self.addCleanup(self.provider_patch.stop)
        self.provider.submit.return_value = Submission("accepted", "test-id")

    def request_code(self, username="admin"):
        with patch("core.account.secrets.randbelow", return_value=123456):
            response = self.client.post("/forgot-password/", {"username":username})
        self.assertRedirects(response, "/forgot-password/code/")
        return PasswordRecovery.objects.filter(user=self.user).latest("created_at") if username == "admin" else None

    def reset(self, code="123456"):
        return self.client.post("/forgot-password/code/", {"code":code,
            "new_password1":"Changed-private-password-987!", "new_password2":"Changed-private-password-987!"})

    def test_direct_login_even_with_legacy_security_flags(self):
        Access.objects.filter(user=self.user).update(must_change_password=True, totp_secret="legacy-secret")
        response = self.client.post("/login/", {"username":"admin", "password":"ADMIN"}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/workspace/")
        self.assertContains(response, "Command centre")

    def test_code_is_hashed_sent_only_to_saved_number_and_consumed_once(self):
        challenge = self.request_code()
        self.assertNotEqual(challenge.code_digest, "123456")
        self.assertEqual(self.provider.submit.call_args.args[0], "+233241234567")
        self.assertIn("123456", self.provider.submit.call_args.args[1])
        old_version = self.user.access.session_version
        self.assertRedirects(self.reset(), "/login/")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Changed-private-password-987!"))
        self.user.access.refresh_from_db()
        self.assertGreater(self.user.access.session_version, old_version)
        challenge.refresh_from_db()
        self.assertTrue(challenge.used)
        session = self.client.session
        session["recovery_id"] = str(challenge.pk)
        session.save()
        self.assertContains(self.reset(), "invalid, expired or unavailable")

    def test_unknown_account_is_not_disclosed_and_never_sent(self):
        self.request_code("unknown")
        self.provider.submit.assert_not_called()
        self.assertContains(self.reset(), "invalid, expired or unavailable")

    def test_code_copied_with_sms_spacing_is_accepted(self):
        self.request_code()
        self.assertRedirects(self.reset(" 123  - 456 "), "/login/")

    def test_only_five_attempts_and_no_prefix_match(self):
        challenge = self.request_code()
        self.assertContains(self.reset("123456extra"), "invalid, expired or unavailable")
        for _ in range(4):
            self.reset("000000")
        self.assertContains(self.reset(), "invalid, expired or unavailable")
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 5)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("ADMIN"))

    def test_expired_code_rejected(self):
        challenge = self.request_code()
        challenge.expires_at = timezone.now()-timedelta(seconds=1)
        challenge.save()
        self.assertContains(self.reset(), "invalid, expired or unavailable")

    def test_other_browser_cannot_use_code(self):
        self.request_code()
        self.assertRedirects(Client().post("/forgot-password/code/", {"code":"123456"}), "/forgot-password/")

    def test_resend_invalidates_previous_challenge(self):
        first = self.request_code()
        PasswordRecovery.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(seconds=61))
        self.request_code()
        first.refresh_from_db()
        self.assertTrue(first.used)

    def test_three_requests_per_account_per_hour(self):
        for _ in range(4):
            self.request_code()
            PasswordRecovery.objects.filter(user=self.user).update(created_at=timezone.now() - timedelta(seconds=61))
        self.assertEqual(self.provider.submit.call_count, 3)

    def test_changed_phone_or_password_invalidates_code(self):
        self.request_code()
        Access.objects.filter(user=self.user).update(recovery_phone="+233241234568")
        self.assertContains(self.reset(), "invalid, expired or unavailable")
        Access.objects.filter(user=self.user).update(recovery_phone="+233241234567")
        self.user.set_password("Another-private-password-987!")
        self.user.save()
        self.assertContains(self.reset(), "invalid, expired or unavailable")

    def test_provider_failure_never_allows_password_reset(self):
        self.provider.submit.return_value = Submission("failed")
        self.request_code()
        self.assertContains(self.reset(), "invalid, expired or unavailable")

    @override_settings(SMS_ENABLED=False)
    def test_disabled_sms_has_clear_unavailable_state(self):
        response = self.client.post("/forgot-password/", {"username":"ADMIN"})
        self.assertContains(response, "SMS recovery is not available yet")
        self.provider.submit.assert_not_called()

    def test_phone_change_requires_current_password(self):
        self.client.post("/login/", {"username":"ADMIN","password":"ADMIN"})
        response = self.client.post("/account/", {"recovery_phone":"0241234568","current_password":"wrong"})
        self.assertContains(response, "Your current password is incorrect")
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.access.recovery_phone, "+233241234567")
        with patch("core.account.secrets.randbelow", return_value=123456):
            self.assertRedirects(self.client.post("/account/", {
                "recovery_phone":"0241234568","current_password":"ADMIN",
            }), "/account/")
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.access.recovery_phone, "+233241234567")
        self.assertEqual(self.provider.submit.call_args.args[0], "+233241234568")
        self.assertContains(self.client.get("/account/"), "Verify and link number")
        self.assertRedirects(self.client.post("/account/", {
            "action":"recovery_verify", "phone_code":"123456", "current_password":"ADMIN",
        }), "/account/")
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.access.recovery_phone, "+233241234568")

    def _login_staff_for_phone(self):
        self.client.force_login(self.user)
        self.user.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.user.access.session_version
        session.save()

    def _start_phone_link(self, phone="0241234568"):
        self._login_staff_for_phone()
        with patch("core.account.secrets.randbelow", return_value=123456):
            self.assertRedirects(self.client.post("/account/", {
                "action":"recovery", "recovery_phone":phone,
                "current_password":"ADMIN",
            }), "/account/")
        self.user.access.refresh_from_db()
        return self.client.session.get("staff_phone_link_verification")

    def test_phone_verification_wrong_code_and_retry_limit_leave_old_number(self):
        self.assertIsNotNone(self._start_phone_link())
        for _ in range(5):
            self.assertRedirects(self.client.post("/account/", {
                "action":"recovery_verify", "phone_code":"000000",
                "current_password":"ADMIN",
            }), "/account/")
        self.assertNotIn("staff_phone_link_verification", self.client.session)
        self.assertRedirects(self.client.post("/account/", {
            "action":"recovery_verify", "phone_code":"123456",
            "current_password":"ADMIN",
        }), "/account/")
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.access.recovery_phone, "+233241234567")

    def test_phone_verification_expires_and_cannot_cross_browser_sessions(self):
        challenge = self._start_phone_link()
        self.assertIsNotNone(challenge)
        other = Client()
        other.force_login(self.user)
        session = other.session
        session["access_version"] = self.user.access.session_version
        session.save()
        other.post("/account/", {
            "action":"recovery_verify", "phone_code":"123456",
            "current_password":"ADMIN",
        })
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.access.recovery_phone, "+233241234567")
        session = self.client.session
        challenge["expires"] = (timezone.now() - timedelta(seconds=1)).timestamp()
        session["staff_phone_link_verification"] = challenge
        session.save()
        self.client.post("/account/", {
            "action":"recovery_verify", "phone_code":"123456",
            "current_password":"ADMIN",
        })
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.access.recovery_phone, "+233241234567")

    def test_phone_verification_rejects_password_change_and_old_phone_change(self):
        self._start_phone_link()
        Access.objects.filter(user=self.user).update(recovery_phone="+233241234569")
        self.client.post("/account/", {
            "action":"recovery_verify", "phone_code":"123456",
            "current_password":"ADMIN",
        })
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.access.recovery_phone, "+233241234569")
        self._start_phone_link()
        self.user.set_password("Changed-password-without-session-reset-123!")
        self.user.save()
        self.client.post("/account/", {
            "action":"recovery_verify", "phone_code":"123456",
            "current_password":"ADMIN",
        })
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.access.recovery_phone, "+233241234569")

    def test_phone_link_accepts_provider_delivered_acknowledgement(self):
        self.provider.submit.return_value = Submission("delivered")
        self.assertIsNotNone(self._start_phone_link())

    def test_phone_link_provider_failure_cannot_be_bypassed_with_code(self):
        self.provider.submit.return_value = Submission("failed")
        self.assertIsNone(self._start_phone_link())
        self.client.post("/account/", {
            "action":"recovery_verify", "phone_code":"123456",
            "current_password":"ADMIN",
        })
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.access.recovery_phone, "+233241234567")

    @override_settings(SMS_ENABLED=False)
    def test_unavailable_sms_does_not_allow_unverified_link(self):
        self.assertIsNone(self._start_phone_link())
        self.assertEqual(self.user.access.recovery_phone, "+233241234567")
        self.assertRedirects(self.client.post("/account/", {
            "action":"recovery", "recovery_phone":"",
            "current_password":"ADMIN",
        }), "/account/")
        self.user.access.refresh_from_db()
        self.assertEqual(self.user.access.recovery_phone, "")

    def test_phone_link_limited_to_three_submissions_per_hour(self):
        self._login_staff_for_phone()
        with patch("core.account.secrets.randbelow", return_value=123456):
            for _ in range(4):
                self.client.post("/account/", {
                    "action":"recovery", "recovery_phone":"0241234568",
                    "current_password":"ADMIN",
                })
        self.assertEqual(self.provider.submit.call_count, 3)

    def test_phone_cannot_be_claimed_from_another_staff_account(self):
        other = User.objects.create_user("other-staff", password="another-password")
        Access.objects.filter(user=other).update(recovery_phone="+233241234568")
        self._login_staff_for_phone()
        response = self.client.post("/account/", {
            "action":"recovery", "recovery_phone":"0241234568",
            "current_password":"ADMIN",
        })
        self.assertContains(response, "already linked to another staff")
        self.provider.submit.assert_not_called()

    def test_authenticated_password_change_is_optional_and_keeps_current_session(self):
        self.client.post("/login/", {"username":"ADMIN","password":"ADMIN"})
        response = self.client.post("/account/password/", {"old_password":"ADMIN",
            "new_password1":"Changed-private-password-987!", "new_password2":"Changed-private-password-987!"})
        self.assertRedirects(response, "/account/")
        self.assertEqual(self.client.get("/workspace/").status_code, 200)

    def test_admin_phone_update_invalidates_existing_codes(self):
        challenge = self.request_code()
        self.client.post("/login/", {"username":"ADMIN","password":"ADMIN"})
        access = Access.objects.get(user=self.user)
        response = self.client.post(reverse("admin:core_access_change", args=[access.pk]), {
            "user":self.user.pk, "branches":[Branch.objects.get(code="main").pk],
            "recovery_phone":"0241234568", "_save":"Save"})
        self.assertEqual(response.status_code, 302)
        access.refresh_from_db()
        challenge.refresh_from_db()
        self.assertEqual(access.recovery_phone, "+233241234568")
        self.assertTrue(challenge.used)

    def test_uncertain_provider_response_does_not_reject_a_received_code(self):
        self.provider.submit.return_value = Submission("unknown")
        self.request_code()
        self.assertRedirects(self.reset(), "/login/")

    def test_double_request_reuses_challenge_without_sending_a_second_code(self):
        first = self.request_code()
        second = self.request_code()
        self.assertEqual(first.pk, second.pk)
        self.provider.submit.assert_called_once()
        self.assertRedirects(self.reset("123-456"), "/login/")
