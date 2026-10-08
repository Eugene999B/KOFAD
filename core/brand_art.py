"""Official identity and Unicode type for printed KOFAD records."""
import base64
import os
import re
from functools import lru_cache
from io import BytesIO

from django.conf import settings
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


def print_fonts():
    root = "/usr/share/fonts/truetype/dejavu"
    for name, file in (("KofadSans", "DejaVuSans.ttf"), ("KofadBold", "DejaVuSans-Bold.ttf")):
        path = os.path.join(root, file)
        if name not in pdfmetrics.getRegisteredFontNames() and os.path.exists(path):
            pdfmetrics.registerFont(TTFont(name, path))
    if "KofadSans" in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFontFamily(
            "KofadSans", normal="KofadSans", bold="KofadBold",
            italic="KofadSans", boldItalic="KofadBold",
        )
        return "KofadSans", "KofadBold"
    return "Helvetica", "Helvetica-Bold"


@lru_cache(maxsize=1)
def _official_logo_bytes():
    """Read the one canonical SVG asset and recover its embedded official JPEG."""
    path = settings.BASE_DIR / "static" / "brand" / "kofad-official-logo.svg"
    source = path.read_text(encoding="utf-8")
    match = re.search(r'data:image/jpeg;base64,([^"\']+)', source)
    if not match:
        raise RuntimeError("Official KOFAD logo SVG does not contain its embedded image.")
    try:
        raw = base64.b64decode(match.group(1), validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Official KOFAD logo image data is invalid.") from exc
    if not (raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")):
        raise RuntimeError("Official KOFAD logo image data is not a JPEG.")
    return raw


def draw_mark(pdf, x, y, size):
    """Embed the exact official KOFAD logo source on printed records."""
    pdf.drawImage(
        ImageReader(BytesIO(_official_logo_bytes())),
        x, y, width=size, height=size,
        preserveAspectRatio=True, anchor="c", mask="auto",
    )
