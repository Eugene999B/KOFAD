"""Regression: compact POS, finance-gated Direct Debit setup and safe sale handoff."""

from hubtel_direct.models import OnlineDebtSettings
from marketplace.tests import MarketFixtures


class PosDirectDebitEntryTests(MarketFixtures):
    def login_staff(self):
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    def test_sale_page_offers_separate_invoice_authorization_not_inline_prompt(self):
        self.login_staff()
        OnlineDebtSettings.objects.create(
            branch=self.branch, plan_creation_enabled=True,
            allow_mtn=True, allow_telecel=True, allow_weekly=True,
        )
        page = self.client.get("/sales/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Record sale")
        self.assertContains(page, "Set up Direct Debit for this unpaid invoice")
        self.assertContains(page, 'id="sale-direct-debit-link"')
        self.assertContains(page, 'href="/debts/"')
        for old in ('id="credit-repayment-mode"', 'id="credit-direct-phone"',
                    'id="credit-direct-end-date"', 'id="credit-direct-date"',
                    'id="credit-direct-preview"', 'id="credit-direct-installment"',
                    'id="sale-debit-authorization"'):
            self.assertNotContains(page, old)
        self.assertNotContains(page, "Hubtel Direct Mobile Money")
        self.assertNotContains(page, "COUNTER SALES")
        self.assertNotContains(page, 'class="single-location"')

    def test_paused_new_mandates_keep_sale_checkout_simple_and_independent(self):
        self.login_staff()
        OnlineDebtSettings.objects.create(
            branch=self.branch, plan_creation_enabled=False,
        )
        page = self.client.get("/sales/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Manage Direct Debit")
        self.assertContains(page, "Record sale")
        self.assertNotContains(page, 'id="credit-direct-phone"')
        self.assertNotContains(page, 'id="credit-repayment-mode"')
        self.assertNotContains(page, 'id="sale-debit-authorization"')

    def test_one_branch_hides_location_chrome_but_keeps_page_accessible(self):
        self.login_staff()
        page = self.client.get("/sales/new/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'class="single-location"')
        self.assertContains(page, 'href="/debts/"')
