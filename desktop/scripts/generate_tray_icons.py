#!/usr/bin/env python3
"""Generate placeholder tray + app icons for the Flowstate menubar app.

This produces a small set of solid-color PNGs (idle / running / error) at
32x32 and @2x 64x64, plus a 128x128 app icon. The icons are intentionally
minimal — visual polish is a follow-up. The shapes are chosen so each state
is distinguishable at a glance: hollow ring (idle), filled circle (running),
filled circle with a slash (error).

Run from the repo root:
    python3 desktop/scripts/generate_tray_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ICON_DIR = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"

# Palette
TRANSPARENT = (0, 0, 0, 0)
DIMMED = (140, 140, 140, 255)
GREEN = (46, 174, 83, 255)
RED = (220, 65, 65, 255)
BLUE = (60, 120, 200, 255)


def _circle(size: int, color: tuple[int, int, int, int], filled: bool) -> Image.Image:
    img = Image.new("RGBA", (size, size), TRANSPARENT)
    draw = ImageDraw.Draw(img)
    pad = max(2, size // 8)
    bbox = (pad, pad, size - pad, size - pad)
    if filled:
        draw.ellipse(bbox, fill=color)
    else:
        # Thick ring — at 32px this gives a 5-6px stroke, scaled to a
        # readable ~3px ring in the NSStatusItem's 18-pt height. A thinner
        # stroke (e.g. size // 12 = 2px) collapses to ~1px and is invisible
        # on a translucent menubar against wallpaper.
        width = max(4, size // 6)
        draw.ellipse(bbox, outline=color, width=width)
    return img


def _slash(img: Image.Image) -> Image.Image:
    draw = ImageDraw.Draw(img)
    size = img.width
    width = max(2, size // 10)
    draw.line(
        (size // 4, size // 4, size - size // 4, size - size // 4),
        fill=(255, 255, 255, 255),
        width=width,
    )
    return img


def _save(img: Image.Image, name: str) -> None:
    out = ICON_DIR / name
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    print(f"  wrote {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")


def main() -> None:
    print(f"Generating tray icons in {ICON_DIR}")
    # Tray states
    for size, suffix in ((32, ""), (64, "@2x")):
        _save(_circle(size, DIMMED, filled=False), f"tray-idle{suffix}.png")
        _save(_circle(size, GREEN, filled=True), f"tray-running{suffix}.png")
        _save(_slash(_circle(size, RED, filled=True)), f"tray-error{suffix}.png")

    # App icon (used for window/dock if ever shown). Tauri requires icon.png
    # in the bundle config; we ship a 128x128 plus a 32x32.
    _save(_circle(128, BLUE, filled=True), "icon.png")
    _save(_circle(32, BLUE, filled=True), "32x32.png")
    _save(_circle(64, BLUE, filled=True), "icon@2x.png")
    print("done.")


if __name__ == "__main__":
    main()
