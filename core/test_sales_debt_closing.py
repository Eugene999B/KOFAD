import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from . import debts as debt_service
from . import services as s
from .identity import normalize_ghana_phone
from .models import Audit, Closing, Document, Movement, Party, Product, Stock
from .tests import Fixtures


class FastSalesCustomerDebtClosingTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def payload(self, *, quantity=1, payments=None, party=None, **extra):
        data = {
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": quantity}],
            "payments": payments if payments is not None else [{"method": "cash", "amount": str(50 * quantity)}],
        }
        if party is not None:
            data["party"] = party
        data.update(extra)
        return data

    def test_ghana_phone_normalization_accepts_local_and_national_input(self):
        for value in ("0241234567", "241234567", "+233241234567", "233 24 123 4567"):
            with self.subTest(value=value):
                self.assertEqual(normalize_ghana_phone(value), "+233241234567")
        for value in ("024123456", "02412345678", "233123"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_ghana_phone(value)

    def test_checkout_creates_and_then_reuses_customer_by_phone(self):
        first = s.post_trade(self.user, self.branch, self.payload(
            customer_name="Ama Mensah",
            customer_phone="0241234567",
        ), uuid.uuid4())
        customer = first.party
        self.assertEqual(customer.name, "Ama Mensah")
        self.assertEqual(customer.phone, "+233241234567")

        second = s.post_trade(self.user, self.branch, self.payload(
            customer_name="Ama M.",
            customer_phone="241234567",
        ), uuid.uuid4())
        self.assertEqual(second.party_id, customer.pk)
        self.assertEqual(
            Party.objects.filter(branch=self.branch, kind="customer", phone="+233241234567").count(),
            1,
        )

    def test_fully_paid_sale_can_remain_walk_in(self):
        sale = s.post_trade(self.user, self.branch, self.payload(), uuid.uuid4())
        self.assertIsNone(sale.party_id)
        self.assertEqual(sale.paid, sale.total)

    def test_two_and_half_packs_leave_exact_twenty_seven_and_half(self):
        Stock.objects.filter(branch=self.branch, product=self.product).update(quantity=360)
        doc = s.post_trade(self.user, self.branch, {
            "party": self.customer.pk,
            "items": [
                {"product": self.product.pk, "mode": "retail_pack", "quantity": 2},
                {"product": self.product.pk, "mode": "retail_unit", "quantity": 6},
            ],
            "payments": [{"method": "cash", "amount": "1400"}],
        }, uuid.uuid4())
        self.assertEqual(doc.total, Decimal("1400"))
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 330)
        self.assertEqual(Decimal(330) / Decimal(self.product.pack_size), Decimal("27.5"))

        self.authenticate_client()
        response = self.client.get("/inventory/")
        self.assertContains(response, "27.50")
        self.assertContains(response, "27 Test carton".replace("Test carton", self.product.pack_name))

    def test_pos_is_search_first_and_product_search_is_bounded(self):
        self.authenticate_client()
        page = self.client.get("/sales/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Find a product")
        self.assertContains(page, "Search, choose the item, and it goes straight into the current sale.")
        self.assertNotContains(page, self.product.name)

        result = self.client.get("/sales/new/", {"format": "json", "q": self.product.sku})
        self.assertEqual(result.status_code, 200)
        body = result.json()
        self.assertFalse(body["search_required"])
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["catalog"][0]["id"], self.product.pk)

        blank = self.client.get("/sales/new/", {"format": "json"})
        self.assertTrue(blank.json()["search_required"])
        self.assertEqual(blank.json()["catalog"], [])

    def test_quick_restock_adds_packs_and_loose_units_without_replacing_balance(self):
        before = Stock.objects.get(branch=self.branch, product=self.product).quantity
        result = s.restock_inventory(
            self.user,
            self.branch,
            self.product.pk,
            packs="3",
            loose="5",
            note="Received supplier top-up delivery",
            external_reference="DN-TEST-001",
            unit_cost="22.00",
        )
        expected_added = 3 * self.product.pack_size + 5
        self.assertEqual(result["before"], before)
        self.assertEqual(result["added"], expected_added)
        self.assertEqual(result["after"], before + expected_added)
        stock = Stock.objects.get(branch=self.branch, product=self.product)
        self.assertEqual(stock.quantity, before + expected_added)
        self.product.refresh_from_db()
        self.assertEqual(self.product.cost, Decimal("22.00"))
        movement = Movement.objects.get(reference="DN-TEST-001")
        self.assertEqual(movement.delta, expected_added)
        self.assertEqual(movement.balance, before + expected_added)
        evidence = Audit.objects.filter(action="inventory.restocked", reference=self.product.sku).latest("created_at")
        self.assertEqual(evidence.detail["stock_before"], before)
        self.assertEqual(evidence.detail["stock_after"], before + expected_added)

        with self.assertRaisesRegex(ValidationError, "less than one full"):
            s.restock_inventory(
                self.user,
                self.branch,
                self.product.pk,
                packs="0",
                loose=str(self.product.pack_size),
                note="Invalid loose quantity test",
            )

    def test_inventory_post_restock_and_workspace_render(self):
        self.authenticate_client()
        before = Stock.objects.get(branch=self.branch, product=self.product).quantity
        response = self.client.post("/inventory/", {
            "product": str(self.product.pk),
            "packs": "2",
            "loose": "3",
            "note": "Received counter stock delivery",
            "reference": "RESTOCK-WEB-001",
            "unit_cost": "21.00",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/inventory/")
        self.assertEqual(
            Stock.objects.get(branch=self.branch, product=self.product).quantity,
            before + 2 * self.product.pack_size + 3,
        )
        page = self.client.get("/inventory/")
        self.assertContains(page, "Find product to restock")
        self.assertContains(page, "QUICK RESTOCK")
        self.assertContains(page, "Recent stock movement")
        self.assertContains(page, "RESTOCK-WEB-001")

    def test_product_setup_can_record_opening_packs_and_loose_units(self):
        self.authenticate_client()
        response = self.client.post("/products/new/", {
            "name": "Packed filter",
            "sku": "FILTER-PACK",
            "barcode": "",
            "category": "Filters",
            "base_unit": "piece",
            "pack_enabled": "yes",
            "pack_name": "box",
            "pack_size": "12",
            "opening_packs": "30",
            "opening_units": "6",
            "cost": "10.00",
            "retail_unit": "15.00",
            "retail_pack": "170.00",
            "wholesale_unit": "14.00",
            "wholesale_pack": "156.00",
            "reorder_level": "24",
            "active": "on",
        })
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(sku="FILTER-PACK")
        self.assertEqual(Stock.objects.get(branch=self.branch, product=product).quantity, 366)
        movement = Movement.objects.get(branch=self.branch, product=product)
        self.assertEqual(movement.delta, 366)
        self.assertIn("Opening stock", movement.reason)

    def test_debt_snapshot_has_aging_credit_usage_and_recent_payments(self):
        today = timezone.localdate()
        old = Document(
            id=uuid.uuid4(),
            reference="AGING-OLD-001",
            branch=self.branch,
            kind="sale",
            party=self.customer,
            finalized=True,
            total=Decimal("50.00"),
            paid=Decimal("0.00"),
            due_date=today - timedelta(days=45),
            note="Historical aging fixture",
            created_by=self.user,
            created_at=timezone.now() - timedelta(days=50),
        )
        old.save_base(raw=True, force_insert=True, using="default")
        current = s.post_trade(self.user, self.branch, self.payload(
            payments=[], party=self.customer.pk,
            due_date=(today + timedelta(days=10)).isoformat(),
        ), uuid.uuid4())
        self.customer.credit_limit = Decimal("500")
        self.customer.save(update_fields=["credit_limit"])

        payment = debt_service.post_customer_payment(self.user, self.branch, {
            "party": self.customer.pk,
            "amount": "20",
            "method": "cash",
            "note": "Part payment collected at counter",
        }, uuid.uuid4())

        snapshot = debt_service.customer_account_snapshot(self.customer)
        self.assertEqual(snapshot["outstanding"], Decimal("80"))
        self.assertEqual(snapshot["aging"]["days_31_60"], Decimal("30"))
        self.assertEqual(snapshot["aging"]["current"], Decimal("50"))
        self.assertEqual(snapshot["maximum_days_overdue"], 45)
        self.assertEqual(snapshot["available_credit"], Decimal("420"))
        self.assertEqual(snapshot["credit_usage_percent"], Decimal("16.00"))
        self.assertEqual(snapshot["recent_payments"][0].pk, payment.pk)
        self.assertEqual(snapshot["invoice_rows"][0]["invoice"].pk, old.pk)
        self.assertEqual(snapshot["invoice_rows"][1]["invoice"].pk, current.pk)

        self.authenticate_client()
        page = self.client.get("/debts/", {"customer": self.customer.pk, "status": "all"})
        self.assertContains(page, "Aging position")
        self.assertContains(page, "Record partial payment")
        self.assertContains(page, "Allocation preview")
        self.assertContains(page, old.reference)

    def test_customer_level_partial_payment_allocates_oldest_due_first(self):
        today = timezone.localdate()
        first = s.post_trade(self.user, self.branch, self.payload(
            payments=[], party=self.customer.pk, due_date=today.isoformat()
        ), uuid.uuid4())
        second = s.post_trade(self.user, self.branch, self.payload(
            quantity=2, payments=[], party=self.customer.pk,
            due_date=(today + timedelta(days=1)).isoformat()
        ), uuid.uuid4())

        payment = debt_service.post_customer_payment(self.user, self.branch, {
            "party": self.customer.pk,
            "amount": "70",
            "method": "momo",
        }, uuid.uuid4())
        allocations = list(payment.allocations.order_by("invoice__due_date", "invoice__created_at"))
        self.assertEqual([(row.invoice_id, row.amount) for row in allocations], [
            (first.pk, Decimal("50")),
            (second.pk, Decimal("20")),
        ])
        self.assertEqual(s.balance(first), Decimal("0"))
        self.assertEqual(s.balance(second), Decimal("80"))

        with self.assertRaisesRegex(ValidationError, "cannot exceed"):
            debt_service.post_customer_payment(self.user, self.branch, {
                "party": self.customer.pk,
                "amount": "81",
                "method": "cash",
            }, uuid.uuid4())

        settled = debt_service.post_customer_payment(self.user, self.branch, {
            "party": self.customer.pk,
            "pay_full": "1",
            "method": "bank",
        }, uuid.uuid4())
        self.assertEqual(settled.total, Decimal("80"))
        self.assertEqual(debt_service.customer_account_snapshot(self.customer)["outstanding"], Decimal("0"))

    def test_customer_search_profile_debt_page_and_debt_export(self):
        due = (timezone.localdate() + timedelta(days=7)).isoformat()
        s.post_trade(self.user, self.branch, self.payload(
            payments=[], party=self.customer.pk, due_date=due
        ), uuid.uuid4())
        self.customer.phone = "+233241234567"
        self.customer.save(update_fields=["phone"])
        self.authenticate_client()

        response = self.client.get("/api/customers/?q=0241234567")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["customers"][0]["id"], self.customer.pk)
        self.assertEqual(response.json()["customers"][0]["outstanding"], "50.00")

        for path in ("/sales/new/", "/debts/", f"/customers/{self.customer.pk}/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        export = self.client.get("/exports/download/csv/?dataset=debts")
        self.assertEqual(export.status_code, 200)
        self.assertIn(self.customer.name.encode(), export.content)
        self.assertIn(b"Outstanding", export.content)

    def test_daily_closing_explains_credit_collections_refunds_and_cash_controls(self):
        today = timezone.localdate()
        paid_sale = self.sale(2)
        s.post_trade(self.user, self.branch, self.payload(
            payments=[], party=self.customer.pk,
            due_date=today.isoformat(),
        ), uuid.uuid4())
        s.post_trade(self.user, self.branch, self.payload(
            payments=[{"method": "momo", "amount": "20"}],
            party=self.customer.pk,
            due_date=(today + timedelta(days=1)).isoformat(),
        ), uuid.uuid4())
        debt_service.post_customer_payment(self.user, self.branch, {
            "party": self.customer.pk,
            "amount": "25",
            "method": "bank",
        }, uuid.uuid4())
        s.post_expense(self.user, self.branch, {
            "amount": "10", "method": "cash", "note": "Counter transport expense",
        }, uuid.uuid4())
        s.post_return(self.user, self.branch, {
            "line": paid_sale.lines.first().pk,
            "quantity": 1,
            "reason": "Customer returned one unit",
            "method": "cash",
        }, uuid.uuid4())

        summary = s.closing_summary(self.branch, today)
        self.assertEqual(summary["sales_total"], Decimal("200"))
        self.assertEqual(summary["sales_received_at_checkout"], Decimal("120"))
        self.assertEqual(summary["credit_created"], Decimal("80"))
        self.assertEqual(summary["returns_total"], Decimal("50"))
        self.assertEqual(summary["net_sales"], Decimal("150"))
        self.assertEqual(summary["debt_collections"], Decimal("25"))
        self.assertEqual(summary["expenses_total"], Decimal("10"))
        self.assertEqual(summary["channel_net"]["cash"], Decimal("40"))
        self.assertEqual(summary["channel_net"]["momo"], Decimal("20"))
        self.assertEqual(summary["channel_net"]["bank"], Decimal("25"))

        closing = s.submit_closing(
            self.user, self.branch, today,
            {"cash": "62", "momo": "20", "bank": "25", "card": "0"},
            "Counts agree with provider evidence",
            opening_cash="20", cash_in="5", cash_out="3",
        )
        self.assertEqual(closing.expected["cash"], "62.00")
        self.assertEqual(closing.summary["credit_created"], "80.00")
        self.assertEqual(closing.summary["debt_collections"], "25.00")
        self.assertEqual(closing.summary["variance"]["cash"], "0.00")

        self.authenticate_client()
        page = self.client.get(f"/closings/?date={today.isoformat()}")
        self.assertContains(page, "Credit created")
        self.assertContains(page, "Debt collected")
        export = self.client.get("/exports/download/csv/?dataset=closings")
        self.assertEqual(export.status_code, 200)
        self.assertIn(b"Credit created", export.content)
        self.assertIn(b"Opening cash", export.content)
