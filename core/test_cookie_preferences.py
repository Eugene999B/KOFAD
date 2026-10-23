from django.test import RequestFactory, SimpleTestCase, override_settings
from .cookie_preferences import context


@override_settings(KOFAD_GA4_MEASUREMENT_ID="G-ABC12345", ALLOWED_HOSTS=[".kofadimpex.com"])
class CookieAnalyticsBoundaries(SimpleTestCase):
    def test_analytics_is_only_available_on_public_non_identifying_pages(self):
        factory = RequestFactory()
        for host, path, expected in [
            ("market.kofadimpex.com", "/market/", "G-ABC12345"),
            ("kofadimpex.com", "/about/", "G-ABC12345"),
            ("market.kofadimpex.com", "/market/orders/private-reference/", ""),
            ("market.kofadimpex.com", "/market/account/", ""),
            ("staff.kofadimpex.com", "/", ""),
        ]:
            with self.subTest(host=host, path=path):
                request = factory.get(path, HTTP_HOST=host)
                self.assertEqual(context(request)["cookie_analytics_id"], expected)

    @override_settings(KOFAD_GA4_MEASUREMENT_ID='G-INVALID"><script>')
    def test_invalid_measurement_id_is_never_rendered(self):
        request = RequestFactory().get("/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(context(request)["cookie_analytics_id"], "")


class CookieExperienceContracts(SimpleTestCase):
    """Consent controls stay optional; staff sign-in stays fully reachable."""

    def test_privacy_choices_reopen_only_on_privacy_page(self):
        from pathlib import Path
        from django.conf import settings

        template = (Path(settings.BASE_DIR) / "templates/cookie_choices.html").read_text()
        self.assertIn("{% if request.path == '/privacy/' %}", template)
        self.assertIn("data-cookie-open", template)
        self.assertIn("data-cookie-reject", template)
        self.assertIn("data-cookie-accept", template)
        self.assertIn("data-cookie-options", template)
        self.assertNotIn("kofad-cookie-footer", template)
        self.assertIn('Essential cookies keep sign-in', template)

    def test_cookie_script_persists_choices_and_hides_panel(self):
        from pathlib import Path
        from django.conf import settings

        script = (Path(settings.BASE_DIR) / "static/cookie-consent.js").read_text()
        self.assertIn('panel.hidden = true;', script)
        self.assertIn('openers.forEach(button => button.setAttribute("aria-expanded", "false"))', script)
        self.assertIn('Max-Age=15552000; Path=/; SameSite=Lax', script)
        self.assertIn('"__Host-kofad_consent"', script)
        self.assertNotIn('preventDefault()', script)
        self.assertIn('save(false, false)', script)
        self.assertIn('save(true, analyticsAvailable)', script)

    def test_staff_login_never_clips_form_or_requires_cookie_consent(self):
        from pathlib import Path
        from django.conf import settings

        root = Path(settings.BASE_DIR)
        layout = (root / "static/staff-login-welcome.css").read_text()
        login = (root / "templates/login.html").read_text()
        for rule in ("height:auto!important", "max-height:none!important",
                     "overflow-y:auto!important", "overflow:visible!important"):
            self.assertIn(rule, layout)
        self.assertIn('class="premium-login-form"', login)
        self.assertIn("Sign in to workspace", login)
        self.assertIn('data-cookie-panel', (root / "templates/cookie_choices.html").read_text())
        self.assertNotIn('data-cookie-accept', login)
