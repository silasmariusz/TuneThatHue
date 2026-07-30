"""
Build the QNAP App Center icons from the app artwork.

`qbuild` packs whatever sits in this directory, and QNAP looks for the files by the
package name: TuneThatHue.gif (64 px), TuneThatHue_80.gif (80 px on the tile) and
TuneThatHue_gray.gif (64 px, the stopped state). They must be REAL GIFs - the earlier
set was PNG data carrying a .gif name, which the panel would not display.

Run:  python make_icons.py     (regenerates the three GIFs from icon-source.png)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

HERE = Path(__file__).parent
SOURCE = HERE / "icon-source.png"

# name -> (size, greyscale)
TARGETS = {
    "TuneThatHue.gif": (64, False),
    "TuneThatHue_80.gif": (80, False),
    "TuneThatHue_gray.gif": (64, True),
}


def render(src: Image.Image, size: int, grey: bool) -> Image.Image:
    img = src.convert("RGB").resize((size, size), Image.LANCZOS)
    if grey:
        # The stopped state keeps the artwork and loses the colour. A little contrast
        # back, because a flat greyscale of a dark icon turns into a smudge at 64 px.
        img = ImageEnhance.Contrast(ImageOps.grayscale(img).convert("RGB")).enhance(1.15)
    return img.convert("P", palette=Image.ADAPTIVE, colors=256)


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"missing {SOURCE}")
    src = Image.open(SOURCE)
    for name, (size, grey) in TARGETS.items():
        render(src, size, grey).save(HERE / name)
        print(f"wrote {name} ({size} px{', grey' if grey else ''})")


if __name__ == "__main__":
    main()
