from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings
from .paystack_challenges import charge_step


@override_settings(PAYSTACK_SECRET_KEY="test-key", PAYSTACK_TIMEOUT_SECONDS=5)
class ChargeChallengeTests(SimpleTestCase):
    @patch("core.paystack_challenges.requests.post")
    def test_code_submitted_only_to_fixed_provider_endpoint(self, post):
        post.return_value = Mock(status_code=200, json=lambda: {"status": True,
            "data": {"status": "send_otp", "reference": "KFD-TEST-1"}})
        charge_step("KFD-TEST-1", otp="123456")
        self.assertEqual(post.call_args.args[0], "https://api.paystack.co/charge/submit_otp")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    @patch("core.paystack_challenges.requests.post")
    def test_bad_code_or_reference_does_not_reach_provider(self, post):
        for reference, code in [("KFD-TEST-1", "PIN"), ("../invalid", "123456")]:
            with self.assertRaises(ValidationError):
                charge_step(reference, otp=code)
        post.assert_not_called()

    @patch("core.paystack_challenges.requests.post")
    def test_wrong_reference_cannot_advance_challenge(self, post):
        post.return_value = Mock(status_code=200, json=lambda: {"status": True,
            "data": {"status": "success", "reference": "WRONG"}})
        with self.assertRaises(ValidationError):
            charge_step("KFD-TEST-1", otp="123456")
