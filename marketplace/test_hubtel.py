import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from . import hubtel, services
from .models import MarketPaymentAttempt, HubtelEvidence, PaymentConfiguration
from .tests import MarketFixtures


@override_settings(HUBTEL_API_ID="test-id", HUBTEL_API_KEY="test-key", HUBTEL_COLLECTION_ACCOUNT="12345")
class HubtelContractTests(TestCase):
    @patch("marketplace.hubtel.requests.get")
    def test_public_status_endpoint_and_basic_auth(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {
            "responseCode": "0000", "data": {"clientReference": "ref1", "status": "Paid"}
        }
        self.assertEqual(hubtel.verify("ref1")["status"], "Paid")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://rmsc.hubtel.com/v1/merchantaccount/merchants/12345/transactions/status")
        self.assertEqual(kwargs["params"], {"clientReference": "ref1"})
        self.assertTrue(kwargs["headers"]["Authorization"].startswith("Basic "))
        self.assertFalse(kwargs["allow_redirects"])

    @patch("marketplace.hubtel.requests.get")
    def test_public_pascal_case_response(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {"ResponseCode": "0000", "Data": {
            "ClientReference": "ref1", "Status": "Paid", "Amount": 200,
            "TransactionId": "txn1", "CurrencyCode": "GHS",
        }}
        result = hubtel.verify("ref1")
        self.assertEqual(result["clientReference"], "ref1")
        self.assertEqual(result["amount"], 200)

    @patch("marketplace.hubtel.requests.get")
    def test_mismatched_and_unknown_responses_fail_closed(self, get):
        get.return_value = Mock(status_code=200)
        for body in ([], {}, {"responseCode": "0000", "data": []},
                     {"responseCode": "0000", "data": {"clientReference": "wrong", "status": "Paid"}},
                     {"responseCode": "0000", "data": {"clientReference": "ref1", "status": "Success"}}):
            get.return_value.json.return_value = body
            with self.assertRaises(services.PaymentVerificationUnavailable):
                hubtel.verify("ref1")

    @patch("marketplace.hubtel.requests.get")
    def test_status_whitespace_and_case_are_normalised_without_accepting_generic_success(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {
            "ResponseCode": "0000", "Data": {
                "ClientReference": "ref1", "Status": " paid ",
                "Amount": "1.00", "TransactionId": "txn1",
            }
        }
        self.assertEqual(hubtel.verify("ref1")["status"], "Paid")

    @patch("marketplace.hubtel.requests.get")
    def test_generic_success_is_not_treated_as_paid(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {
            "ResponseCode": "0000", "Data": {
                "ClientReference": "ref1", "Status": "Success",
                "Amount": "1.00", "TransactionId": "txn1",
            }
        }
        with self.assertRaises(services.PaymentVerificationUnavailable):
            hubtel.verify("ref1")

    @patch("marketplace.hubtel.requests.get")
    def test_public_status_list_selects_exact_client_reference(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {
            "ResponseCode": "0000",
            "Data": [
                {"ClientReference": "other", "Status": "Paid", "Amount": 9, "TransactionId": "txn-other"},
                {"ClientReference": "ref1", "Status": "Paid", "Amount": 200, "TransactionId": "txn1"},
            ],
        }
        result = hubtel.verify("ref1")
        self.assertEqual(result["status"], "Paid")
        self.assertEqual(result["transactionId"], "txn1")

    @patch("marketplace.hubtel.requests.get")
    def test_live_sales_api_shape_maps_success_to_paid(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {
            "ResponseCode": "0000",
            "Data": [{
                "ClientReference": "ref1",
                "InvoiceStatus": "Success",
                "TransactionStatus": "Success",
                "TransactionId": "txn-live-1",
                "TransactionAmount": 1.00,
                "CurrencyCode": "GHS",
                "PaymentMethod": "MOBILE-MONEY",
            }],
        }
        result = hubtel.verify("ref1")
        self.assertEqual(result["status"], "Paid")
        self.assertEqual(result["amount"], 1.00)
        self.assertEqual(result["transactionId"], "txn-live-1")

    @patch("marketplace.hubtel.requests.get")
    def test_live_sales_api_shape_does_not_settle_partial_or_nonfinal_status(self, get):
        get.return_value = Mock(status_code=200)
        for invoice_status, transaction_status in [
            ("Pending", "Pending"),
            ("Success", "Pending"),
            ("Pending", "Success"),
        ]:
            get.return_value.json.return_value = {
                "ResponseCode": "0000",
                "Data": [{
                    "ClientReference": "ref1",
                    "InvoiceStatus": invoice_status,
                    "TransactionStatus": transaction_status,
                    "TransactionId": "txn-live-1",
                    "TransactionAmount": 1.00,
                    "CurrencyCode": "GHS",
                }],
            }
            with self.assertRaises(services.PaymentVerificationUnavailable):
                hubtel.verify("ref1")

    @patch("marketplace.hubtel.requests.get")
    def test_reference_cannot_change_status_url(self, get):
        for reference in ("../path", "x?y=1", "", "x" * 33):
            with self.assertRaises(ValidationError):
                hubtel.verify(reference)
        get.assert_not_called()


@override_settings(HUBTEL_API_ID="test-id", HUBTEL_API_KEY="test-key",
                   HUBTEL_COLLECTION_ACCOUNT="12345", HUBTEL_CHECKOUT_ENABLED=True)
class HubtelPaymentTests(MarketFixtures):
    def pending(self):
        order = self.order()
        return MarketPaymentAttempt.objects.create(
            order=order, provider="hubtel", reference="a" * 32, amount=order.total,
            status="pending", next_check_at=timezone.now() - timedelta(seconds=1)
        )

    @patch("marketplace.hubtel.requests.post")
    def test_initialization_is_reused_and_has_callback(self, post):
        def response(url, **kwargs):
            payload = kwargs["json"]
            self.assertEqual(len(payload["clientReference"]), 32)
            self.assertEqual(payload["totalAmount"], 200.0)
            self.assertIn("/market/payments/hubtel/callback/", payload["callbackUrl"])
            result = Mock(status_code=200)
            result.json.return_value = {"responseCode": "0000", "data": {
                "clientReference": payload["clientReference"], "checkoutId": "checkout1",
                "checkoutUrl": "https://pay.hubtel.com/checkout1",
            }}
            return result
        post.side_effect = response
        order = self.order()
        first = hubtel.initialize(order)
        second = hubtel.initialize(order)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(post.call_count, 1)

    @patch("marketplace.hubtel.requests.post", side_effect=requests.Timeout)
    def test_lost_response_does_not_create_second_attempt(self, post):
        order = self.order()
        for _ in range(2):
            with self.assertRaises(ValidationError):
                hubtel.initialize(order)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(order.payment_attempts.count(), 1)
        self.assertEqual(order.payment_attempts.get().status, "submission_unknown")

    @patch("marketplace.hubtel.verify")
    def test_paid_verification_posts_once(self, verify):
        attempt = self.pending()
        verify.return_value = {
            "status": "Paid", "clientReference": attempt.reference, "amount": Decimal("200.00"),
            "currencyCode": None, "transactionId": "hubtel-txn-1", "paymentMethod": "mobilemoney",
        }
        with patch("marketplace.notifications.queue_order_sms") as notify:
            first = hubtel.reconcile(attempt.reference)
            second = hubtel.reconcile(attempt.reference)
        self.assertEqual(first.sale_document_id, second.sale_document_id)
        self.assertEqual(first.payment_status, "paid")
        self.assertEqual(first.payment_channel, "mobile_money")
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(notify.call_count, 1)

    @patch("marketplace.hubtel.verify")
    def test_wrong_amount_never_posts_or_sends_receipt(self, verify):
        attempt = self.pending()
        verify.return_value = {
            "status": "Paid", "amount": 1, "currencyCode": "GHS", "transactionId": "txn",
            "clientReference": attempt.reference,
        }
        with self.assertRaises(ValidationError):
            hubtel.reconcile(attempt.reference)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "attention")
        self.assertIsNone(attempt.order.sale_document_id)
        self.assertNotEqual(attempt.order.payment_status, "paid")

    @patch("marketplace.hubtel.verify")
    def test_unpaid_is_not_failed_or_paid(self, verify):
        attempt = self.pending()
        verify.return_value = {"status": "Unpaid", "clientReference": attempt.reference}
        with self.assertRaises(services.PaymentVerificationUnavailable):
            hubtel.reconcile(attempt.reference)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "pending")
        self.assertIsNone(attempt.order.sale_document_id)

    def test_forged_callback_does_not_confirm_payment(self):
        attempt = self.pending()
        response = self.client.post("/market/payments/hubtel/callback/", {
            "ResponseCode": "0000", "Data": {"ClientReference": attempt.reference, "Status": "Success", "Amount": 200}
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        attempt.order.refresh_from_db()
        self.assertNotEqual(attempt.order.payment_status, "paid")
        self.assertIsNone(attempt.order.sale_document_id)
        event = attempt.order.events.get(status="hubtel_callback")
        self.assertFalse(event.customer_visible)
        self.assertIn("unverified", event.title)

    def test_callback_requeues_verification_even_after_an_earlier_check(self):
        attempt = self.pending()
        attempt.check_count = 4
        attempt.next_check_at = timezone.now() + timedelta(minutes=5)
        attempt.save(update_fields=["check_count", "next_check_at"])
        response = self.client.post("/market/payments/hubtel/callback/", {
            "responseCode": "0000", "data": {
                "clientReference": attempt.reference, "status": "Paid", "amount": 200
            }
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        attempt.refresh_from_db()
        self.assertLessEqual(attempt.next_check_at, timezone.now())
        self.assertIn("verification queued", attempt.provider_message)

    def test_retry_schedule_stays_fast_while_customer_may_be_waiting(self):
        attempt = self.pending()
        attempt.created_at = timezone.now() - timedelta(minutes=2)
        self.assertEqual(hubtel._retry_delay(attempt), timedelta(seconds=40))
        attempt.created_at = timezone.now() - timedelta(minutes=10)
        self.assertEqual(hubtel._retry_delay(attempt), timedelta(seconds=40))

    def test_paystack_data_cannot_settle_hubtel_attempt(self):
        attempt = self.pending()
        with self.assertRaises(ValidationError):
            services.finalize_payment(attempt.reference, {"status": "success", "amount": 20000, "currency": "GHS"})

    @override_settings(HUBTEL_CHECKOUT_ENABLED=False)
    @patch("marketplace.hubtel.requests.post")
    def test_disabled_checkout_does_not_contact_provider(self, post):
        with self.assertRaises(ValidationError):
            hubtel.initialize(self.order())
        post.assert_not_called()

    def test_provider_setting_retains_existing_attempt(self):
        attempt = self.pending()
        attempt.authorization_url = "https://pay.hubtel.com/saved"
        attempt.save()
        PaymentConfiguration.objects.create(provider="paystack")
        with patch("marketplace.services.initialize_paystack") as paystack:
            result = hubtel.initialize_payment(attempt.order, "https://example.test/return")
        self.assertEqual(result.pk, attempt.pk)
        paystack.assert_not_called()


    def test_callback_saves_exact_original_json_encrypted_and_allows_download(self):
        from .hubtel_evidence import decrypt_exchange
        attempt = self.pending()
        raw = (
            '{"ResponseCode":"0000","Data":{"ClientReference":"'
            + attempt.reference
            + '","Status":"Success","Amount":200,"CheckoutId":"abc"}}'
        ).encode("utf-8")
        response = self.client.post(
            "/market/payments/hubtel/callback/", raw,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        record = HubtelEvidence.objects.get(reference=attempt.reference, direction="callback")
        self.assertEqual(decrypt_exchange(record), raw)
        self.assertNotIn("CheckoutId", record.encrypted_body)
        self.assertIsNone(attempt.order.sale_document_id)

        url = f"/settings/online-payments/hubtel-evidence/download/{record.pk}/"
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        download = self.client.get(url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, raw)
        self.assertIn("no-store", download["Cache-Control"])
        index = self.client.get(
            f"/settings/online-payments/hubtel-evidence/{attempt.pk}/"
        )
        self.assertEqual(index.status_code, 200)
        self.assertContains(index, "Download original JSON")
        self.assertNotContains(index, "CheckoutId")
        self.assertNotContains(index, raw.decode("utf-8"))

    def test_failed_callback_is_saved_and_does_not_mark_paid(self):
        from .hubtel_evidence import decrypt_exchange
        attempt = self.pending()
        raw = json.dumps({"ResponseCode": "0000", "Data": {
            "ClientReference": attempt.reference, "Status": "Failed", "Amount": 200,
        }}).encode()
        self.assertEqual(self.client.post(
            "/market/payments/hubtel/callback/", raw, content_type="application/json"
        ).status_code, 200)
        evidence = HubtelEvidence.objects.get(reference=attempt.reference, direction="callback")
        self.assertEqual(decrypt_exchange(evidence), raw)
        attempt.order.refresh_from_db()
        self.assertNotEqual(attempt.order.payment_status, "paid")

    @patch("marketplace.hubtel.requests.get")
    def test_status_check_records_original_success_and_failed_response_bodies(self, get):
        from .hubtel_evidence import decrypt_exchange
        attempt = self.pending()
        good = json.dumps({
            "responseCode": "0000", "data": [{
                "clientReference": attempt.reference,
                "transactionStatus": "Success", "invoiceStatus": "Success",
                "transactionAmount": 200, "currencyCode": "GHS",
                "transactionId": "txn-confirmed",
            }],
        }, separators=(",", ":")).encode()
        get.return_value = Mock(status_code=200, content=good)
        get.return_value.json.return_value = json.loads(good)
        self.assertEqual(hubtel.verify(attempt.reference)["status"], "Paid")
        bad = b'{"responseCode":"4720","message":"Transaction not found","data":null}'
        get.return_value = Mock(status_code=400, content=bad)
        get.return_value.json.return_value = json.loads(bad)
        with self.assertRaises(services.PaymentVerificationUnavailable):
            hubtel.verify(attempt.reference)
        records = list(HubtelEvidence.objects.filter(
            reference=attempt.reference, direction="status_check"
        ).order_by("pk"))
        self.assertEqual(len(records), 2)
        self.assertEqual([item.http_status for item in records], [200, 400])
        self.assertEqual([decrypt_exchange(item) for item in records], [good, bad])

    def test_repeated_callbacks_get_separate_evidence_entries(self):
        attempt = self.pending()
        body = {"Data": {"ClientReference": attempt.reference, "Status": "Failed"}}
        for _ in range(2):
            self.assertEqual(self.client.post(
                "/market/payments/hubtel/callback/", body,
                content_type="application/json",
            ).status_code, 200)
        self.assertEqual(HubtelEvidence.objects.filter(
            reference=attempt.reference, direction="callback"
        ).count(), 2)
