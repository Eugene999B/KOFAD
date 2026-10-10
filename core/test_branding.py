from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class BrandingConsistencyTests(SimpleTestCase):
    def test_legacy_brand_names_are_absent_from_source(self):
        forbidden = (("KO" + "PEX").casefold(), ("KO" + "FEX").casefold())
        allowed_suffixes = {".py", ".html", ".js", ".css", ".md", ".txt", ".yml", ".yaml", ".json", ".svg"}
        skip_parts = {".git", "staticfiles", "node_modules", "__pycache__", ".venv", "browser-artifacts"}
        offenders = []
        root = Path(settings.BASE_DIR)
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
                continue
            if any(part in skip_parts for part in path.parts):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore").casefold()
            except OSError:
                continue
            if any(term in content for term in forbidden):
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(
            offenders,
            [],
            "Legacy company branding remains in: " + ", ".join(offenders),
        )


    def test_only_official_logo_asset_is_referenced(self):
        root = Path(settings.BASE_DIR)
        official = root / "static" / "brand" / "kofad-official-logo.svg"
        legacy_name = "kofad-" + "emblem.png"
        duplicate_name = "kofad-official-logo." + "jpg"
        legacy = root / "static" / "brand" / legacy_name
        duplicate = root / "static" / "brand" / duplicate_name
        self.assertTrue(official.exists(), "Official KOFAD logo asset is missing.")
        self.assertFalse(legacy.exists(), "Legacy KOFAD emblem must be removed.")
        self.assertFalse(duplicate.exists(), "A second KOFAD logo asset must not remain.")

        allowed_suffixes = {".py", ".html", ".js", ".css", ".md", ".txt", ".yml", ".yaml", ".json"}
        skip_parts = {".git", "staticfiles", "node_modules", "__pycache__", ".venv", "browser-artifacts"}
        offenders = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
                continue
            if any(part in skip_parts for part in path.parts):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if legacy_name in content or duplicate_name in content:
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(
            offenders,
            [],
            "Legacy or duplicate KOFAD logo references remain in: " + ", ".join(offenders),
        )

    def test_owner_uploaded_master_is_the_only_logo_source(self):
        from io import BytesIO
        from PIL import Image
        from core.brand_art import official_logo_bytes
        from scripts.prepare_logo import prepare_logo

        brand = Path(settings.BASE_DIR) / "static" / "brand"
        master = brand / "kofad-original-logo.png"
        self.assertTrue(master.exists(), "Owner-approved original PNG is required")
        self.assertGreater(master.stat().st_size, 10000)
        prepare_logo()
        generated = Image.open(BytesIO(official_logo_bytes())).convert("RGBA")
        original = Image.open(master).convert("RGBA")
        self.assertGreaterEqual(generated.width, original.width)
        self.assertGreaterEqual(generated.height, original.height)
        self.assertIsNotNone(generated.getchannel("A").getbbox())
        self.assertLess(generated.getpixel((0,0))[3], 10)
        self.assertEqual((brand / "favicon-96.png").exists(), True)
        self.assertEqual((brand / "favicon-48.png").exists(), True)
        self.assertEqual((brand / "favicon.ico").exists(), True)
        self.assertEqual((brand / "apple-touch-icon.png").exists(), True)
        svg = (brand / "kofad-official-logo.svg").read_text("utf-8")
        self.assertIn("data:image/png;base64,", svg)
        self.assertNotIn("data:image/jpeg;base64,", svg)
        self.assertNotIn("kofad-" + "emblem.png", svg)

    def test_favicon_is_uncropped_miniature_of_the_official_brand(self):
        from PIL import Image, ImageChops, ImageOps
        from scripts.prepare_logo import prepare_logo

        brand = Path(settings.BASE_DIR) / "static" / "brand"
        prepare_logo()
        with Image.open(brand / "kofad-logo-transparent.png") as display:
            display = display.convert("RGBA")
            complete = display.crop(display.getchannel("A").getbbox() or (0, 0, *display.size))
        for size in (48, 96, 180):
            with self.subTest(size=size), Image.open(brand / f"favicon-{size}.png") as actual_image:
                actual = actual_image.convert("RGBA")
                self.assertEqual(actual.size, (size, size))
                safe = max(1, round(size * .92))
                mark = ImageOps.contain(complete, (safe, safe), Image.Resampling.LANCZOS)
                expected = Image.new("RGBA", (size, size))
                expected.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
                self.assertIsNone(ImageChops.difference(actual, expected).getbbox())

    def test_all_site_shells_link_to_one_favicon_family(self):
        root = Path(settings.BASE_DIR)
        snippet = (root / "templates" / "favicon_links.html").read_text("utf-8")
        for filename in ("favicon-32.png", "favicon-48.png", "favicon-96.png",
                         "apple-touch-icon.png", "/favicon.ico"):
            self.assertIn(filename, snippet)
        for shell in ("templates/base.html", "templates/auth_base.html",
                      "marketplace/templates/marketplace/base.html"):
            self.assertIn('include "favicon_links.html"', (root / shell).read_text("utf-8"))

    def test_favicon_endpoint_delivers_correct_type(self):
        from django.test import Client
        from django.test import override_settings
        with override_settings(ALLOWED_HOSTS=["kofadimpex.com", "testserver"]):
            response = Client().get("/favicon.ico", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/x-icon")
        self.assertIn("public", response["Cache-Control"])
        self.assertNotIn("X-Robots-Tag", response)
        response.close()

    def test_customer_auth_templates_have_branded_google_action(self):
        from pathlib import Path
        root = Path(settings.BASE_DIR)
        for template in ("access.html", "login.html"):
            path = root / "marketplace" / "templates" / "marketplace" / template
            source = path.read_text("utf-8")
            self.assertIn("kofad-google-button", source)
            self.assertIn("google_customer_login", source)
            self.assertIn("google-colour-mark", source)
            self.assertIn("customer-auth-premium.css", source)
