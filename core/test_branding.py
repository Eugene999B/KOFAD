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

    def test_official_logo_keeps_emblem_and_both_text_lines_after_transparency(self):
        """Regression: previous simple threshold destroyed the actual KOFAD lettering."""
        from io import BytesIO
        from PIL import Image
        from core.brand_art import official_logo_bytes

        image = Image.open(BytesIO(official_logo_bytes())).convert("RGBA")
        self.assertGreaterEqual(image.width, 210)
        self.assertGreaterEqual(image.height, 200)
        self.assertLess(image.getpixel((0, 0))[3], 30)
        alpha = image.getchannel("A")
        def visible(rect):
            crop = alpha.crop(rect)
            return sum(1 for v in crop.getdata() if v >= 80)
        self.assertGreater(visible((15, 6, 201, 132)), 6000, "Compass emblem was erased")
        self.assertGreater(visible((0, 115, 219, 175)), 4000, "KOFAD name was erased")
        self.assertGreater(visible((0, 175, 219, 211)), 1200, "IMPEX and tagline were erased")
        favicon = Image.open(settings.BASE_DIR / "static" / "brand" / "favicon-96.png")
        self.assertEqual(favicon.size, (96, 96))
        self.assertEqual(favicon.mode, "RGBA")
