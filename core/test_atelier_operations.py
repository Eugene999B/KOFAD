"""Regression coverage for the KOFAD Atelier operational navigation.

The visual-only rollout must never change permission checks, business values,
form actions, or the existing owner workflows.
"""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from core.models import Branch, Company


class AtelierOperationsSourceTests(SimpleTestCase):
    def test_operations_layer_is_loaded_after_base_visual_system(self):
        root = Path(settings.BASE_DIR)
        shell = (root / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("kofad-atelier-ops.css", shell)
        self.assertGreater(shell.index("kofad-atelier-ops.css"),
                           shell.index("kofad-atelier.css"))
        css = (root / "static" / "kofad-atelier-ops.css").read_text(encoding="utf-8")
        for marker in (
            'html[data-theme="dark"]', "body.staff-dashboard-refined",
            ".atelier-settings-group", ".atelier-stock-jump",
            ".atelier-insights-index", ".mail-workspace", ".approval-card",
            "@media(max-width:700px)", "prefers-reduced-motion:reduce"
        ):
            self.assertIn(marker, css)
        self.assertEqual(css.count("{"), css.count("}"))
        self.assertNotIn("@import", css)

    def test_settings_sections_keep_real_owner_actions_and_access_controls(self):
        s = (Path(settings.BASE_DIR) / "templates" / "settings_center.html").read_text(encoding="utf-8")
        ids = (
            "atelier-settings-business", "atelier-settings-people",
            "atelier-settings-messages", "atelier-settings-tools",
            "atelier-settings-appearance",
        )
        for id_ in ids:
            self.assertIn(f'href="#{id_}"', s)
            self.assertIn(f'id="{id_}"', s)
        self.assertIn('href="/settings/online-payments/"', s)
        self.assertIn("request.user.is_superuser", s)
        for theme in ("light", "dark", "system"):
            self.assertIn(f'data-theme-choice="{theme}"', s)

    def test_inventory_and_reports_jump_links_target_existing_sections(self):
        root = Path(settings.BASE_DIR) / "templates"
        for filename, ids in (
            ("inventory.html", ("atelier-inventory-search",
                                "atelier-inventory-ledger", "atelier-inventory-movement")),
            ("reports.html", ("atelier-reports-brief",
                              "atelier-reports-actions", "atelier-reports-trajectory",
                              "intelligence-evidence")),
        ):
            s = (root / filename).read_text(encoding="utf-8")
            for id_ in ids:
                self.assertIn(f'href="#{id_}"', s)
                self.assertIn(f'id="{id_}"', s)
        inventory = (root / "inventory.html").read_text(encoding="utf-8")
        self.assertIn("data-restock-open", inventory)
        self.assertIn("restock-form", inventory)
        report = (root / "reports.html").read_text(encoding="utf-8")
        self.assertIn("Run drill-down", report)


class AtelierOperationsPageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            "atelier-ops-owner", password="CI-only-synthetic-password"
        )
        self.branch = Branch.objects.create(name="Atelier branch", code="atelier")
        Company.objects.create()
        self.owner.access.refresh_from_db()
        self.client.force_login(self.owner)
        session = self.client.session
        session["access_version"] = self.owner.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    def test_settings_and_inventory_keep_live_owner_controls(self):
        settings_page = self.client.get("/settings/")
        self.assertEqual(settings_page.status_code, 200)
        self.assertContains(settings_page, "atelier-settings-business")
        self.assertContains(settings_page, "atelier-settings-appearance")
        self.assertContains(settings_page, "kofad-atelier-ops")
        inventory = self.client.get("/inventory/")
        self.assertEqual(inventory.status_code, 200)
        self.assertContains(inventory, "atelier-inventory-ledger")
        self.assertContains(inventory, "atelier-inventory-movement")

    def test_contact_directory_links_to_real_accounts(self):
        response = self.client.get("/parties/?kind=customer")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "atelier-contacts-intro")
        self.assertContains(response, "atelier-contact-ledger")
        self.assertContains(response, "Suppliers")
