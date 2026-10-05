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
