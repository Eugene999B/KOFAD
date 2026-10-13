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

    def test_credit_sale_shows_schedule_without_initiating_hubtel(self):
        self.login_staff()
        OnlineDebtSettings.objects.create(
            branch=self.branch, plan_creation_enabled=True,
            allow_mtn=True, allow_telecel=True, allow_weekly=True,
        )
        page = self.client.get("/sales/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="credit-repayment-mode"')
        self.assertContains(page, 'id="credit-direct-phone"')
        self.assertContains(page, 'id="credit-direct-installment"')
        self.assertContains(page, 'value="mtn-gh-direct-debit"')
        self.assertContains(page, 'value="weekly"')
        self.assertContains(page, 'id="sale-debit-authorization"')
        self.assertContains(page, 'id="hubtel-wallet-name"')
        self.assertNotContains(page, "Hubtel Direct Mobile Money")
        self.assertNotContains(page, "COUNTER SALES")
        self.assertNotContains(page, 'class="single-location"')

    def test_paused_new_mandates_display_settings_not_live_debit_fields(self):
        self.login_staff()
        OnlineDebtSettings.objects.create(
            branch=self.branch, plan_creation_enabled=False,
        )
        page = self.client.get("/sales/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Online Debt Settings")
        self.assertNotContains(page, 'id="credit-direct-phone"')
        self.assertContains(page, 'id="credit-repayment-mode"')

    def test_one_branch_hides_location_chrome_but_keeps_page_accessible(self):
        self.login_staff()
        page = self.client.get("/sales/new/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'class="single-location"')
        self.assertContains(page, 'href="/debts/"')
