import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from . import services as s
from .models import Audit, Company, Line
from .tests import Fixtures


class BusinessPolicyTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.company = Company.objects.get()
        self.authenticate_client()

    def sale_payload(self, **changes):
        payload = {
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1}],
            "payments": [{"method": "cash", "amount": "50"}],
        }
        payload.update(changes)
        return payload

    def policy_user(self, username, permission):
        user = User.objects.create_user(username, password="policy-user-password-2026")
        user.user_permissions.add(Permission.objects.get(codename=permission))
        user.access.branches.add(self.branch)
        return user

    def test_payment_method_setting_hides_and_blocks_disabled_channel(self):
        response = self.client.post("/settings/payments/", {"payment_momo": "on"})
        self.assertEqual(response.status_code, 302)
        self.company.refresh_from_db()
        self.assertFalse(self.company.payment_cash)
        self.assertTrue(self.company.payment_momo)

        page = self.client.get("/sales/new/")
        self.assertNotContains(page, 'id="pay-cash"')
        self.assertContains(page, 'id="pay-momo"')

        with self.assertRaisesRegex(ValidationError, "Cash is disabled"):
            s.post_trade(self.user, self.branch, self.sale_payload(), uuid.uuid4())

    def test_discount_is_server_priced_and_snapshotted(self):
        self.company.allow_discounts = True
        self.company.staff_discount_limit = Decimal("5")
        self.company.max_discount_percent = Decimal("20")
        self.company.save()

        doc = s.post_trade(self.user, self.branch, {
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1, "discount": "10"}],
            "payments": [{"method": "cash", "amount": "45"}],
            "override_reason": "Approved customer loyalty discount",
        }, uuid.uuid4())

        line = doc.lines.get()
        self.assertEqual(line.list_price, Decimal("50"))
        self.assertEqual(line.unit_price, Decimal("45"))
        self.assertEqual(line.discount_percent, Decimal("10"))
        evidence = Audit.objects.get(action="sale.posted", reference=doc.reference).detail
        self.assertEqual(evidence["overrides"][0]["type"], "discount")

    def test_cashier_cannot_exceed_staff_discount_limit(self):
        self.company.allow_discounts = True
        self.company.staff_discount_limit = Decimal("5")
        self.company.max_discount_percent = Decimal("20")
        self.company.save()
        cashier = self.policy_user("discount-cashier", "operate_sales")

        with self.assertRaisesRegex(ValidationError, "manager with approval authority"):
            s.post_trade(cashier, self.branch, {
                "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1, "discount": "10"}],
                "payments": [{"method": "cash", "amount": "45"}],
                "override_reason": "Requested loyalty discount at counter",
            }, uuid.uuid4())

    def test_price_override_uses_configured_reduction_limits_and_reason(self):
        self.company.allow_price_overrides = True
        self.company.staff_price_reduction_limit = Decimal("5")
        self.company.max_price_reduction_percent = Decimal("20")
        self.company.save()

        with self.assertRaisesRegex(ValidationError, "Explain the discount or price override"):
            s.post_trade(self.user, self.branch, {
                "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1, "price": "45"}],
                "payments": [{"method": "cash", "amount": "45"}],
            }, uuid.uuid4())

        doc = s.post_trade(self.user, self.branch, {
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1, "price": "45"}],
            "payments": [{"method": "cash", "amount": "45"}],
            "override_reason": "Manager approved negotiated selling price",
        }, uuid.uuid4())
        line = doc.lines.get()
        self.assertEqual(line.list_price, Decimal("50"))
        self.assertEqual(line.unit_price, Decimal("45"))

        with self.assertRaisesRegex(ValidationError, "configured maximum"):
            s.post_trade(self.user, self.branch, {
                "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1, "price": "35"}],
                "payments": [{"method": "cash", "amount": "35"}],
                "override_reason": "Manager requested excessive reduction test",
            }, uuid.uuid4())

    def test_credit_policy_enforces_term_and_manager_override(self):
        self.company.max_credit_days = 30
        self.company.max_credit_override = Decimal("40")
        self.company.save()
        self.customer.credit_limit = Decimal("20")
        self.customer.save()
        due = timezone.localdate() + timedelta(days=10)

        doc = s.post_trade(self.user, self.branch, {
            "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1}],
            "payments": [],
            "party": self.customer.pk,
            "due_date": due.isoformat(),
            "override_reason": "Owner approved temporary credit exception",
        }, uuid.uuid4())
        self.assertEqual(s.balance(doc), Decimal("50"))

        too_far = timezone.localdate() + timedelta(days=31)
        with self.assertRaisesRegex(ValidationError, "Credit terms cannot exceed 30 days"):
            s.post_trade(self.user, self.branch, {
                "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1}],
                "payments": [],
                "party": self.customer.pk,
                "due_date": too_far.isoformat(),
                "override_reason": "Owner requested long credit test only",
            }, uuid.uuid4())

    def test_credit_can_be_disabled(self):
        self.company.allow_credit_sales = False
        self.company.save()
        with self.assertRaisesRegex(ValidationError, "Credit sales are disabled"):
            s.post_trade(self.user, self.branch, {
                "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1}],
                "payments": [],
                "party": self.customer.pk,
                "due_date": timezone.localdate().isoformat(),
            }, uuid.uuid4())

    def test_customer_and_sale_authority_thresholds_are_enforced(self):
        self.company.customer_required_above = Decimal("40")
        self.company.sale_manager_threshold = Decimal("40")
        self.company.save()
        cashier = self.policy_user("threshold-cashier", "operate_sales")

        with self.assertRaisesRegex(ValidationError, "named customer is required"):
            s.post_trade(cashier, self.branch, self.sale_payload(), uuid.uuid4())

        payload = self.sale_payload(party=self.customer.pk)
        with self.assertRaisesRegex(ValidationError, "manager with approval authority"):
            s.post_trade(cashier, self.branch, payload, uuid.uuid4())

    def test_expense_manager_threshold_is_enforced(self):
        self.company.expense_manager_threshold = Decimal("100")
        self.company.save()
        accountant = self.policy_user("policy-accountant", "operate_finance")

        with self.assertRaisesRegex(ValidationError, "manager with approval authority"):
            s.post_expense(accountant, self.branch, {
                "amount": "150", "method": "cash", "note": "Large transport expense",
            }, uuid.uuid4())

        doc = s.post_expense(self.user, self.branch, {
            "amount": "150", "method": "cash", "note": "Large transport expense",
        }, uuid.uuid4())
        self.assertEqual(doc.total, Decimal("150"))

    def test_reference_prefix_applies_only_to_new_records(self):
        first = self.sale()
        self.company.reference_prefix = "KOFAD"
        self.company.save()
        second = self.sale()
        self.assertFalse(first.reference.startswith("KOFAD-"))
        self.assertTrue(second.reference.startswith("KOFAD-SAL-"))

    def test_receipt_visibility_settings_are_respected(self):
        self.customer.phone = "0240009876"
        self.customer.save(update_fields=["phone"])
        self.company.receipt_show_staff = False
        self.company.receipt_show_contact_phone = False
        self.company.receipt_show_payment_reference = False
        self.company.save()
        doc = self.sale(party=self.customer.pk)
        response = self.client.get(f"/documents/{doc.pk}/")
        self.assertNotContains(response, "RECORDED BY")
        self.assertNotContains(response, self.customer.phone)

    def test_historical_payment_reversal_survives_channel_disable(self):
        expense = s.post_expense(self.user, self.branch, {
            "amount": "20", "method": "cash", "note": "Office supply expense",
        }, uuid.uuid4())
        request = s.request_correction(self.user, self.branch, expense.pk, "Incorrect expense recorded")
        self.company.payment_cash = False
        self.company.save()
        posted = s.review_correction(self.reviewer, self.branch, request.pk, True).posted
        self.assertEqual(posted.payments.get().method, "cash")

    def test_all_new_settings_pages_render(self):
        for path in (
            "/settings/sales/", "/settings/payments/", "/settings/finance/", "/settings/receipts/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
