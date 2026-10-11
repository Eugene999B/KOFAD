"""Guard public Market metadata and customer-contact context across deployments."""
from unittest.mock import patch

from django.test import TestCase


class MarketDiscoveryContextRegressionTests(TestCase):
    def test_public_home_is_indexable_with_canonical_metadata(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["seo_indexable"])
        self.assertIn("/static/brand/", response.context["seo_image"])
        self.assertTrue(response.context["canonical_url"].endswith("/"))
        self.assertContains(response, 'name="robots" content="index,follow,max-image-preview:large"')
        self.assertContains(response, 'rel="canonical"')

    def test_market_has_separate_commerce_identity_and_discovery_data(self):
        response = self.client.get("/market/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["seo_indexable"])
        self.assertIn("KOFAD Market", response.context["seo_title"])
        self.assertIn('"name":"KOFAD Market"', response.context["seo_json_ld"])
        self.assertIn("market.kofadimpex.com/market/", response.context["canonical_url"])
        self.assertIn("customer_service_contacts", response.context)
        self.assertIn("native_customer_app_released", response.context)
        self.assertIn("native_customer_app_android", response.context)

    def test_customer_login_and_filtered_catalogue_are_not_indexed(self):
        login = self.client.get("/market/account/login/")
        self.assertEqual(login.status_code, 200)
        self.assertFalse(login.context["seo_indexable"])
        self.assertContains(login, 'name="robots" content="noindex,nofollow,noarchive"')
        filtered = self.client.get("/market/?q=filter")
        self.assertEqual(filtered.status_code, 200)
        self.assertFalse(filtered.context["seo_indexable"])
        self.assertEqual(filtered.context["canonical_url"], "")

    @patch("core.google_oauth.enabled", return_value=True)
    def test_google_readiness_survives_seo_recovery(self, readiness):
        response = self.client.get("/market/account/login/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["google_ready"])
