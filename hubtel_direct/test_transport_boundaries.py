import os
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from marketplace import hubtel
from . import client, debit_api


@override_settings(
    HUBTEL_API_ID="offline-id", HUBTEL_API_KEY="offline-key",
    HUBTEL_COLLECTION_ACCOUNT="12345", HUBTEL_DIRECT_EGRESS_CONFIRMED=True,
    HUBTEL_DEBT_PREAPPROVAL_ENABLED=True,
)
class HubtelTransportBoundaryTests(SimpleTestCase):
    @patch.dict(os.environ, {"RAILWAY_SERVICE_NAME": "kofad-sms"})
    @patch("marketplace.hubtel.requests.get")
    @patch("marketplace.hubtel.requests.post")
    def test_hosted_payments_cannot_use_sms_worker_egress(self, post, get):
        with self.assertRaises(ValidationError):
            hubtel.initialize(None)
        with self.assertRaises(ValidationError):
            hubtel.verify("saved-reference")
        post.assert_not_called()
        get.assert_not_called()

    @patch.dict(os.environ, {"RAILWAY_SERVICE_NAME": "kofad-web"})
    def test_web_service_can_build_hosted_payment_headers(self):
        self.assertTrue(hubtel.headers()["Authorization"].startswith("Basic "))

    @patch("hubtel_direct.debit_api.requests.request")
    def test_transient_http_outcomes_never_release_the_original_intent(self, request):
        for status in (408, 409, 425, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                request.return_value = Mock(status_code=status)
                with self.assertRaises(client.StatusUnconfirmed):
                    debit_api._request("POST", "https://preapproval.hubtel.com/test", payload={})

    @patch("hubtel_direct.debit_api._request")
    def test_unknown_preapproval_codes_remain_unconfirmed(self, request):
        for code in (None, "", "9999"):
            with self.subTest(code=code):
                request.return_value = {"responseCode": code}
                with self.assertRaises(client.StatusUnconfirmed):
                    debit_api.initiate_preapproval(
                        phone="0241234567", channel="mtn-gh-direct-debit",
                        client_reference="a" * 32,
                    )

    @patch("hubtel_direct.debit_api._request")
    def test_documented_preapproval_refusals_remain_definitive(self, request):
        for code in ("4000", "4101", "4103", "4204"):
            with self.subTest(code=code):
                request.return_value = {"responseCode": code}
                with self.assertRaises(debit_api.ProviderDenied):
                    debit_api.initiate_preapproval(
                        phone="0241234567", channel="mtn-gh-direct-debit",
                        client_reference="a" * 32,
                    )
