"""Shared vector identity and Unicode type for printed KOFAD records."""
import os

from reportlab.lib import colors
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
    # Geometry matches the approved KI monogram in static/brand/v4.
    pdf.saveState()
    pdf.translate(x, y + size)
    pdf.scale(size / 420, -size / 420)
    ink, copper = colors.HexColor("#171717"), colors.HexColor("#B66B45")
    pdf.setStrokeColor(ink)
    pdf.setLineWidth(12)
    for points in (
        [(92,28),(326,28),(392,57),(392,94),(392,144)],
        [(392,276),(392,326),(363,392),(326,392),(276,392)],
        [(144,392),(94,392),(28,363),(28,326),(28,276)],
        [(28,144),(28,94),(57,28),(94,28),(144,28)],
    ):
        path = pdf.beginPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        pdf.drawPath(path, stroke=1)
    for points, colour in (
        ([(94,92),(140,92),(140,330),(94,330)], ink),
        ([(136,201),(247,92),(299,92),(167,229),(136,220)], copper),
        ([(136,205),(169,192),(304,330),(247,330),(136,225)], ink),
        ([(316,96),(348,96),(348,326),(316,326)], copper),
        ([(299,96),(365,96),(365,113),(299,113)], ink),
        ([(299,309),(365,309),(365,326),(299,326)], ink),
    ):
        path = pdf.beginPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        path.close()
        pdf.setFillColor(colour)
        pdf.drawPath(path, fill=1, stroke=0)
    pdf.restoreState()
