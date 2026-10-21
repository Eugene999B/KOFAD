"""Regression coverage for signed-out staff and Market deep links.

These requests run only against Django's isolated test database. They must
never initiate a payment, customer notification, or provider request.
"""
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit

from django.test import TestCase
from django.urls import reverse


class SignedOutRouteGuardTests(TestCase):
    def test_market_payment_verification_requires_sign_in_without_provider_calls(self):
        path = reverse("market_payment_verification", kwargs={"pk": uuid.uuid4()})
        for suffix in ("", "?format=json"):
            with self.subTest(suffix=suffix), (
                patch("requests.get", side_effect=AssertionError("Provider GET must not run")),
                patch("requests.post", side_effect=AssertionError("Provider POST must not run")),
            ):
                requested = path + suffix
                response = self.client.get(requested)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], reverse("market_access"))
                self.assertEqual(self.client.session["market_after_login"], requested)

    def test_sensitive_market_pages_require_customer_session(self):
        for name in ("market_account", "market_orders", "market_checkout", "market_messages"):
            with self.subTest(route=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], reverse("market_access"))

    def test_sensitive_staff_pages_require_staff_login(self):
        for name in ("dashboard", "pos", "inventory", "staff_online_orders", "staff_market_inbox"):
            with self.subTest(route=name):
                path = reverse(name)
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(urlsplit(response["Location"]).path, reverse("login"))
