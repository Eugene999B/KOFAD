"""Public discovery regression tests: no fabricated metadata or private indexing."""
import json
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings

from marketplace.discovery import description, page_title, structured_data


@override_settings(PUBLIC_SITE_ORIGIN="https://kofadimpex.com",
                   MARKET_SITE_ORIGIN="https://market.kofadimpex.com")
class DiscoverySchemaTests(SimpleTestCase):
    def _data(self, path, **kw):
        origin = "https://market.kofadimpex.com" if path.startswith("/market/") else "https://kofadimpex.com"
        return json.loads(structured_data(path, origin + path, "Title", "Description", **kw))

    def test_company_home_uses_official_organisation_and_website_name(self):
        graph = self._data("/")["@graph"]
        website = next(obj for obj in graph if obj["@type"] == "WebSite")
        organisation = next(obj for obj in graph if obj["@type"] == "Organization")
        self.assertEqual(website["name"], "KOFAD IMPEX ENTERPRISE")
        self.assertEqual(website["url"], "https://kofadimpex.com/")
        self.assertEqual(organisation["areaServed"]["name"], "Ghana")
        self.assertNotIn("aggregateRating", str(graph))
        self.assertNotIn("sameAs", organisation)

    def test_market_has_independent_website_name_and_correct_breadcrumb(self):
        graph = self._data("/market/")["@graph"]
        website = next(obj for obj in graph if obj["@type"] == "WebSite")
        self.assertEqual(website["name"], "KOFAD Market")
        self.assertEqual(website["url"], "https://market.kofadimpex.com/market/")
        self.assertEqual(website["@id"], "https://market.kofadimpex.com/market/#website")
        market_page = next(obj for obj in graph if obj["@type"] == "CollectionPage")
        self.assertEqual(market_page["isPartOf"]["@id"], website["@id"])
        category = self._data("/market/categories/home-cleaning/", category_name="Home & Cleaning")["@graph"]
        breadcrumb = next(obj for obj in category if obj["@type"] == "BreadcrumbList")
        self.assertEqual(breadcrumb["itemListElement"][-1]["name"], "Home & Cleaning")

    def test_product_schema_is_real_stock_and_escapes_script_breakouts(self):
        item = SimpleNamespace(
            display_name="Dinner plates", description="Value </script><img src=x>",
            pk=42, image_data=b"real-image", market_price_value="34.50",
            available_sell_qty=0,
            product=SimpleNamespace(category="Dinnerware", sku="DIN-42"),
        )
        output = structured_data(
            "/market/products/42/", "https://market.kofadimpex.com/market/products/42/",
            "Dinner plates", "Ceramic dinner plates", listing=item)
        self.assertNotIn("</script>", output)
        graph = json.loads(output)["@graph"]
        product = next(obj for obj in graph if obj["@type"] == "Product")
        self.assertEqual(product["offers"]["priceCurrency"], "GHS")
        self.assertEqual(product["offers"]["price"], "34.50")
        self.assertTrue(product["offers"]["availability"].endswith("OutOfStock"))
        self.assertTrue(product["image"].endswith("/market/products/42/image/large/"))
        self.assertNotIn("brand", product)

    def test_market_favicon_is_public_on_its_own_host(self):
        from django.test import RequestFactory
        from marketplace.seo import favicon

        # Direct streaming response test avoids closing TestCase's atomic
        # database connection when Django's request_finished signal fires.
        response = favicon(RequestFactory().get("/favicon.ico", HTTP_HOST="market.kofadimpex.com"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/x-icon")
        self.assertNotIn("X-Robots-Tag", response)
        response.close()

    def test_unique_titles_and_search_intent_are_truthful(self):
        self.assertIn("Wholesale", page_title("/wholesale/", "Bulk supply"))
        self.assertIn("Download", page_title("/apps/", "Get app"))
        self.assertIn("FAQs", page_title("/faq/", "Help"))
        self.assertIn("Contact", page_title("/contact/", "Enquiries"))
        self.assertIn("order support", description("/contact/"))
        self.assertIn("Ghana", description("/market/"))
        self.assertNotIn("worldwide shipping", description("/wholesale/").lower())


@override_settings(ALLOWED_HOSTS=["kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com"])
class PublicDiscoveryPagesTests(TestCase):
    def test_company_home_offers_clear_crawlable_sitelink_navigation(self):
        response = self.client.get("/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Explore KOFAD pages"')
        self.assertContains(response, "KOFAD IMPEX ENTERPRISE")
        for path in (
            "/market/", "/wholesale/", "/apps/", "/delivery/",
            "/faq/", "/contact/", "/about/", "/returns-policy/",
        ):
            with self.subTest(path=path):
                self.assertContains(response, f'href="{path}"')

    def test_public_pages_load_shared_light_theme_contrast_guard(self):
        # The same final CSS layer must load on home, company guidance and
        # Market pages; don't fix just the screenshot's footer.
        for path, hostname in (
            ("/", "kofadimpex.com"),
            ("/contact/", "kofadimpex.com"),
            ("/faq/", "kofadimpex.com"),
            ("/market/", "market.kofadimpex.com"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST=hostname)
                self.assertEqual(response.status_code, 200)
                # ManifestStaticFilesStorage fingerprints production assets.
                # Test stable filename stems and cascade order instead.
                self.assertContains(response, "kofad-light-contrast.")
                self.assertLess(
                    response.content.index(b"kofad-atelier."),
                    response.content.index(b"kofad-light-contrast."),
                )

    def test_company_home_has_compact_footer_without_oversized_navy_band(self):
        homepage = self.client.get("/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(homepage.status_code, 200)
        self.assertContains(homepage, 'class="kfd-compact-footer"')
        self.assertContains(homepage, 'aria-label="Company information and policies"')
        self.assertContains(homepage, "Everyday shopping and wholesale supply in Ghana.")
        self.assertNotContains(homepage, 'class="market-footer commerce-footer"')
        for important_path in ("/contact/", "/privacy/", "/terms/", "/market/"):
            with self.subTest(path=important_path):
                self.assertContains(homepage, f'href="{important_path}"')

    def test_other_public_pages_retain_their_existing_company_footer(self):
        response = self.client.get("/about/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="market-footer commerce-footer"')
        self.assertNotContains(response, 'class="kfd-compact-footer"')

    def test_customer_app_page_is_public_and_describes_real_downloads(self):
        response = self.client.get("/apps/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'rel="canonical" href="https://kofadimpex.com/apps/"')
        self.assertContains(response, "Download KOFAD Market App")
        self.assertContains(response, '"isPartOf":{"@id":"https://kofadimpex.com/#website"}')
        self.assertNotContains(response, 'content="noindex,nofollow,noarchive"')

    def test_wholesale_guide_indexable_on_company_domain(self):
        response = self.client.get("/wholesale/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "wholesale quantities")
        self.assertContains(response, 'rel="canonical" href="https://kofadimpex.com/wholesale/"')
        self.assertContains(response, "application/ld+json")

    def test_market_catalog_has_independent_seo_identity(self):
        response = self.client.get("/market/", HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="description"')
        self.assertContains(response, '"name":"KOFAD Market"')
        self.assertContains(response, 'href="/favicon.ico"')
        self.assertContains(response, "<h1>All products</h1>", html=True)

    def test_filtered_search_is_not_indexed(self):
        response = self.client.get("/market/?q=example", HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'content="noindex,nofollow,noarchive"')
        self.assertNotContains(response, 'rel="canonical"')

    def test_category_landing_rejects_unpublished_departments(self):
        response = self.client.get("/market/categories/not-a-real-department/",
                                   HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(response.status_code, 404)

