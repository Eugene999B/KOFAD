"""Hubtel success/failure status-check evidence is exact and provider-backed.

All network replies are synthetic offline fixtures; the real provider is
never called by this test module.
"""
import io
import json
import zipfile
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.core.exceptions import ValidationError
from django.test import override_settings

from marketplace.hubtel_evidence import decrypt_exchange, save_exchange
from marketplace.models import HubtelEvidence, MarketPaymentAttempt
from marketplace.tests import MarketFixtures
from . import client


@override_settings(
    HUBTEL_API_ID="test-api-id",
    HUBTEL_API_KEY="test-api-key",
    HUBTEL_COLLECTION_ACCOUNT="12345",
    HUBTEL_DIRECT_EGRESS_CONFIRMED=True,
)
class StatusEvidenceContractTests(MarketFixtures):
    def _provider_reply(self, *, reference="fixture-ref", transaction="Success", invoice="Success", amount=1.0):
        body = {
            "ResponseCode": "0000",
            "Data": [{
                "StartDate": "2026-10-08T07:00:45",
                "InvoiceStatus": invoice, "TransactionStatus": transaction,
                "TransactionId": "provider-fixture-id",
                "NetworkTransactionId": "network-fixture-id" if transaction == "Success" else "",
                "CheckoutId": "provider-fixture-id",
                "TransactionType": "RECEIVE-MONEY", "PaymentMethod": "MOBILE-MONEY",
                "ClientReference": reference, "CurrencyCode": "GHS",
                "TransactionAmount": amount, "Fee": 0.00,
                "MobileNumber": "233241234567",
                "ProviderResponseCode": "SUCCESSFUL" if transaction == "Success" else "TARGET_AUTHORIZATION_ERROR",
                "ProviderDescription": "Approved" if transaction == "Success" else "Insufficient funds",
            }],
        }
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        response = requests.Response()
        response.status_code = 200
        response._content = raw
        response.request = requests.Request(
            "GET", "https://api-txnstatus.hubtel.com/transactions/12345/status",
            params={"clientReference": reference},
        ).prepare()
        return response, raw

    def _tx(self, ref="fixture-ref", amount="1.00"):
        return SimpleNamespace(
            kind="receive", client_reference=ref, amount=Decimal(amount),
            hubtel_transaction_id="provider-fixture-id",
            channel="mtn-gh",
        )

    @patch("hubtel_direct.client.require_reconciliation_ready")
    @patch("hubtel_direct.client.requests.get")
    def test_success_response_data_array_is_verified_and_original_bytes_downloadable(self, get, _):
        response, raw = self._provider_reply()
        get.return_value = response
        status, tx_id = client.check(self._tx())
        self.assertEqual((status, tx_id), ("success", "provider-fixture-id"))
        original = HubtelEvidence.objects.get(reference="fixture-ref", direction="direct_status")
        self.assertEqual(decrypt_exchange(original), raw)
        url = HubtelEvidence.objects.get(reference="fixture-ref", direction="direct_check")
        self.assertIn(b"clientReference=fixture-ref", decrypt_exchange(url))
        self.assertNotIn(b"test-api-key", decrypt_exchange(url))

    @patch("hubtel_direct.client.require_reconciliation_ready")
    @patch("hubtel_direct.client.requests.get")
    def test_failed_response_data_array_is_terminal_failed_not_pending(self, get, _):
        response, raw = self._provider_reply(transaction="Failed", invoice="Failed", amount=2.0)
        get.return_value = response
        status, _id = client.check(self._tx(amount="2.00"))
        self.assertEqual(status, "failed")
        self.assertEqual(
            decrypt_exchange(HubtelEvidence.objects.get(direction="direct_status")),
            raw,
        )

    @patch("hubtel_direct.client.require_reconciliation_ready")
    @patch("hubtel_direct.client.requests.get")
    def test_reference_and_amount_mismatch_never_mark_paid(self, get, _):
        get.return_value, _ = self._provider_reply(reference="someone-else")
        with self.assertRaises(client.StatusUnconfirmed):
            client.check(self._tx())
        get.return_value, _ = self._provider_reply(amount=9.0)
        with self.assertRaises(ValidationError):
            client.check(self._tx())

    @patch("hubtel_direct.client.require_reconciliation_ready")
    @patch("hubtel_direct.client.requests.get")
    def test_conflicting_provider_final_states_fail_closed(self, get, _):
        get.return_value, _ = self._provider_reply(transaction="Success", invoice="Failed")
        with self.assertRaisesRegex(ValidationError, "Conflicting"):
            client.check(self._tx())

    def test_manager_can_download_original_callback_and_status_in_one_zip(self):
        order = self.order()
        ref = "fixture-bundle-ref"
        MarketPaymentAttempt.objects.create(
            order=order, provider="hubtel", reference=ref,
            amount=Decimal("2.00"), currency="GHS", status="failed",
        )
        original_callback = b'{"ResponseCode":"2001","Status":"Failed"}'
        original_status = b'{"ResponseCode":"0000","Data":[{"TransactionStatus":"Failed","ClientReference":"fixture-bundle-ref"}]}'
        save_exchange(reference=ref, direction="callback", raw_body=original_callback)
        save_exchange(reference=ref, direction="status_check", raw_body=original_status, http_status=200)
        # Exercise the owner-only view with an authenticated staff request.
        # The browser login + MFA lifecycle is tested separately; test
        # client logins do not create a real staff browser session.
        from django.test import RequestFactory
        from django.contrib.sessions.middleware import SessionMiddleware
        from .evidence_views import download_bundle
        request = RequestFactory().get(
            f"/settings/online-payments/hubtel-diagnostics/transaction-bundle/?reference={ref}"
        )
        request.user = self.staff
        SessionMiddleware(lambda req: None).process_request(request)
        request.session["branch"] = self.branch.pk
        response = download_bundle.__wrapped__.__wrapped__(request)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response["Content-Type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            self.assertTrue(any(name.startswith("callback-") for name in names))
            self.assertTrue(any(name.startswith("status_check-") for name in names))
            self.assertIn(original_callback, [archive.read(n) for n in names])
            self.assertIn(original_status, [archive.read(n) for n in names])
