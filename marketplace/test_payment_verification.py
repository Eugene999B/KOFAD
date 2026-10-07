import hashlib
import hmac
import json
from unittest.mock import Mock, patch

import requests
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from . import services


@override_settings(PAYSTACK_SECRET_KEY="test-provider-key")
class PaymentVerificationTests(SimpleTestCase):
    @patch("marketplace.services.requests.get")
    def test_verification_must_match_the_requested_reference(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {"status": True, "data": {"reference": "different", "status": "success"}}
        with self.assertRaises(ValidationError):
            services.verify_paystack("KOFAD-expected")

    @patch("marketplace.services.requests.get")
    def test_reference_cannot_change_provider_url(self, get):
        for reference in ("../other", "ref?query=1", "ref/path", ""):
            with self.assertRaises(ValidationError):
                services.verify_paystack(reference)
        get.assert_not_called()

    @patch("marketplace.services.requests.get")
    def test_incomplete_response_is_retryable(self, get):
        get.return_value = Mock(status_code=200)
        for body in ([], {"status": True, "data": []}, {"status": False}):
            get.return_value.json.return_value = body
            with self.assertRaises(services.PaymentVerificationUnavailable):
                services.verify_paystack("KOFAD-test")

    @patch("marketplace.services.requests.get", side_effect=requests.Timeout)
    def test_webhook_requests_retry_when_verification_is_unavailable(self, get):
        raw = json.dumps({"event": "charge.success", "data": {"reference": "KOFAD-test"}}).encode()
        signature = hmac.new(b"test-provider-key", raw, hashlib.sha512).hexdigest()
        response = self.client.post("/market/payments/paystack/webhook/", data=raw,
                                    content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)
        self.assertEqual(response.status_code, 503)

    def test_signed_malformed_event_is_rejected(self):
        raw = b"[]"
        signature = hmac.new(b"test-provider-key", raw, hashlib.sha512).hexdigest()
        response = self.client.post("/market/payments/paystack/webhook/", data=raw,
                                    content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)
        self.assertEqual(response.status_code, 400)
