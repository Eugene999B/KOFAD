"""Regression checks for CSP-safe staff confirmation and wallet status UI.

The tests do not request money services, submit forms, or contact Hubtel.
"""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from core.middleware import AccessMiddleware


class BrowserCspContracts(SimpleTestCase):
    def test_default_csp_keeps_scripts_external_only(self):
        request = RequestFactory().get("/debts/")
        request.user = AnonymousUser()
        request.session = {}
        response = AccessMiddleware(lambda _: HttpResponse("ok"))(request)
        policy = response["Content-Security-Policy"]
        scripts = policy.split("script-src ", 1)[1].split(";", 1)[0]
        styles = policy.split("style-src ", 1)[1].split(";", 1)[0]
        self.assertIn("'self'", scripts)
        self.assertNotIn("'unsafe-inline'", scripts)
        self.assertNotIn("'unsafe-eval'", scripts)
        # Legacy template layout attributes must remain usable while their
        # JS handlers are moved into external scripts.
        self.assertIn("'unsafe-inline'", styles)

    def test_sensitive_staff_forms_use_external_confirmation_handlers(self):
        root = Path(settings.BASE_DIR)
        cases = {
            "templates/debts.html": "data-confirm-click",
            "templates/payroll_period.html": "data-confirm-submit",
            "templates/communications.html": "data-confirm-submit",
            "templates/customer_care_settings.html": "data-confirm-submit",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                html = (root / path).read_text()
                self.assertIn(expected, html)
                self.assertNotIn("onclick=", html)
                self.assertNotIn("onsubmit=", html)
        base = (root / "templates/base.html").read_text()
        self.assertIn("staff-confirm-actions.js", base)
        script = (root / "static/staff-confirm-actions.js").read_text()
        self.assertIn('addEventListener("submit"', script)
        self.assertIn('addEventListener("click"', script)
        self.assertIn("event.preventDefault()", script)
        self.assertNotIn("eval(", script)

    def test_wallet_preapproval_polling_uses_read_only_external_script(self):
        root = Path(settings.BASE_DIR)
        html = (root / "hubtel_direct/templates/hubtel_direct/sale_intent_wait.html").read_text()
        script = (root / "hubtel_direct/static/hubtel_direct/sale-intent-wait.js").read_text()
        self.assertIn("data-v2-status-url", html)
        self.assertIn("sale-intent-wait.js", html)
        self.assertNotIn("<script>", html)
        self.assertIn('fetch(url, { credentials: "same-origin", cache: "no-store" })', script)
        self.assertNotIn("fetch(url, { method: \"POST\"", script)

    def test_offline_retry_never_uses_blocked_inline_javascript(self):
        from core import pwa
        import inspect
        source = inspect.getsource(pwa.offline)
        self.assertIn('<form method="get">', source)
        self.assertNotIn("onclick=", source)
