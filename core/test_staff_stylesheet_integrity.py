"""Prevent staff-wide design regressions when unrelated feature branches merge.

Staff overview, reports, inventory and settings depend on the shared CSS
pipeline and on the per-page after_styles block in base.html.
"""
from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase
from core.test_atelier_operations import AtelierOperationsPageTests


class StaffDesignAssetContractTests(SimpleTestCase):
    def test_base_template_keeps_staff_design_pipeline(self):
        shell = (Path(settings.BASE_DIR) / "templates/base.html").read_text(encoding="utf-8")
        expected = (
            "app.css", "ui-v4.css", "brand-system.css",
            "premium-dark-v2.css", "operations-intelligence-v1.css",
            "brand-transparent.css", "kofad-contrast-mobile.css",
            "kofad-atelier.css", "kofad-atelier-ops.css",
            "kofad-atelier-commerce.css", "kofad-mobile-reachability.css",
            "password-visibility.css",
        )
        for name in expected:
            with self.subTest(css=name):
                self.assertIn("{% static '" + name + "' %}", shell)
                self.assertTrue((Path(settings.BASE_DIR) / "static" / name).is_file())
        self.assertIn("{% block after_styles %}{% endblock %}", shell)
        self.assertLess(shell.index("ui-v4.css"), shell.index("kofad-atelier.css"))
        self.assertLess(shell.index("kofad-atelier.css"), shell.index("kofad-atelier-ops.css"))
        self.assertLess(shell.index("kofad-atelier-ops.css"), shell.index("kofad-atelier-commerce.css"))
        self.assertIn("/settings/connections/", shell)
        self.assertIn("Email Centre", shell)

    def test_dashboard_still_uses_per_page_css_hook(self):
        page = (Path(settings.BASE_DIR) / "templates/dashboard.html").read_text(encoding="utf-8")
        self.assertIn("{% block after_styles %}", page)
        self.assertIn("staff-refinement.css", page)
        self.assertIn("experience-upgrade.css", page)
        self.assertIn("staff-dashboard-refined", page)
        self.assertIn("executive-hero", page)
        self.assertIn("executive-kpis", page)
        for asset in ("staff-refinement.css", "experience-upgrade.css"):
            self.assertTrue((Path(settings.BASE_DIR) / "static" / asset).is_file())


class StaffDesignRenderedPagesTests(AtelierOperationsPageTests):
    def test_overview_renders_all_restored_css_and_core_actions(self):
        response = self.client.get("/workspace/")
        self.assertEqual(response.status_code, 200)
        for asset in ("staff-refinement.css", "experience-upgrade.css",
                      "kofad-atelier.css", "kofad-atelier-ops.css",
                      "kofad-atelier-commerce.css", "brand-transparent.css"):
            self.assertContains(response, asset)
        self.assertContains(response, "KOFAD COMMAND CENTRE")
        self.assertContains(response, "Operational control pulse")
        self.assertContains(response, "/settings/connections/")
        self.assertContains(response, "Email Centre")

    def test_settings_page_keeps_atelier_and_new_connections(self):
        response = self.client.get("/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kofad-atelier-ops.css")
        self.assertContains(response, "/settings/connections/")
        self.assertContains(response, "brand-system.css")
