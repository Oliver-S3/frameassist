"""Generate docs/media/promo.gif - an animated promo for the README.

A mock Steam VR-style window ringed in FrameAssist's yellow focus border, with
a pulsing voice waveform and feature captions that cycle. Pure Pillow, generic
content only (no identity data, no real IPs/pairing codes).

Usage:  python tools/make_promo_gif.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "docs" / "media" / "promo.gif"
W, H = 540, 380

BG = (20, 24, 31)
WIN = (13, 17, 23)
BORDER = (255, 212, 0)      # FrameAssist's yellow focus marker
TEXT = (233, 238, 245)
MUTED = (125, 138, 153)
CAREY = (102, 116, 130)

CAPTIONS = [
    ("Hold both grips, and talk", "words land where you look"),
    ("Hands-free dictation", "speak once - type twice"),
    ("On-device speech. No cloud", "your voice never leaves the headset"),
    ("Type into a streamed PC", "the real PC - not just the headset"),
    ("Quick press the stick = Enter", "double-tap grips copy & paste"),
    ("Optional AI polish", "OpenRouter or your own local model"),
    ("FrameAssist - say it, typed", "the streamed-PC typing assistant"),
]

FRAMES_PER = 12          # frames per caption
DURATION = 110           # ms per frame
MAX_T = FRAMES_PER * len(CAPTIONS)


def blend(a, b, f):
    return tuple(int(ra + (rb - ra) * f) for ra, rb in zip(a, b))


def font(size, bold=False):
    for name in ("segoeuib.ttf" if bold else "segoeui.ttf",
                 "arialbd.ttf" if bold else "arial.ttf"):
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TITLE = font(16)
F_BIG = font(25, bold=True)
F_SUB = font(13)


def waveform(d, cx, cy, phase):
    """A little voice waveform - the bars breathe with `phase` (0..1)."""
    widths = [10, 16, 10, 20, 26, 20, 10, 16, 10]
    for i, wdt in enumerate(widths):
        hgt = int(16 + 30 * (1 - abs(phase - 0.5) * 2)) + (8 if i % 2 == 0 else 0)
        x = cx - 90 + i * 24
        col = BORDER if i in (3, 4, 5) else (112, 96, 40)
        d.rectangle([x, cy - hgt // 2, x + wdt, cy + hgt // 2], fill=col)


def draw_frame(step):
    t = step % FRAMES_PER
    idx = step // FRAMES_PER
    caption, sub = CAPTIONS[idx]
    # ease in -> hold -> ease out
    rel = t / FRAMES_PER
    if rel < 0.35:
        fade = rel / 0.35
    elif rel > 0.8:
        fade = 1.0 - ((rel - 0.8) / 0.2)
    else:
        fade = 1.0
    fade = max(0.0, min(1.0, fade))

    border_w = 5 if (step // 2) % 2 == 0 else 3
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([24, 40, 516, 340], radius=14, fill=WIN,
                        outline=blend((96, 84, 30), BORDER, 0.5 + 0.5 * fade),
                        width=border_w)
    d.line([32, 74, 508, 74], fill=(38, 46, 58), width=1)
    d.text((40, 50), "SteamVR  .  FrameAssist", font=F_TITLE, fill=MUTED)

    waveform(d, 270, 134, rel)
    col = blend((52, 60, 72), TEXT, fade)
    d.text((40, 186), caption, font=F_BIG, fill=col)
    if (step // 3) % 2 == 0 and fade > 0.05:
        cw = d.textlength(caption, font=F_BIG)
        d.rectangle([40 + cw + 6, 192, 40 + cw + 14, 222], fill=col)
    d.text((40, 232), sub, font=F_SUB, fill=blend((40, 47, 57), MUTED, fade))
    for i in range(len(CAPTIONS)):
        x = 496 - (len(CAPTIONS) - i) * 14
        d.rectangle([x, 324, x + 8, 332], fill=BORDER if i == idx else (56, 64, 76))
    return im


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [draw_frame(i) for i in range(MAX_T)]
    frames[0].save(OUT, save_all=True, append_images=frames[1:],
                   duration=DURATION, loop=0, optimize=True)
    print(f"wrote {OUT} ({W}x{H}, {len(frames)} frames, {len(frames)*DURATION/1000:.1f}s loop, "
          f"{OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
