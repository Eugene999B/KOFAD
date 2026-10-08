"""Preserve the official KOFAD emblem while removing only the photographic backdrop.

The approved JPEG is embossed navy/gold artwork over a slowly varying charcoal
gradient.  A global RGB threshold (previous implementation) erased entire
letters and left severe noisy edges.  Use a fitted backdrop for *this exact*
approved 512x279 source; extract colour contrast without recreating the logo.
A transparent PNG within the same canonical SVG is used by web and receipts.
"""
from __future__ import annotations

import base64
from io import BytesIO
import math
from pathlib import Path
import re

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = ROOT / "static" / "brand" / "kofad-official-logo.svg"
JPEG = re.compile(r'data:image/jpeg;base64,([^"\']+)')
PNG = re.compile(r'data:image/png;base64,([^"\']+)')
# Smooth photographic backdrop, fitted from perimeter pixels outside the art.
BACKGROUND = [
 (52.54245,55.22866,60.05051),(26.40666,25.78492,26.17835),
 (-22.02473,-20.67494,-21.69327),(-11.67782,-10.28644,-10.26919),
 (-12.73754,-12.48391,-12.11777),(-5.35004,-3.71687,-4.29774),
 (-7.24404,-7.1554,-6.32604),(6.84233,5.00948,6.11951),
 (1.00063,1.82618,1.81114),(3.39420,2.54250,2.45429),
 (3.49656,3.09514,1.85494),(3.34858,.55565,1.54120),
 (3.69609,2.99843,2.59539),
]


def _favicon(art: Image.Image) -> None:
    # Original compass emblem only, from the actual official artwork.
    emblem = art.crop((49, 3, 172, 124))
    emblem.thumbnail((90, 90), Image.Resampling.LANCZOS)
    icon = Image.new("RGBA", (96, 96))
    icon.alpha_composite(emblem, ((96 - emblem.width)//2, (96 - emblem.height)//2))
    icon.save(ROOT / "static" / "brand" / "favicon-96.png", "PNG", optimize=True)


def prepare_logo() -> None:
    source = OFFICIAL.read_text("utf-8")
    already = PNG.search(source)
    if already:
        art = Image.open(BytesIO(base64.b64decode(already.group(1)))).convert("RGBA")
        _favicon(art)
        return
    match = JPEG.search(source)
    if not match:
        raise RuntimeError("Cannot locate the exact approved official logo in its SVG.")
    approved = Image.open(BytesIO(base64.b64decode(match.group(1)))).convert("RGB")
    if approved.size != (512,279):
        raise RuntimeError("Official artwork changed size; review extraction before deploying.")

    left,top,right,bottom = (144,34,363,245)
    art = approved.crop((left,top,right,bottom))
    rgba = Image.new("RGBA", art.size)
    p = approved.load()
    out = rgba.load()
    # Compute alpha from contrast with the photographed charcoal wall.
    # This keeps the navy/gold text, globe and arrow without a rectangle.
    for yy in range(top,bottom):
        y = yy / 279 * 2 - 1
        for xx in range(left,right):
            x = xx / 512 * 2 - 1
            features=(1,x,y,x*x,x*y,y*y,x**3,x*x*y,x*y*y,y**3,
                      x**4,x*x*y*y,y**4)
            backdrop=tuple(sum(features[k]*BACKGROUND[k][j] for k in range(13))
                           for j in range(3))
            actual=p[xx,yy]
            difference=math.sqrt(sum((actual[j]-backdrop[j])**2 for j in range(3))/3)
            a=max(0,min(255,int((difference-8.0)*255/20.0)))
            # Avoid generating opaque noise at cutout borders.
            out[xx-left, yy-top]=(actual[0],actual[1],actual[2],a)
    alpha=rgba.getchannel("A").filter(ImageFilter.GaussianBlur(.42))
    rgba.putalpha(alpha)
    _favicon(rgba)
    buffer=BytesIO()
    rgba.save(buffer,format="PNG",optimize=True)
    encoded=base64.b64encode(buffer.getvalue()).decode("ascii")
    result=JPEG.sub(lambda m:"data:image/png;base64,"+encoded,source,count=1)
    result=result.replace('viewBox="0 0 512 279"','viewBox="0 0 219 211"')
    result=result.replace('<image width="512" height="279"','<image width="219" height="211"')
    if result == source:
        raise RuntimeError("Official SVG transparency conversion did not apply.")
    OFFICIAL.write_text(result,"utf-8")


if __name__ == "__main__":
    prepare_logo()
    print("Official KOFAD logo: the original emblem preserved with transparent backdrop.")
