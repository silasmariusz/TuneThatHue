#!/usr/bin/env python3
"""
Build the TuneThatHue Windows icon (.ico) from a source PNG.

Windows picks the closest size from the icon, so ship the full ladder: 16/32
for the tray and title bar, 48 for the desktop, 256 for the large-icon views
and the installer. LANCZOS keeps the small sizes readable when the source is a
large artwork.

Usage: python tools/make_icon.py [source.png] [out.ico]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
DEFAULT_SRC = Path(r"Z:\t\t.png")
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "resources" / "tth.ico"


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    if not src.is_file():
        print(f"ERROR: icon source not found: {src}", file=sys.stderr)
        return 2

    img = Image.open(src).convert("RGBA")
    # Square the canvas first (padding transparent) so no size is distorted.
    if img.width != img.height:
        side = max(img.width, img.height)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        img = square

    out.parent.mkdir(parents=True, exist_ok=True)
    # Pillow resizes to every requested size internally with the given resample.
    img.save(out, format="ICO", sizes=SIZES)
    print(f"OK  {out}  ({out.stat().st_size // 1024} KB)  sizes={[s[0] for s in SIZES]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
