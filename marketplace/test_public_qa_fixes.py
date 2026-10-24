"""Production QA fixes: guest cart, showcase verification and contact accuracy."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import override_settings

from core.models import Company
from marketplace import services
from marketplace.models import OnlineOrder
from marketplace.tests import MarketFixtures


class PublicQaFixes(MarketFixtures):
    def test_guest_can_add_and_update_cart_but_not_checkout_anonymously(self):
        response = self.client.post(
            f"/market/cart/add/{self.listing.pk}/",
            {"quantity": "2", "next": "/market/cart/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/market/cart/")
        self.assertEqual(self.client.session["market_cart"], {str(self.listing.pk): 2})
        page = self.client.get("/market/cart/")
        self.assertContains(page, "Sign in to checkout")
        self.assertContains(page, "GHS 200.00")
        updated = self.client.post("/market/cart/update/", {
            "qty_" + str(self.listing.pk): "3",
        })
        self.assertEqual(updated.status_code, 302)
        self.assertEqual(self.client.session["market_cart"], {str(self.listing.pk): 3})
        login = self.client.get("/market/checkout/")
        self.assertEqual(login.status_code, 302)
        self.assertEqual(login["Location"], "/market/access/")
        self.assertEqual(self.client.session["market_after_login"], "/market/checkout/")
        self.assertEqual(self.client.session["market_cart"], {str(self.listing.pk): 3})
        self.assertFalse(OnlineOrder.objects.exists())

    def test_guest_cart_limits_untrusted_session_data(self):
        self.client.post("/market/cart/update/", {
            "qty_²": "1", "qty_" + "9" * 500: "1",
            "qty_" + str(self.listing.pk): "5",
        })
        self.assertEqual(self.client.session["market_cart"], {str(self.listing.pk): 5})
        self.client.post(f"/market/cart/add/{self.listing.pk}/", {
            "quantity": "1", "next": "https://not-kofad.example/steal",
        })
        self.assertEqual(self.client.session["market_cart"], {str(self.listing.pk): 6})

    @override_settings(KOFAD_REQUIRE_SHOWCASE_VERIFICATION=True,
                       KOFAD_VERIFIED_SHOWCASE_SKUS=())
    def test_showcase_item_is_not_sold_or_priced_until_verified(self):
        self.product.sku = "SHOW-001"
        self.product.save(update_fields=["sku"])
        listing = self.client.get(f"/market/products/{self.listing.pk}/")
        self.assertContains(listing, "Price pending verification")
        self.assertContains(listing, "Stock verification required")
        self.assertNotContains(listing, "GHS 100.00")
        catalog = self.client.get("/market/")
        self.assertContains(catalog, "Price to be confirmed")
        self.assertContains(catalog, "stock and price checks")
        blocked = self.client.post(f"/market/cart/add/{self.listing.pk}/", {"quantity": 1})
        self.assertEqual(blocked.status_code, 302)
        self.assertNotIn("market_cart", self.client.session)
        with self.assertRaises(ValidationError):
            services.create_order(self.customer, {str(self.listing.pk): 1}, {
                "fulfilment": "pickup", "recipient_name": self.customer.full_name,
                "phone": self.customer.phone, "email": self.customer.email,
            })
        self.assertFalse(OnlineOrder.objects.exists())

    @override_settings(KOFAD_REQUIRE_SHOWCASE_VERIFICATION=True,
                       KOFAD_VERIFIED_SHOWCASE_SKUS=("SHOW-001",))
    def test_explicit_showcase_verification_unlocks_specific_item(self):
        self.product.sku = "SHOW-001"
        self.product.save(update_fields=["sku"])
        response = self.client.post(
            f"/market/cart/add/{self.listing.pk}/", {"quantity": 1},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["market_cart"][str(self.listing.pk)], 1)
        self.assertContains(self.client.get(f"/market/products/{self.listing.pk}/"),
                            "GHS 100.00")

    def test_public_contact_dedup_and_placeholder_cleanup(self):
        company = Company.objects.first()
        company.phone = "+233240000000"
        company.secondary_phone = "+233240000000"
        company.whatsapp_phone = "+233243000000"
        company.save(update_fields=["phone", "secondary_phone", "whatsapp_phone"])
        home = self.client.get("/")
        contact = self.client.get("/contact/")
        for page in [home, contact]:
            self.assertNotContains(page, "+233240000000")
            self.assertNotContains(page, "+233243000000")
        self.assertContains(contact, "Send an enquiry or feedback")
        company.phone = "+233241112222"
        company.secondary_phone = "+233241112222"
        company.save(update_fields=["phone", "secondary_phone"])
        contact = self.client.get("/contact/")
        self.assertEqual(contact.content.decode().count("tel:+233241112222"), 1)

    def test_delivery_displays_configured_price_without_making_it_up(self):
        company = Company.objects.first()
        company.delivery_enabled = True
        company.delivery_pricing_mode = "flat"
        company.delivery_flat_fee = Decimal("12.50")
        company.save(update_fields=["delivery_enabled", "delivery_pricing_mode", "delivery_flat_fee"])
        response = self.client.get("/delivery/")
        self.assertContains(response, "GHS 12.50")
        self.assertContains(response, "Coverage and delivery eligibility")

    def test_mobile_consent_does_not_cover_top_navigation(self):
        from pathlib import Path
        from django.conf import settings
        css = (Path(settings.BASE_DIR) / "static/cookie-consent.css").read_text()
        self.assertIn("bottom:max(8px,env(safe-area-inset-bottom))", css)
        self.assertIn("top:auto", css)
