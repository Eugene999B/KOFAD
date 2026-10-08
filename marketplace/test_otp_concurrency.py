from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings

from .models import OtpThrottle
from .services import send_otp, verify_otp


@override_settings(CUSTOMER_OTP_ENABLED=True)
class CustomerOtpConcurrencyTests(TransactionTestCase):
    @override_settings(CUSTOMER_OTP_GLOBAL_HOURLY_LIMIT=2)
    def test_global_budget_stops_rotating_phone_sms_abuse(self):
        phones = ["+233241234561", "+233241234562", "+233241234563"]
        with patch("marketplace.services._submit_customer_otp_sms") as submit:
            send_otp(phones[0], "register")
            send_otp(phones[1], "register")
            with self.assertRaisesMessage(ValidationError, "Too many verification-code requests"):
                send_otp(phones[2], "register")
        self.assertEqual(submit.call_count, 2)

    def test_simultaneous_requests_send_only_one_usable_code(self):
        phone = "+233241234567"
        OtpThrottle.objects.create(phone=phone, purpose="register")
        entered = Event()
        release = Event()

        def provider(*args):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("OTP test synchronization timed out")

        def request():
            close_old_connections()
            try:
                send_otp(phone)
                return "sent"
            except ValidationError:
                return "cooldown"
            finally:
                close_old_connections()

        with patch("marketplace.services._submit_customer_otp_sms", side_effect=provider) as submit:
            with patch("marketplace.services.secrets.randbelow", return_value=123):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(request)
                    self.assertTrue(entered.wait(10))
                    second = pool.submit(request)
                    release.set()
                    self.assertEqual(sorted([first.result(), second.result()]), ["cooldown", "sent"])
            submit.assert_called_once()
        self.assertEqual(verify_otp(phone, "000 123"), phone)
