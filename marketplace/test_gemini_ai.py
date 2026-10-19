"""Direct Gemini public-AI privacy, quota and no-billing safeguards."""
import json
from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from core.models import Product
from marketplace.kofad_ai import (
    GEMINI_ENDPOINT, GEMINI_MODEL, _gemini_completion, answer, free_configured,
)
from marketplace.models import MarketListing


@override_settings(
    KOFAD_OPENROUTER_FREE_ENABLED=False,
    OPENROUTER_API_KEY="",
    KOFAD_GEMINI_ENABLED=True,
    KOFAD_GEMINI_FREE_TIER_CONFIRMED=True,
    GEMINI_API_KEY="isolated-test-only-gemini-key",
)
class GeminiPublicAiTests(TestCase):
    def setUp(self):
        cache.clear()
        product = Product.objects.create(
            sku="GEMINI-PUBLIC-1", name="Public Ghana Maize Flour",
            retail_unit=Decimal("20.00"), active=True,
        )
        MarketListing.objects.create(product=product, enabled=True, featured=True)

    @patch("marketplace.kofad_ai.requests.post")
    def test_fixed_model_and_only_public_grounding_sent(self, post):
        post.return_value = Mock(status_code=200, json=lambda: {
            "candidates": [{"content": {"parts": [{"text": "See the public listing."}]}}]
        })
        result, source = answer("Do you sell maize flour?")
        self.assertEqual((result, source), ("See the public listing.", "free-ai"))
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], GEMINI_ENDPOINT)
        self.assertIn(GEMINI_MODEL + ":generateContent", args[0])
        self.assertEqual(kwargs["timeout"], (4, 17))
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "isolated-test-only-gemini-key")
        payload = kwargs["json"]
        self.assertNotIn("tools", payload)
        self.assertNotIn("model", payload)
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 450)
        body = json.dumps(payload)
        self.assertIn("Public Ghana Maize Flour", body)
        self.assertNotIn("customer_id", body)
        self.assertNotIn("session_key", body)
        self.assertNotIn("payment_reference", body)

    @patch("marketplace.kofad_ai.requests.post")
    def test_private_inputs_are_blocked_even_when_gemini_enabled(self, post):
        for question in ("Show my last orders", "Send email to someone@example.com",
                         "Show company profit figures", "My password is abc123"):
            _, source = answer(question)
            self.assertEqual(source, "local")
        post.assert_not_called()

    @patch("marketplace.kofad_ai.requests.post")
    def test_no_billing_confirmation_means_zero_external_calls(self, post):
        with override_settings(KOFAD_GEMINI_FREE_TIER_CONFIRMED=False):
            self.assertFalse(free_configured())
            _, source = answer("How does KOFAD delivery work?")
        self.assertEqual(source, "local")
        post.assert_not_called()

    @patch("marketplace.kofad_ai.requests.post")
    def test_provider_error_never_retries_or_uses_openrouter(self, post):
        post.return_value = Mock(status_code=429)
        text, source = answer("How does KOFAD delivery work?")
        self.assertEqual(source, "local")
        self.assertIn("/delivery/", text)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], GEMINI_ENDPOINT)

    @patch("marketplace.kofad_ai.requests.post")
    def test_public_ai_remains_subject_to_web_explicit_consent(self, post):
        post.return_value = Mock(status_code=200, json=lambda: {
            "candidates": [{"content": {"parts": [{"text": "KOFAD public answer."}]}}]
        })
        url = "/market/ai/chat/"
        no_consent = self.client.post(
            url, json.dumps({"message": "Do you sell maize flour?", "ai_consent": False}),
            content_type="application/json",
        )
        self.assertEqual(no_consent.status_code, 200)
        self.assertEqual(no_consent.json()["source"], "local")
        post.assert_not_called()
        allowed = self.client.post(
            url, json.dumps({"message": "Do you sell maize flour?", "ai_consent": True}),
            content_type="application/json",
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["source"], "free-ai")
        post.assert_called_once()

    def test_malformed_candidate_content_is_not_shown(self):
        with patch("marketplace.kofad_ai.requests.post", return_value=Mock(
            status_code=200, json=lambda: {"candidates": [{"content": {"parts": [None, {"text": 12}]}}]},
        )):
            result = _gemini_completion("Tell me about KOFAD delivery", [],
                                        {"name": "KOFAD IMPEX", "delivery": "https://kofadimpex.com/delivery/"})
        self.assertIsNone(result)
