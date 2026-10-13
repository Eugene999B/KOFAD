"""Switching off money initiation must not disable safe status/consent recovery."""
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from . import client, debit_api


SAFE_GET = {
    "HUBTEL_API_ID": "offline-id",
    "HUBTEL_API_KEY": "offline-key",
    "HUBTEL_COLLECTION_ACCOUNT": "12345",
    "HUBTEL_DISBURSEMENT_ACCOUNT": "67890",
    "HUBTEL_DIRECT_EGRESS_CONFIRMED": True,
    "HUBTEL_DIRECT_RECEIVE_ENABLED": False,
    "HUBTEL_DIRECT_SEND_MOMO_ENABLED": False,
    "HUBTEL_DIRECT_SEND_BANK_ENABLED": False,
    "HUBTEL_DEBT_PREAPPROVAL_ENABLED": False,
    "HUBTEL_DEBT_AUTO_DEBIT_ENABLED": False,
}


@override_settings(**SAFE_GET)
class FinanceRecoveryGatesTests(SimpleTestCase):
    def test_outbound_money_is_off_but_existing_status_checks_are_permitted(self):
        for kind in ("receive", "send_momo", "send_bank"):
            client.require_reconciliation_ready(kind)
            self.assertFalse(client.enabled(kind))
        with self.assertRaises(ValidationError):
            client.require_enabled("receive")
        with self.assertRaises(ValidationError):
            debit_api.gate("charge")
        with self.assertRaises(ValidationError):
            debit_api.gate("preapproval")

    @patch("hubtel_direct.debit_api._request")
    def test_customer_can_revoke_existing_approval_when_new_opt_in_is_off(self, get):
        get.return_value = {"responseCode": "2000", "data": True}
        self.assertTrue(debit_api.cancel_preapproval("0240000000"))
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], "GET")
        self.assertTrue(get.call_args.args[1].endswith("/cancel"))

    @override_settings(HUBTEL_DIRECT_EGRESS_CONFIRMED=False)
    @patch("hubtel_direct.debit_api._request")
    def test_unconfirmed_static_egress_blocks_status_and_revoke(self, get):
        with self.assertRaises(ValidationError):
            client.require_reconciliation_ready("receive")
        with self.assertRaises(ValidationError):
            debit_api.cancel_preapproval("0240000000")
        get.assert_not_called()

    @override_settings(HUBTEL_DISBURSEMENT_ACCOUNT="")
    def test_payout_status_requires_the_original_disbursement_account(self):
        client.require_reconciliation_ready("receive")
        with self.assertRaises(ValidationError):
            client.require_reconciliation_ready("send_bank")
