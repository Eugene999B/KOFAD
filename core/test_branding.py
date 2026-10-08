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
        official = root / "static" / "brand" / "kofad-official-logo.jpg"
        legacy = root / "static" / "brand" / "kofad-emblem.png"
        self.assertTrue(official.exists(), "Official KOFAD logo asset is missing.")
        self.assertFalse(legacy.exists(), "Legacy KOFAD emblem must be removed.")

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
            if "kofad-emblem.png" in content:
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(
            offenders,
            [],
            "Legacy KOFAD logo references remain in: " + ", ".join(offenders),
        )
