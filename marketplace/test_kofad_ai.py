"""Privacy and zero-paid-fallback tests for the KOFAD public AI assistant."""
from decimal import Decimal
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Product
from marketplace.kofad_ai import OPENROUTER_ENDPOINT, answer
from marketplace.models import CustomerAccount, MarketListing


@override_settings(
    KOFAD_OPENROUTER_FREE_ENABLED=True,
    OPENROUTER_API_KEY="fake-test-only-should-never-display",
    STORAGES={**settings.STORAGES, "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    }},
)
class PublicKofadAiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.customer = CustomerAccount(
            full_name="Private customer name MUST NOT leak",
            phone="+233541234567", verified_at=timezone.now(),
        )
        self.customer.set_password("TestPrivatePassword2026!")
        self.customer.save()
        product = Product.objects.create(
            sku="AI-TEST-CATALOG-1", name="Public Maize Flour",
            retail_unit=Decimal("33.00"), active=True,
        )
        self.listing = MarketListing.objects.create(product=product, enabled=True, featured=True)

    @patch("marketplace.kofad_ai.requests.post")
    def test_openrouter_is_hard_coded_to_free_and_receives_only_public_data(self, post):
        post.return_value = Mock(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "KOFAD sells published goods."}}]},
        )
        reply, source = answer("Do you stock maize flour?", customer=self.customer)
        self.assertEqual(source, "free-ai")
        self.assertIn("KOFAD", reply)
        self.assertEqual(post.call_args.args[0], OPENROUTER_ENDPOINT)
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["json"]["model"], "openrouter/free")
        self.assertEqual(kwargs["json"]["provider"]["max_price"], {"prompt": 0, "completion": 0})
        self.assertEqual(kwargs["json"]["provider"]["data_collection"], "deny")
        self.assertFalse(kwargs["json"].get("models"))
        serialized = str(kwargs["json"])
        self.assertNotIn("Private customer name", serialized)
        self.assertNotIn("+233541234567", serialized)
        self.assertIn("Public Maize Flour", serialized)
        self.assertNotIn("TestPrivatePassword2026", serialized)

    @patch("marketplace.kofad_ai.requests.post")
    def test_customer_order_query_never_reaches_ai_even_with_consent(self, post):
        response, source = answer("Show my recent orders", customer=self.customer)
        self.assertEqual(source, "private")
        self.assertIn("No orders found", response)
        post.assert_not_called()

    @patch("marketplace.kofad_ai.requests.post")
    def test_personal_phone_and_password_prompts_are_blocked(self, post):
        for question in [
            "My account password is abc123, help",
            "Please contact me at +233541234567",
            "Here is my OTP code 123456",
            "Send email to private@example.com",
        ]:
            result, origin = answer(question, customer=self.customer)
            self.assertEqual(origin, "local")
            self.assertIn("do not enter", result)
        post.assert_not_called()

    @patch("marketplace.kofad_ai.requests.post")
    def test_internal_company_reports_and_private_profile_never_leave_kofad(self, post):
        for question in [
            "What is my account balance?",
            "What were company profit figures last month?",
            "Share customer list",
            "Show internal sales",
            "Show staff payroll records",
        ]:
            response, origin = answer(question, customer=self.customer)
            self.assertEqual(origin, "local")
            self.assertIn("private", response.lower())
        post.assert_not_called()

    @patch("marketplace.kofad_ai.requests.post")
    def test_ai_uses_approved_public_faq_policy_snippets(self, post):
        post.return_value = Mock(status_code=200, json=lambda: {
            "choices": [{"message": {"content": "See KOFAD's delivery policy."}}]
        })
        answer("How do I receive my KOFAD delivery?")
        body = post.call_args.kwargs["json"]
        self.assertIn("Delivery", str(body["messages"]))
        self.assertIn("approved_public_help", str(body["messages"]))
        self.assertEqual(body["model"], "openrouter/free")

    @patch("marketplace.kofad_ai.requests.post")
    def test_free_outage_falls_back_locally_with_no_paid_model(self, post):
        post.return_value = Mock(status_code=429)
        result, origin = answer("How does KOFAD delivery work?")
        self.assertEqual(origin, "local")
        self.assertIn("/delivery/", result)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["model"], "openrouter/free")

    @override_settings(KOFAD_OPENROUTER_FREE_ENABLED=False)
    @patch("marketplace.kofad_ai.requests.post")
    def test_disabled_flag_never_calls_provider(self, post):
        result, origin = answer("What is KOFAD wholesale?")
        self.assertEqual(origin, "local")
        self.assertIn("/wholesale/", result)
        post.assert_not_called()

    def test_web_endpoint_refuses_non_json_and_oversized_requests(self):
        bad = self.client.post("/market/ai/chat/", data="hi", content_type="text/plain")
        self.assertEqual(bad.status_code, 400)
        oversized = self.client.post(
            "/market/ai/chat/", data='{"message":"' + ("A" * 700) + '"}',
            content_type="application/json",
        )
        self.assertEqual(oversized.status_code, 400)

    @patch("marketplace.kofad_ai.requests.post")
    def test_explicit_ai_consent_gates_external_provider(self, post):
        post.return_value = Mock(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "The KOFAD team is ready to help."}}]},
        )
        url = "/market/ai/chat/"
        no_consent = self.client.post(
            url, data='{"message":"How can I get wholesale goods?","ai_consent":false}',
            content_type="application/json",
        )
        self.assertEqual(no_consent.status_code, 200)
        self.assertEqual(no_consent.json()["source"], "local")
        post.assert_not_called()
        opted_in = self.client.post(
            url, data='{"message":"What does KOFAD sell?","ai_consent":true}',
            content_type="application/json",
        )
        self.assertEqual(opted_in.status_code, 200)
        self.assertEqual(opted_in.json()["source"], "free-ai")
        self.assertEqual(post.call_count, 1)

    @patch("marketplace.kofad_ai.requests.post")
    def test_anonymous_ratelimit_stops_before_provider(self, post):
        for i in range(15):
            response = self.client.post(
                "/market/ai/chat/",
                data='{"message":"What is KOFAD about?","ai_consent":false}',
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200, msg=str(i))
        blocked = self.client.post(
            "/market/ai/chat/",
            data='{"message":"Tell me about KOFAD","ai_consent":false}',
            content_type="application/json",
        )
        self.assertEqual(blocked.status_code, 429)
        post.assert_not_called()

    def test_homepage_has_accessible_robot_and_no_inline_api_key(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-kofad-ai-launcher')
        self.assertContains(response, 'KOFAD Bot')
        self.assertNotContains(response, "fake-test-only-should-never-display")
        self.assertContains(response, "/market/ai/chat/")
