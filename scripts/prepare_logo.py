"""Build every KOFAD logo and favicon from the owner's uploaded ORIGINAL image.

No old inline logo, handmade replacement monogram, or photographic legacy asset.
Files created here are part of Django collectstatic's manifest on every build.
"""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "static" / "brand"
MASTER = BRAND / "kofad-original-logo.png"
DISPLAY = BRAND / "kofad-logo-transparent.png"
OFFICIAL = BRAND / "kofad-official-logo.svg"

def _remove_flat_background(image: Image.Image) -> Image.Image:
    """Remove a uniform *edge-connected* backdrop, never erase enclosed lettering.

    A photographed/variable background cannot safely be guessed: for that kind
    of asset we keep the owner's original pixels rather than destroying the logo.
    """
    image = image.convert("RGBA")
    a = image.getchannel("A")
    if a.getextrema()[0] < 245:
        return image
    w, h = image.size
    pts = [(0,0),(w-1,0),(0,h-1),(w-1,h-1),(w//2,0),(w//2,h-1)]
    samples = [image.getpixel(p)[:3] for p in pts]
    bg = tuple(sorted(p[i] for p in samples)[len(samples)//2] for i in range(3))
    if any(max(abs(p[i]-bg[i]) for i in range(3)) > 24 for p in samples):
        return image
    # Adjacent pixels only; logos cannot be deleted merely for sharing a colour.
    copy = image.copy()
    fill = (bg[0], bg[1], bg[2], 0)
    for point in pts:
        rgb = copy.getpixel(point)
        if max(abs(rgb[i]-bg[i]) for i in range(3)) <= 24:
            ImageDraw.floodfill(copy, point, fill, thresh=18)
    return copy


def prepare_logo() -> None:
    if not MASTER.is_file():
        raise RuntimeError("Missing official uploaded image: static/brand/kofad-original-logo.png")
    with Image.open(MASTER) as original:
        original.verify()
    with Image.open(MASTER) as original:
        if original.width < 120 or original.height < 120:
            raise RuntimeError("Uploaded KOFAD logo is too small for sharp branding.")
        artwork = _remove_flat_background(ImageOps.exif_transpose(original))
    # Add safe transparent space so no part of emblem or text touches a crop.
    pad = max(4, round(max(artwork.size) * .025))
    padded = Image.new("RGBA", (artwork.width + 2*pad, artwork.height + 2*pad))
    padded.alpha_composite(artwork, (pad, pad))
    padded.save(DISPLAY, "PNG", optimize=True)
    with BytesIO() as buffer:
        padded.save(buffer, "PNG", optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    width, height = padded.size
    OFFICIAL.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="KOFAD IMPEX ENTERPRISE">'
        f'<image width="{width}" height="{height}" '
        f'href="data:image/png;base64,{encoded}"/></svg>\n', encoding="utf-8",
    )

    # Use only the emblem for the square small-icon family, not tiny unreadable text.
    # Preserve its original colours/appearance; crop approximately its upper region.
    box = padded.getchannel("A").getbbox() or (0,0,width,height)
    x0,y0,x1,y1 = box
    W,H=x1-x0,y1-y0
    if W > H * 1.3:
        emblem = padded.crop((x0 + int(W*.28),y0,x0 + int(W*.72),y0 + int(H*.66)))
    else:
        emblem = padded.crop((x0,y0,x1,y0 + int(H*.65)))
    square = max(emblem.size)
    icon = Image.new("RGBA",(square,square))
    icon.alpha_composite(emblem,((square-emblem.width)//2,(square-emblem.height)//2))
    icon = ImageOps.contain(icon,(480,480),Image.Resampling.LANCZOS)
    # Dark/navy tile keeps the mark legible even where PNG alpha is transparent.
    for size in (16,32,48,96,180,192,512):
        tile = Image.new("RGBA",(size,size),(10,40,61,255))
        safe = max(1,int(size*.82))
        mark = ImageOps.contain(icon,(safe,safe),Image.Resampling.LANCZOS)
        tile.alpha_composite(mark,((size-mark.width)//2,(size-mark.height)//2))
        tile.save(BRAND / f"favicon-{size}.png","PNG",optimize=True)
    (BRAND / "apple-touch-icon.png").write_bytes((BRAND / "favicon-180.png").read_bytes())
    fav = Image.open(BRAND / "favicon-96.png").convert("RGBA")
    fav.save(BRAND / "favicon.ico",format="ICO",sizes=[(16,16),(32,32),(48,48)])

if __name__=="__main__":
    prepare_logo()
    print("All KOFAD branding generated from the owner's uploaded PNG.")
