"""Prepare the canonical FrameAssist icon from the user's original .ico.

Removes the surrounding white/background so the blue emblem fills the frame
("larger blue square"), then writes:
  assets/frameassist.ico  (multi-size, canonical for the PyInstaller builds)
  assets/frameassist.png  (256px, for the headset install)
  docs/media/icon-512.png (README preview)

Usage:  python tools/prep_icon.py [path-to-source.ico]
Default source: docs/media/FrameAssist.ico (the user's original).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "media" / "FrameAssist.ico"
ICO_OUT = ROOT / "assets" / "frameassist.ico"
PNG_OUT = ROOT / "assets" / "frameassist.png"
PREVIEW_OUT = ROOT / "docs" / "media" / "icon-512.png"

WHITE_THR = 230      # treat pixels whose RGB>thr & alpha>thr as background
MARGIN = 0.05        # small breathing room (fraction of content size)


def pick_biggest_rgba(im: Image.Image) -> Image.Image:
    sizes = im.info.get("sizes") or [(im.width, im.height)]
    best, best_frame = 0, None
    for i, (w, _) in enumerate(sizes):
        try:
            im.seek(i)
        except Exception:
            continue
        if w >= best:
            best, best_frame = w, im.convert("RGBA")
    return best_frame or im.convert("RGBA")


def crop_white(rgba: Image.Image) -> Image.Image:
    r, g, b, a = rgba.split()
    bin_ = lambda ch: ch.point(lambda v: 1 if v > WHITE_THR else 0, mode="1")
    white = ImageChops.logical_and(ImageChops.logical_and(bin_(r), bin_(g)),
                                   ImageChops.logical_and(bin_(b), bin_(a)))
    content = ImageChops.invert(white)
    bbox = content.getbbox()
    if not bbox:
        return rgba
    left, top, right, bottom = bbox
    w, h = right - left, bottom - top
    side = int(max(w, h) * (1 + 2 * MARGIN))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    x0 = (side - w) // 2
    y0 = (side - h) // 2
    canvas.paste(rgba.crop(bbox), (x0, y0))
    return canvas


def main():
    if not SRC.exists():
        raise SystemExit(f"missing source: {SRC}")
    rgba = pick_biggest_rgba(Image.open(SRC))
    square = crop_white(rgba)
    # canonical multi-size ico + 256 png + preview
    square.save(ICO_OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                (64, 64), (128, 128), (256, 256)])
    square.resize((256, 256)).save(PNG_OUT)
    square.resize((512, 512)).save(PREVIEW_OUT)
    print(f"{SRC.name} -> cropped {square.size}px square")
    print(f"wrote {ICO_OUT}, {PNG_OUT}, {PREVIEW_OUT}")


if __name__ == "__main__":
    main()
