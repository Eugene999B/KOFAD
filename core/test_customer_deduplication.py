"""Customer identity deduplication on POS, debt ledger and contact creation."""
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from . import services
from .customer_guard import customer_conflicts
from .models import Document, Party
from .tests import Fixtures


class CustomerDeduplicationTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()
        self.customer.name = "Ama Boafo"
        self.customer.phone = "+233551234567"
        self.customer.save(update_fields=["name", "phone"])

    def fresh_payload(self, *, name="New Customer", phone="0241234567"):
        return {
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1}],
            "payments": [{"method": "cash", "amount": "50.00"}],
            "customer_name": name, "customer_phone": phone,
            "customer_consent": False, "send_sms": False, "send_whatsapp": False,
        }

    def test_duplicate_debtor_phone_blocks_sale_without_stock_changes(self):
        invoice = self.sale(
            payments=[{"method": "cash", "amount": "20.00"}],
            due_date=timezone.localdate().isoformat(),
        )
        self.assertEqual(services.balance(invoice), Decimal("30.00"))
        count = Party.objects.filter(kind="customer", branch=self.branch).count()
        before = Document.objects.count()
        with self.assertRaisesMessage(ValidationError, "phone number"):
            services.post_trade(self.user, self.branch,
                                self.fresh_payload(name="Different Person", phone="0551234567"),
                                uuid.uuid4())
        self.assertEqual(Party.objects.filter(kind="customer", branch=self.branch).count(), count)
        self.assertEqual(Document.objects.count(), before)
        self.assertEqual(services.balance(invoice), Decimal("30.00"))

    def test_same_name_with_different_phone_is_rejected_even_if_no_debt(self):
        with self.assertRaisesMessage(ValidationError, "name"):
            services.post_trade(
                self.user, self.branch,
                self.fresh_payload(name="  ama    boafo ", phone="0241234567"),
                uuid.uuid4(),
            )
        self.assertEqual(Party.objects.filter(branch=self.branch, kind="customer").count(), 1)

    def test_existing_customer_is_chosen_explicitly_without_new_record(self):
        payload = self.fresh_payload()
        payload.pop("customer_name")
        payload.pop("customer_phone")
        payload["party"] = self.customer.pk
        doc = services.post_trade(self.user, self.branch, payload, uuid.uuid4())
        self.assertEqual(doc.party_id, self.customer.pk)
        self.assertEqual(Party.objects.filter(branch=self.branch, kind="customer").count(), 1)

    def test_walk_in_does_not_create_unwanted_customer(self):
        payload = self.fresh_payload()
        payload.pop("customer_name")
        payload.pop("customer_phone")
        doc = services.post_trade(self.user, self.branch, payload, uuid.uuid4())
        self.assertIsNone(doc.party)
        self.assertEqual(Party.objects.filter(branch=self.branch, kind="customer").count(), 1)

    def test_distinct_customer_is_saved_for_future_history(self):
        doc = services.post_trade(self.user, self.branch, self.fresh_payload(), uuid.uuid4())
        self.assertEqual(doc.party.name, "New Customer")
        self.assertEqual(doc.party.phone, "+233241234567")
        self.assertEqual(Document.objects.filter(party=doc.party, kind="sale").count(), 1)

    def test_customer_duplicate_preflight_includes_existing_debt_and_selectable_record(self):
        doc = self.sale(
            payments=[{"method": "cash", "amount": "20.00"}],
            due_date=timezone.localdate().isoformat(),
        )
        response = self.client.get("/api/customers/check-duplicate/", {
            "name": "Different Person", "phone": "0551234567",
        })
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["matches"][0]["id"], self.customer.pk)
        self.assertEqual(result["matches"][0]["outstanding"], "30.00")
        self.assertTrue(result["matches"][0]["phone_match"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(services.balance(doc), Decimal("30.00"))

    def test_legacy_formatted_phone_cannot_create_second_debtor(self):
        self.customer.phone = "+233 (55) 123-4567"
        self.customer.save(update_fields=["phone"])
        with self.assertRaisesMessage(ValidationError, "phone number"):
            services.post_trade(
                self.user, self.branch,
                self.fresh_payload(name="Unrelated Name", phone="0551234567"),
                uuid.uuid4(),
            )
        self.assertEqual(Party.objects.filter(branch=self.branch, kind="customer").count(), 1)

    def test_customer_form_blocks_duplicate_phone_and_name(self):
        for name, phone in [("Other Person", "0551234567"), ("Ama Boafo", "0241234567")]:
            response = self.client.post("/parties/new/?kind=customer", {
                "name": name, "phone": phone, "email": "", "address": "",
            })
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "saved customer already uses")
        self.assertEqual(Party.objects.filter(branch=self.branch, kind="customer").count(), 1)

    def test_similar_names_are_not_automatically_merged(self):
        matches = customer_conflicts(self.branch, "Ama Boafos", "0241234567")
        self.assertEqual(matches, [])
        with self.assertRaisesMessage(ValidationError, "name"):
            services.post_trade(
                self.user, self.branch,
                self.fresh_payload(name="AMA BOAFO", phone="0241234567"),
                uuid.uuid4(),
            )

    def test_another_branch_may_hold_a_separate_customer_record(self):
        payload = self.fresh_payload(name="Ama Boafo", phone="0551234567")
        self.other.active = True
        self.other.save(update_fields=["active"])
        from .models import Stock
        Stock.objects.create(branch=self.other, product=self.product, quantity=10)
        doc = services.post_trade(self.user, self.other, payload, uuid.uuid4())
        self.assertEqual(doc.party.branch_id, self.other.pk)
        self.assertEqual(doc.party.name, "Ama Boafo")
