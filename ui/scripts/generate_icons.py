"""One-shot icon generator for the Flowstate PWA (UI-073).

Generates four PNG icons from a synthetic Flowstate "F" letterform:

- icon-192.png         (regular, 192x192)
- icon-512.png         (regular, 512x512)
- icon-192-maskable.png (maskable, 192x192, safe-zone aware)
- icon-512-maskable.png (maskable, 512x512, safe-zone aware)

The "regular" variants fill the canvas edge-to-edge with the background color
and place the glyph in the center. The "maskable" variants reserve the
~10% outer ring as background-only padding so platforms (Android, etc.) that
clip the icon to a circle / squircle don't lop off any of the glyph.

Re-running this script is idempotent: it overwrites the four PNGs in-place.
The script is committed alongside the icons so the assets can be regenerated
or tweaked without re-tracing the original recipe.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Flowstate dark theme background (matches --bg-primary in src/index.css).
BG_COLOR = (15, 15, 15, 255)
# Flowstate accent (matches --accent in src/index.css) — used for the glyph.
FG_COLOR = (59, 130, 246, 255)

OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "icons"


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Find a bold sans-serif font on the host. Falls back to Pillow default."""
    candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_icon(size: int, *, maskable: bool) -> Image.Image:
    """Render one icon. ``maskable`` reserves ~10% padding on each side."""
    img = Image.new("RGBA", (size, size), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Safe zone: maskable spec recommends keeping the glyph inside the
    # central 80% of the canvas so platform masks (circle, squircle) can't
    # clip it.
    safe_ratio = 0.6 if maskable else 0.75
    glyph_box = int(size * safe_ratio)

    font = _load_font(glyph_box)

    text = "F"
    # textbbox is more accurate than textsize for centering large glyphs.
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    # Compensate for the bbox's top-left offset when centering.
    x = (size - text_w) // 2 - bbox[0]
    y = (size - text_h) // 2 - bbox[1]

    draw.text((x, y), text, font=font, fill=FG_COLOR)
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-192-maskable.png", 192, True),
        ("icon-512-maskable.png", 512, True),
        # Apple touch icon: iOS expects a non-maskable 180x180 PNG and uses
        # this for the "Add to Home Screen" / "Add to Dock" tile.
        ("apple-touch-icon.png", 180, False),
    ]
    for name, size, maskable in targets:
        out = OUT_DIR / name
        _draw_icon(size, maskable=maskable).save(out, format="PNG")
        print(f"wrote {out} ({size}x{size}, maskable={maskable})")


if __name__ == "__main__":
    main()
