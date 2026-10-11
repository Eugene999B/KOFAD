"""Create first-party Android, iOS and Windows app icons from KOFAD's own emblem.

Usage: python native/scripts/branding.py --channel customer|staff
Run scripts/prepare_logo.py first if favicon-512.png has not been generated.
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[2]
FAVICON = ROOT / "static" / "brand" / "favicon-512.png"
MOBILE = ROOT / "native" / "mobile" / "assets"
DESKTOP = ROOT / "native" / "desktop" / "build"
NAVY = (10, 40, 61, 255)
DARK = (7, 26, 42, 255)
COPPER = (211, 156, 91, 255)


def icon(channel, size=1024, transparent=False):
    if not FAVICON.is_file():
        raise RuntimeError("Generate the official KOFAD icon with python scripts/prepare_logo.py.")
    with Image.open(FAVICON) as raw:
        emblem = raw.convert("RGBA")
    if transparent:
        canvas = Image.new("RGBA", (size, size))
    else:
        canvas = Image.new("RGBA", (size, size), NAVY)
    inner = int(size * 0.77)
    resized = ImageOps.contain(emblem, (inner, inner), Image.Resampling.LANCZOS)
    canvas.alpha_composite(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    if channel == "staff":
        draw = ImageDraw.Draw(canvas)
        # Deliberately distinct staff identity without re-creating or altering
        # the KOFAD emblem. Keep badge away from the icon crop margins.
        left, top, right, bottom = int(size * .27), int(size * .79), int(size * .73), int(size * .89)
        draw.rounded_rectangle((left, top, right, bottom), radius=int(size * .035), fill=COPPER)
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(size * .058))
        except OSError:
            font = ImageFont.load_default()
        draw.text(((left + right) / 2, (top + bottom) / 2), "STAFF",
                  fill=DARK, anchor="mm", font=font)
    return canvas


def splash(channel, bg):
    size = 2732
    result = Image.new("RGBA", (size, size), bg)
    layer = icon(channel, size=900, transparent=True)
    result.alpha_composite(layer, ((size - layer.width) // 2, (size - layer.height) // 2))
    return result.convert("RGB")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=["customer", "staff"], required=True)
    args = parser.parse_args()
    MOBILE.mkdir(parents=True, exist_ok=True)
    DESKTOP.mkdir(parents=True, exist_ok=True)
    graphic = icon(args.channel)
    graphic.convert("RGB").save(MOBILE / "icon-only.png", optimize=True)
    graphic.save(MOBILE / "icon-foreground.png", optimize=True)
    Image.new("RGBA", (1024, 1024), NAVY).save(MOBILE / "icon-background.png", optimize=True)
    splash(args.channel, (246, 249, 249, 255)).save(MOBILE / "splash.png", optimize=True)
    splash(args.channel, NAVY).save(MOBILE / "splash-dark.png", optimize=True)
    graphic.save(DESKTOP / "icon.ico", format="ICO", sizes=[
        (16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)
    ])
    print("Prepared first-party", args.channel, "branded native resources.")


if __name__ == "__main__":
    main()
