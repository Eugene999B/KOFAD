"""Convert the APPROVED official KOFAD JPEG-inside-SVG to a transparent PNG-inside-SVG.

Never redraw or replace the KOFAD artwork. Work on its original pixel data and
discard only the dark grey backdrop; retain one canonical asset path for web,
favicons, printable documents and credentials. Run before collectstatic.
"""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import re

from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = ROOT / "static" / "brand" / "kofad-official-logo.svg"
JPEG = re.compile(r'data:image/jpeg;base64,([^"\']+)')
PNG = re.compile(r'data:image/png;base64,([^"\']+)')


def prepare_logo() -> None:
    source = OFFICIAL.read_text("utf-8")
    if PNG.search(source):
        # Idempotent for repeat builds, tests, and local development.
        return
    match = JPEG.search(source)
    if not match:
        raise RuntimeError("Official SVG does not contain its approved JPEG source.")
    raw = base64.b64decode(match.group(1), validate=True)
    original = Image.open(BytesIO(raw)).convert("RGB")
    if original.size != (512, 279):
        raise RuntimeError("Unexpected original KOFAD logo dimensions; review artwork before editing.")
    left, top, right, bottom = (148, 39, 360, 243)
    art = original.crop((left, top, right, bottom))
    alpha = Image.new("L", art.size)
    for y in range(art.height):
        for x in range(art.width):
            red, green, blue = art.getpixel((x, y))
            chroma = max(red, green, blue) - min(red, green, blue)
            mark_blue = blue - red > 14 and blue - green > 4
            mark_gold = red - blue > 15 and red - green > 6
            bright_ink = max(red, green, blue) > 132
            white_caption = y + top > 207 and (red + green + blue) / 3 > 67
            score = max(
                (chroma - 16) * 13,
                (max(red, green, blue) - 115) * 9 if bright_ink else 0,
                (red + green + blue) / 3 - 62 if white_caption else 0,
            )
            opacity = 255 if mark_blue or mark_gold or score >= 255 else int(max(0, min(255, score)))
            alpha.putpixel((x, y), opacity)
    art.putalpha(alpha.filter(ImageFilter.GaussianBlur(0.35)))
    buffer = BytesIO()
    art.save(buffer, format="PNG", optimize=True)
    converted = base64.b64encode(buffer.getvalue()).decode("ascii")
    result = JPEG.sub("data:image/png;base64," + converted, source, count=1)
    result = result.replace('viewBox="0 0 512 279"', 'viewBox="0 0 212 204"')
    result = result.replace('<image width="512" height="279"', '<image width="212" height="204"')
    if result == source:
        raise RuntimeError("Official logo conversion unexpectedly made no changes.")
    OFFICIAL.write_text(result, "utf-8")


if __name__ == "__main__":
    prepare_logo()
    print("Official KOFAD brand SVG now contains cropped transparent artwork.")
