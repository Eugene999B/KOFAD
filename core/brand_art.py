"""Official identity and Unicode type for printed KOFAD records."""
import os

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
        pdfmetrics.registerFontFamily("KofadSans", normal="KofadSans", bold="KofadBold", italic="KofadSans", boldItalic="KofadBold")
        return "KofadSans", "KofadBold"
    return "Helvetica", "Helvetica-Bold"


def draw_mark(pdf, x, y, size):
    """Use the same official emblem as the homepage, without redrawing it."""
    path = settings.BASE_DIR / "static" / "brand" / "kofad-emblem.png"
    pdf.drawImage(ImageReader(str(path)), x, y, width=size, height=size,
                  preserveAspectRatio=True, anchor="c", mask="auto")
