"""Customer private order intelligence never escapes account filters or free models."""
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.models import Branch
from marketplace.kofad_ai import answer
from marketplace.models import CustomerAccount, OnlineOrder


@override_settings(KOFAD_GEMINI_ENABLED=True, KOFAD_GEMINI_FREE_TIER_CONFIRMED=True,
                   GEMINI_API_KEY="test-only-key")
class CustomerOrderAssistantTests(TestCase):
    def setUp(self):
        branch = Branch.objects.create(name="Customer AI Store", code="caistore")
        self.ada = CustomerAccount.objects.create(full_name="Ada", password_hash="unused")
        self.kofi = CustomerAccount.objects.create(full_name="Kofi", password_hash="unused")
        common = dict(branch=branch, recipient_name="Test", phone="0200000000",
                      email="test@example.test")
        self.ada_order = OnlineOrder.objects.create(
            customer=self.ada, public_reference="KOF-ADA-ORDER", status="ready_pickup",
            payment_status="paid", **common,
        )
        self.kofi_order = OnlineOrder.objects.create(
            customer=self.kofi, public_reference="KOF-KOFI-PRIVATE", status="preparing",
            payment_status="unpaid", **common,
        )

    @patch("marketplace.kofad_ai.requests.post")
    def test_own_reference_and_next_step_only(self, external):
        reply, source = answer("Where is my order KOF-ADA-ORDER?", customer=self.ada)
        self.assertEqual(source, "private")
        self.assertIn("KOF-ADA-ORDER", reply)
        self.assertIn("ready for pickup", reply)
        self.assertNotIn("KOF-KOFI-PRIVATE", reply)
        external.assert_not_called()

    @patch("marketplace.kofad_ai.requests.post")
    def test_cross_customer_reference_never_lookup(self, external):
        reply, source = answer("Where is my order KOF-KOFI-PRIVATE?", customer=self.ada)
        self.assertEqual(source, "private")
        self.assertNotIn("KOF-KOFI-PRIVATE", reply)
        self.assertIn("KOF-ADA-ORDER", reply)
        external.assert_not_called()

    @patch("marketplace.kofad_ai.requests.post")
    def test_anonymous_cannot_view_orders(self, external):
        reply, source = answer("Show my latest orders")
        self.assertEqual(source, "local")
        self.assertNotIn("KOF-ADA-ORDER", reply)
        self.assertIn("Sign in", reply)
        external.assert_not_called()
