from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .accounting_views import _filtered_ledger
from .models import Branch, Company


class AccountingNavigationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("accounting-owner", password="test-only-password")
        self.branch = Branch.objects.create(name="Main", code="main")
        Company.objects.create()
        self.client.force_login(self.owner)

    def test_invalid_date_range_returns_a_useful_page_instead_of_server_error(self):
        for url in ("/accounting/", "/accounting/export/csv/"):
            response = self.client.get(url, {"start": "not-a-date"})
            self.assertRedirects(response, "/accounting/", fetch_redirect_response=False)

    def test_filters_combine_and_exports_match_the_screen(self):
        today = timezone.localdate()
        rows = [
            {"date": today, "reference": "SALE-ONE", "source": "Sale",
             "description": "Counter sale", "account_code": "1000", "account": "Cash",
             "debit": Decimal("25"), "credit": Decimal("0")},
            {"date": today, "reference": "SALE-TWO", "source": "Sale",
             "description": "Counter sale", "account_code": "4000", "account": "Revenue",
             "debit": Decimal("0"), "credit": Decimal("25")},
        ]
        query = {"view": "ledger", "account": "1000", "source": "Sale", "q": "sale-one"}
        request = RequestFactory().get("/", query)
        self.assertEqual(_filtered_ledger(request, rows), rows[:1])
        with patch("core.accounting_views.engine.ledger", return_value=rows):
            response = self.client.get("/accounting/export/csv/", query)
        self.assertContains(response, "SALE-ONE")
        self.assertNotContains(response, "SALE-TWO")

    def test_ledger_does_not_drop_entries_beyond_two_thousand(self):
        row = {"date": date(2026, 1, 1), "reference": "LAST-ENTRY", "source": "Sale",
               "description": "Sale", "account_code": "1000", "account": "Cash",
               "debit": Decimal("1"), "credit": Decimal("0")}
        with patch("core.accounting_views.engine.ledger", return_value=[row] * 2001):
            response = self.client.get("/accounting/", {"view": "ledger", "page": 21})
        self.assertEqual(response.context["ledger_page"].paginator.count, 2001)
        self.assertEqual(len(response.context["ledger_rows"]), 1)
        self.assertEqual(response.context["ledger_debits"], Decimal("2001"))
