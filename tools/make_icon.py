"""Render the FrameAssist icon (vector design mirrored from assets/frameassist.svg)
into a 512px PNG and a multi-size Windows .ico for the PyInstaller builds.

Usage:  python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SVG_OUT = ROOT / "assets" / "frameassist.svg"
PNG_OUT = ROOT / "docs" / "media" / "icon-512.png"
ICO_OUT = ROOT / "assets" / "frameassist.ico"

S = 1024  # supersample for crisp downscaling
YELLOW = (255, 212, 0)
YELLOW_SOFT = (255, 226, 122)
YELLOW_DIM = (201, 169, 28)
YELLOW_FADER = (138, 114, 19)
DARK = (11, 14, 20)


def draw(size=S):
    im = Image.new("RGB", (size, size), DARK)
    d = ImageDraw.Draw(im)
    r = size / S  # scale factor

    def rr(x0, y0, x1, y1, rad, fill):
        d.rounded_rectangle([x0 * r, y0 * r, x1 * r, y1 * r],
                            radius=rad * r, fill=fill)

    # background + faint focus ring
    rr(0, 0, S, S, 96, "#161c28")
    blended = tuple(int(11 + (255 - 11) * 0.14) for _ in "123")
    ring = tuple(int(11 + (y - 11) * 0.14) for y in YELLOW)
    outline = tuple(int(160 + (y - 160) * 0.0) for y in YELLOW)
    d.rounded_rectangle([30 * r, 30 * r, 482 * r, 482 * r], radius=82 * r,
                        outline=ring, width=int(8 * r))

    # microphone
    rr(192, 116, 280, 266, 44, YELLOW)
    d.arc([150 * r, 270 * r, 362 * r, 380 * r], start=200, end=340,
          fill=YELLOW, width=int(26 * r))
    rr(240, 306, 272, 372, 16, YELLOW)
    rr(176, 368, 336, 396, 14, YELLOW)

    # typed-text caret + lines
    rr(352, 200, 418, 216, 8, YELLOW_DIM)
    rr(352, 226, 396, 242, 8, YELLOW_FADER)
    rr(352, 252, 374, 324, 11, YELLOW_SOFT)
    return im.resize((512, 512), Image.LANCZOS) if size > 512 else im


def main():
    PNG_OUT.parent.mkdir(parents=True, exist_ok=True)
    img512 = draw()
    img512.save(PNG_OUT)
    img512.save(ICO_OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                (64, 64), (128, 128), (256, 256)])
    print(f"wrote {SVG_OUT.name} (source vector, unchanged)")
    print(f"wrote {PNG_OUT} (512px)")
    print(f"wrote {ICO_OUT} (16..256 multi-size)")


if __name__ == "__main__":
    main()
