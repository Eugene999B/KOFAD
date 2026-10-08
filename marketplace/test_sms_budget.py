"""SMS cost protection for KOFAD Market sign-in and onboarding."""
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import LoginAttempt
from marketplace import services
from marketplace.models import OtpThrottle


@override_settings(
    CUSTOMER_OTP_ENABLED=True,
    CUSTOMER_OTP_GLOBAL_HOURLY_LIMIT=1000,
    CUSTOMER_OTP_SESSION_HOURLY_LIMIT=1000,
)
class CustomerSmsBudgetTests(TestCase):
    @patch("marketplace.services._submit_customer_otp_sms")
    def test_two_codes_total_per_phone_across_purposes_and_sessions(self, sms):
        phone = "+233245555010"
        services.send_otp(phone, "register")
        services.send_otp(phone, "reset")
        with self.assertRaisesMessage(ValidationError, "Two verification SMS"):
            services.send_otp(phone, "change_phone")
        self.assertEqual(sms.call_count, 2)
        # An attempted third request may leave a throttle record, but cannot
        # send a third SMS or produce any usable verification code.
        self.assertEqual(OtpThrottle.objects.filter(phone=phone).count(), 3)
        self.assertFalse(OtpThrottle.objects.get(phone=phone, purpose="change_phone").code_digest)

    @patch("marketplace.services._submit_customer_otp_sms")
    def test_allowance_returns_one_hour_after_first_send(self, sms):
        phone = "+233245555011"
        services.send_otp(phone, "register")
        services.send_otp(phone, "reset")
        self.assertEqual(sms.call_count, 2)
        budget = LoginAttempt.objects.get(
            key=__import__("hashlib").sha256(
                ("customer-otp-phone-v2:" + phone).encode()
            ).hexdigest()
        )
        budget.blocked_until = timezone.now() - timedelta(seconds=1)
        budget.save(update_fields=["blocked_until"])
        services.send_otp(phone, "change_phone")
        self.assertEqual(sms.call_count, 3)
