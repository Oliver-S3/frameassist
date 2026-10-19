"""Generate docs/media/typing-chat.gif - a promo of FrameAssist typing into a
desktop chat app.

Fully generic/fake content (no real accounts, handles, servers, IPs or pairing
codes): a messenger-style window ringed in FrameAssist's yellow focus border, a
breathing "voice" chip, then a message typed character-by-character into the
compose box and sent as an outgoing bubble. Loops through a few lines of
generic game chat.

Usage:  python tools/make_chat_gif.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "docs" / "media" / "typing-chat.gif"
W, H = 620, 460

BG = (20, 24, 31)
WIN = (13, 17, 23)
BORDER = (255, 212, 0)          # FrameAssist yellow focus marker
TEXT = (233, 238, 245)
MUTED = (125, 138, 153)
IN_BUB = (233, 238, 245)
OUT_BUB = (255, 212, 0)
LINE = (38, 46, 58)

INCOMING = [
    ("Alex", "yo you there?"),
    ("Mia", "lobby up when u ready"),
]
MESSAGES = [
    "just beat the final boss",
    "gg everyone that was close",
    "so which game next?",
    "brb grabbing snacks lol",
]
PRE = 8          # frames with the voice chip before typing
PER_CHAR = 2     # frames per typed character
SEND = 8         # frames for the bubble to fade in
HOLD = 4         # pause after sending

DURATION = 85
HEAD = 66        # bottom of the title bar


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


F_TITLE = font(15)
F_NAME = font(12, bold=True)
F_BODY = font(14)
F_BUB = font(13)


def rounded(d, xy, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)


_per_msg = [PRE + len(m) * PER_CHAR + SEND + HOLD for m in MESSAGES]
_starts = []
_s = 0
for _c in _per_msg:
    _starts.append(_s)
    _s += _c
_TOTAL = _s


def frame(step):
    stepg = step % _TOTAL
    idx = 0
    while idx < len(MESSAGES) - 1 and stepg >= _starts[idx + 1]:
        idx += 1
    start = _starts[idx]
    msg = MESSAGES[idx]
    t = stepg - start
    # phase
    if t < PRE:
        phase, ch, send_t = "voice", 0, 0.0
    elif t < PRE + len(msg) * PER_CHAR:
        ch = (t - PRE) // PER_CHAR
        phase, send_t = "type", 0.0
    else:
        send_t = min(1.0, (t - PRE - len(msg) * PER_CHAR) / SEND)
        phase, ch = "send", len(msg)

    fade = 1.0 if phase != "voice" else min(1.0, t / max(1, PRE - 2))

    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    # window + yellow focus border (pulse)
    bw = 5 if (step // 2) % 2 == 0 else 3
    rounded(d, [24, 30, 596, 430], 14, fill=WIN,
            outline=blend((96, 84, 30), BORDER, 0.5 + 0.5 * fade), width=bw)
    d.line([32, HEAD, 588, HEAD], fill=LINE, width=1)
    d.text((40, 44), "Messages  -  stream side", font=F_TITLE, fill=MUTED)

    # -- left conversation list ------------------------------------------
    left = [("Alex", MESSAGES[-1][:16] + "…"),
            ("Mia", INCOMING[1][1][:14] + "…"),
            ("#stream", "4 online")]
    for i, (name, _preview) in enumerate(left):
        x, y = 40, 96 + i * 34
        d.ellipse([x, y, x + 20, y + 20], fill=(56, 64, 76),
                  outline=(73, 84, 96))
        d.text((x + 5, y + 2), name[0].upper(), font=F_NAME, fill=MUTED)
        d.text((x + 26, y), name, font=F_NAME, fill=TEXT if i == 0 else MUTED)
        d.text((x + 26, y + 15), _preview, font=font(10), fill=(88, 99, 112))

    # -- incoming bubbles -------------------------------------------------
    for i, (_, text) in enumerate(INCOMING):
        y = 92 + i * 46
        tw = max(42, int(d.textlength(text, font=F_BUB)) + 22)
        rounded(d, [182, y, 182 + tw, y + 30], 12, fill=IN_BUB)
        d.text((194, y + 7), text, font=F_BUB, fill=(30, 34, 40))

    # -- outgoing (just-sent) bubble --------------------------------------
    if phase in ("type", "send") and ch > 0 or send_t > 0:
        shown = msg[:ch] + ("|" if (step // 2) % 2 == 0 and phase == "type" else "")
        tw = max(42, int(d.textlength(shown, font=F_BUB)) + 22)
        x0 = 596 - 20 - tw
        out_col = blend((60, 56, 40), OUT_BUB, 0.15 + 0.85 * send_t) if phase == "send" else OUT_BUB
        rounded(d, [x0, 252, x0 + tw, 282], 12, fill=out_col)
        d.text((x0 + 11, 259), shown.replace("|", ""), font=F_BUB,
               fill=(30, 34, 40) if send_t > 0.3 or phase == "type" else blend((90, 80, 44), (255, 212, 0), 0.98))
        if phase == "type":
            d.rectangle([x0 + 11 + int(d.textlength(shown.replace("|", ""), font=F_BUB)), 259,
                         x0 + 11 + int(d.textlength(shown.replace("|", ""), font=F_BUB)) + 2, 278],
                        fill=(255, 255, 255) if (step // 2) % 2 == 0 else (180, 170, 150))

    # -- compose box -------------------------------------------------------
    typed = msg[:ch]
    cx0, cy0, cx1, cy1 = 178, 356, 578, 398
    rounded(d, [cx0, cy0, cx1, cy1], 10, fill=(24, 30, 38),
            outline=(64, 74, 88), width=1)
    show = typed + ("|" if (step // 2) % 2 == 0 and phase in ("type", "voice") else "")
    d.text((194, 372), show, font=F_BODY, fill=blend((70, 80, 94), TEXT, fade))
    # placeholder when empty
    if ch == 0:
        d.text((194, 372), "Write a message…", font=F_BODY, fill=(70, 80, 94))

    # -- voice chip (breathing) --------------------------------------------
    if phase == "voice":
        vib = 0.5 + 0.5 * (t / max(1, PRE))
        x = 194
        for i, wdt in enumerate([6, 9, 6, 12, 15, 12, 6, 9, 6]):
            hgt = int(6 + 14 * (0.5 + 0.5 * vib)) + (3 if i % 2 == 0 else 0)
            y0 = 366 - hgt // 2
            d.rectangle([x, y0, x + wdt, y0 + hgt],  # ascending y always
                        fill=BORDER if i in (3, 4, 5) else (112, 96, 40))
            x += wdt + 4

    # progress dots
    for i in range(len(MESSAGES)):
        px = 560 - (len(MESSAGES) - i) * 14
        d.rectangle([px, 416, px + 8, 424], fill=BORDER if i == idx else (56, 64, 76))
    return im


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n = sum(PRE + len(m) * PER_CHAR + SEND + HOLD for m in MESSAGES)
    frames = [frame(i) for i in range(n)]
    frames[0].save(OUT, save_all=True, append_images=frames[1:],
                   duration=DURATION, loop=0, optimize=True)
    print(f"wrote {OUT} ({W}x{H}, {len(frames)} frames, "
          f"{len(frames)*DURATION/1000:.1f}s loop, {OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
