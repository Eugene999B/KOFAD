"""Offline tests for HIBP password checking and existing password forms."""
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from core.password_validation import PwnedPasswordValidator
from marketplace.forms import (
    CustomerPasswordChangeForm,
    CustomerPasswordResetForm,
    CustomerRegistrationForm,
)


TEST_PASSWORD = "A-new-unique-test-password-2026!"


def fake_range_response(password, count):
    suffix = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()[5:]
    result = Mock()
    result.text = f"00112233445566778899AABBCCDDEEFF001:17\n{suffix}:{count}\n"
    return result


@override_settings(KOFAD_PWNED_PASSWORDS_ENABLED=True)
class PwnedPasswordValidatorTests(SimpleTestCase):
    @patch("core.password_validation.requests.get")
    def test_breached_password_rejected_without_sending_password_or_full_hash(self, get):
        get.return_value = fake_range_response(TEST_PASSWORD, 19)
        with self.assertRaises(ValidationError) as error:
            validate_password(TEST_PASSWORD)
        self.assertIn("password_compromised", error.exception.error_list[0].code)
        args, kwargs = get.call_args
        self.assertEqual(
            args[0],
            "https://api.pwnedpasswords.com/range/" +
            hashlib.sha1(TEST_PASSWORD.encode("utf-8")).hexdigest().upper()[:5],
        )
        self.assertNotIn(TEST_PASSWORD, str(get.call_args))
        self.assertNotIn(
            hashlib.sha1(TEST_PASSWORD.encode("utf-8")).hexdigest().upper(),
            str(get.call_args),
        )
        self.assertEqual(kwargs["headers"]["Add-Padding"], "true")
        self.assertIn("User-Agent", kwargs["headers"])
        self.assertEqual(kwargs["timeout"], (1.0, 2.0))

    @patch("core.password_validation.requests.get")
    def test_not_found_is_accepted(self, get):
        get.return_value = fake_range_response(TEST_PASSWORD + "another", 15)
        validate_password(TEST_PASSWORD)

    @patch("core.password_validation.requests.get")
    def test_zero_count_padding_does_not_block_password(self, get):
        get.return_value = fake_range_response(TEST_PASSWORD, 0)
        validate_password(TEST_PASSWORD)

    @patch("core.password_validation.requests.get")
    def test_network_timeout_does_not_block_local_validation(self, get):
        get.side_effect = requests.Timeout("mock upstream timeout")
        validate_password(TEST_PASSWORD)
        with self.assertRaises(ValidationError):
            validate_password("short")

    @patch("core.password_validation.requests.get")
    def test_customer_registration_reset_and_change_forms_enforce_screening(self, get):
        get.return_value = fake_range_response(TEST_PASSWORD, 3)
        registration = CustomerRegistrationForm(data={
            "full_name": "Test Customer",
            "password": TEST_PASSWORD,
            "password_confirm": TEST_PASSWORD,
        })
        reset = CustomerPasswordResetForm(data={
            "password": TEST_PASSWORD,
            "password_confirm": TEST_PASSWORD,
        })
        customer = SimpleNamespace(
            password_hash=make_password("Existing-customer-password-2025!"),
            check_password=lambda password: password == "Existing-customer-password-2025!",
        )
        change = CustomerPasswordChangeForm(
            data={
                "current_password": "Existing-customer-password-2025!",
                "password": TEST_PASSWORD,
                "password_confirm": TEST_PASSWORD,
            },
            customer=customer,
        )
        for form in (registration, reset, change):
            with self.subTest(form=type(form).__name__):
                self.assertFalse(form.is_valid())
                self.assertIn("password", form.errors)

    @patch("core.password_validation.requests.get")
    def test_staff_password_change_and_reset_forms_enforce_screening(self, get):
        get.return_value = fake_range_response(TEST_PASSWORD, 5)
        user = User(username="test-staff")
        user.set_password("Existing-staff-password-2025!")
        reset = SetPasswordForm(user=user, data={
            "new_password1": TEST_PASSWORD,
            "new_password2": TEST_PASSWORD,
        })
        change = PasswordChangeForm(user=user, data={
            "old_password": "Existing-staff-password-2025!",
            "new_password1": TEST_PASSWORD,
            "new_password2": TEST_PASSWORD,
        })
        for form in (reset, change):
            with self.subTest(form=type(form).__name__):
                self.assertFalse(form.is_valid())
                self.assertIn("new_password2", form.errors)

    @override_settings(KOFAD_PWNED_PASSWORDS_ENABLED=False)
    @patch("core.password_validation.requests.get")
    def test_disabled_mode_does_not_make_network_requests(self, get):
        PwnedPasswordValidator().validate(TEST_PASSWORD)
        get.assert_not_called()
