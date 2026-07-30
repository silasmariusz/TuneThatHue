"""
Build the clickable video still for the README.

GitHub strips iframes, so a video has to be a linked image. Rather than hotlinking
YouTube's thumbnail - which puts a third-party request on every page view and breaks
the day the URL changes - this bakes our own still: the video's frame, dimmed a little,
with a play button and a corner label over it. The result is committed, so the README
depends on nothing outside the repo.

Usage:  python tools/make_video_thumb.py [source.jpg] [out.png]
        (source defaults to the YouTube still already downloaded next to this script)
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "docs" / "img" / "video.jpg"
LABEL = "Watch it run"

YT_RED = (255, 0, 0)
FONTS = [
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int) -> ImageFont.ImageFont:
    for path in FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def build(src: Path, out: Path) -> None:
    base = Image.open(src).convert("RGB")
    w, h = base.size

    # Dim the frame so the button reads at any size, and so the still cannot be
    # mistaken for a screenshot of the app itself.
    base = Image.blend(base, Image.new("RGB", (w, h), (0, 0, 0)), 0.22)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # Play button, YouTube's proportions (68x48 at 1x).
    bw = int(w * 0.135)
    bh = int(bw * 48 / 68)
    x, y = (w - bw) / 2, (h - bh) / 2
    d.rounded_rectangle([x, y, x + bw, y + bh], radius=bh * 0.22, fill=YT_RED + (235,))
    t = bh * 0.46
    cx, cy = x + bw / 2, y + bh / 2
    d.polygon(
        [(cx - t * 0.55, cy - t), (cx - t * 0.55, cy + t), (cx + t * 0.95, cy)],
        fill=(255, 255, 255, 255),
    )

    # Corner label, on a pill so it survives a bright frame.
    f = _font(int(h * 0.042))
    pad = int(h * 0.022)
    tw = d.textlength(LABEL, font=f)
    th = f.size
    lx, ly = w - tw - pad * 3.2, h - th - pad * 2.6
    d.rounded_rectangle(
        [lx - pad, ly - pad * 0.7, lx + tw + pad, ly + th + pad * 0.7],
        radius=(th + pad * 1.4) / 2,
        fill=(0, 0, 0, 165),
    )
    d.text((lx, ly), LABEL, font=f, fill=(255, 255, 255, 255))

    out.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB").save(out, quality=92)
    print(f"wrote {out} ({w}x{h})")


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT
    if source is None or not source.exists():
        raise SystemExit("pass the video still as the first argument")
    build(source, target)
