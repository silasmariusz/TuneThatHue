"""
Build the QNAP App Center icons from the app artwork.

`qbuild` packs whatever sits in this directory and QNAP looks the files up by the
package name: TuneThatHue.gif (the tile and the App Center card), TuneThatHue_80.gif
(the small tile) and TuneThatHue_gray.gif (the stopped state). The `.gif` names are a
QDK naming convention, not a format: QTS 6 and QuTS hero read the files by content, so
they carry 256 px RGBA **PNG** data - the same thing rtorrent16 ships and the App Center
displays. Keep the alpha channel; the rounded tile sits on the panel's own background.

The artwork is TuneThatHue.svg (the Q with the hue wheel across its top half), which is
also the mark the website serves. icon-source.png is a 512 px render of that SVG, kept
so the script still works where rsvg-convert is missing (the NAS has no librsvg).

Run:  python make_icons.py     (rsvg-convert when present, else icon-source.png)
"""

from __future__ import annotations

import shutil
import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).parent
SVG = HERE / "TuneThatHue.svg"
SOURCE = HERE / "icon-source.png"

# name -> (size, greyscale)
TARGETS = {
    "TuneThatHue.gif": (256, False),
    "TuneThatHue_80.gif": (80, False),
    "TuneThatHue_gray.gif": (256, True),
}


def render(size: int) -> Image.Image:
    """The mark at `size`, from the SVG when librsvg is around, else from the master PNG."""
    rsvg = shutil.which("rsvg-convert")
    if rsvg and SVG.exists():
        png = subprocess.run(
            [rsvg, "-w", str(size), "-h", str(size), "-f", "png", str(SVG)],
            check=True, capture_output=True,
        ).stdout
        return Image.open(BytesIO(png)).convert("RGBA")
    if not SOURCE.exists():
        raise SystemExit(f"missing {SVG} and {SOURCE}")
    return Image.open(SOURCE).convert("RGBA").resize((size, size), Image.LANCZOS)


def desaturate(img: Image.Image) -> Image.Image:
    """The stopped state keeps the artwork and the alpha, and loses the colour."""
    r, g, b, a = img.split()
    grey = ImageOps.grayscale(Image.merge("RGB", (r, g, b)))
    return Image.merge("RGBA", (grey, grey, grey, a))


def main() -> None:
    for name, (size, grey) in TARGETS.items():
        img = render(size)
        if grey:
            img = desaturate(img)
        img.save(HERE / name, format="PNG")
        print(f"wrote {name} ({size} px PNG{', grey' if grey else ''})")


if __name__ == "__main__":
    main()
