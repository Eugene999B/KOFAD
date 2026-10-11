"""Official image source for KOFAD business PDF/print documents."""
import os
from functools import lru_cache
from django.conf import settings
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from io import BytesIO


def print_fonts():
    root="/usr/share/fonts/truetype/dejavu"
    for name,file in (("KofadSans","DejaVuSans.ttf"),("KofadBold","DejaVuSans-Bold.ttf")):
        path=os.path.join(root,file)
        if name not in pdfmetrics.getRegisteredFontNames() and os.path.exists(path):
            pdfmetrics.registerFont(TTFont(name,path))
    if "KofadSans" in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFontFamily("KofadSans",normal="KofadSans",bold="KofadBold",italic="KofadSans",boldItalic="KofadBold")
        return "KofadSans","KofadBold"
    return "Helvetica","Helvetica-Bold"


@lru_cache(maxsize=1)
def official_logo_bytes():
    """PNG generated from the owner's uploaded master, never legacy artwork."""
    path=settings.BASE_DIR / "static" / "brand" / "kofad-logo-transparent.png"
    if not path.exists():
        # Developer/test environments may not have run prepare_logo yet.
        from scripts.prepare_logo import prepare_logo
        prepare_logo()
    payload=path.read_bytes()
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Official image is not PNG.")
    return payload


def draw_mark(pdf,x,y,size):
    """Embed the new official logo in receipts, statements and PDF documents."""
    pdf.drawImage(ImageReader(BytesIO(official_logo_bytes())),x,y,width=size,height=size,
                  preserveAspectRatio=True,anchor="c",mask="auto")
