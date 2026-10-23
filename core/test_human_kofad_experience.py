"""Human homepage copy, high-contrast login and accessible verified account methods."""
from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase


ROOT = Path(settings.BASE_DIR)


class HumanKofadExperienceContracts(SimpleTestCase):
    def test_homepage_uses_local_voice_without_false_claims(self):
        html = (ROOT / "marketplace/templates/marketplace/home.html").read_text(encoding="utf-8")
        self.assertIn('lang="tw">Yɛwɔ wo akyi.', html)
        self.assertIn("From your home to your shop.", html)
        self.assertIn("AKWAABA!", html)
        self.assertNotIn("Everything for today.", html)
        self.assertIn("Check the current price and availability", html)
        self.assertIn('href="/market/"', html)
        self.assertIn('href="/wholesale/"', html)

    def test_login_does_not_hide_inputs_or_sensitive_areas(self):
        template = (ROOT / "templates/login.html").read_text(encoding="utf-8")
        css = (ROOT / "static/staff-login-welcome.css").read_text(encoding="utf-8")
        self.assertIn("staff-login-welcome.css", template)
        self.assertIn("Username, mobile number or verified email", template)
        self.assertIn('name="password"', template)
        self.assertIn("Yɛnkɔ adwuma!", template)
        self.assertIn("input:focus-visible", css)
        self.assertIn("html[data-theme=\"dark\"]", css)
        self.assertIn("color: #173b43", css)
        self.assertNotIn("Everything for today.", template)

    def test_staff_and_customer_can_find_existing_linking_controls(self):
        staff = (ROOT / "templates/account.html").read_text(encoding="utf-8")
        account = (ROOT / "marketplace/templates/marketplace/account.html").read_text(encoding="utf-8")
        security = (ROOT / "marketplace/templates/marketplace/security.html").read_text(encoding="utf-8")
        settings_page = (ROOT / "templates/settings_center.html").read_text(encoding="utf-8")
        for name in ("staff-link-email", "staff-link-phone", "staff-link-google"):
            self.assertIn(name, staff)
        for name in ("customer-link-email", "customer-link-phone", "customer-link-google"):
            self.assertIn(name, security)
            self.assertIn(name, account)
        self.assertIn('name="action" value="email_verify"', staff)
        self.assertIn('name="action" value="recovery_verify"', staff)
        self.assertIn('name="action" value="phone_verify"', security)
        self.assertIn("My email, phone &amp; sign-in", settings_page)
        self.assertIn("verification", security.lower())
