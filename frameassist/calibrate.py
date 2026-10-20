"""Calibrate: visual targets to tune IPD, sharpness and sweet spot.

On the Steam Frame the IPD wheel shows a number, but lens spacers and face
fit can shift the real optical alignment, so what you *see* matters more
than the dial. These charts are shown as a SteamVR overlay ~1.4 m away
while you roll the wheel and talk:

* Resolution  - a Siemens star and fine line pairs. Roll the wheel until the
                centre stays crisp and free of doubling.
* Alignment   - a crosshair with two vertical bars and concentric rings.
                With the right IPD the bars sit centred and single.
* Sweet spot  - fine centre detail plus corner markers; keep the middle
                sharp while you glance to find your best lens position.

Controls reuse the gestures you already have: flick right stick = next,
flick left = back, while talking you can say "finer", "coarser", "next",
"save as <name>", and "done". Profiles are stored per fitment so each setup
remembers its own sweet spot.

The session is pure logic (testable anywhere); the overlay renders the
current chart into an RGBA buffer for SteamVR.
"""

import re

FINENESS_NAMES = ("coarse", "fine", "finest")
FINENESS_LEVELS = len(FINENESS_NAMES)

# Chart identifiers and how to render them.
TESTS = ("resolution", "alignment", "sweetspot", "fov")
TEST_NAMES = {
    "resolution": "Resolution",
    "alignment": "Alignment",
    "sweetspot": "Sweet spot",
    "fov": "FOV / fit",
}
TEST_HINTS = {
    "resolution": "Roll the IPD wheel until the star centre is crisp and free of doubling.",
    "alignment": "The two bars and rings should look centred and single.",
    "sweetspot": "Keep the fine detail sharp while you glance side to side.",
    "fov": "Adjust the head strap and spacers until the edge ring is even and centred, and the centre stays crisp.",
}

W, H = 2048, 1024  # overlay texture


class CalibrationSession:
    """What chart to show and where you are in it. No Qt, no VR, no file I/O,
    so it can be tested with plain calls."""

    def __init__(self, on_change=None):
        self.on_change = on_change
        self.active = False
        self.test_index = 0
        self.fineness = 1

    # -- state ---------------------------------------------------------------
    @property
    def test(self):
        return TESTS[self.test_index]

    @property
    def test_name(self):
        return TEST_NAMES[self.test]

    @property
    def fineness_name(self):
        return FINENESS_NAMES[self.fineness]

    def status(self):
        if not self.active:
            return "Calibration off."
        return f"{self.test_name} · {self.fineness_name}"

    def _notify(self, message=""):
        if self.on_change:
            self.on_change(self.status(), message)
        return message

    # -- navigation (controller flicks) ---------------------------------------
    def start(self):
        self.active = True
        self.test_index = 0
        self.fineness = 1
        return self._notify(f"{self.test_name}. {TEST_HINTS[self.test]}")

    def stop(self):
        self.active = False
        return self._notify("Calibration closed.")

    def next(self):
        self.test_index = (self.test_index + 1) % len(TESTS)
        return self._notify(f"{self.test_name}. {TEST_HINTS[self.test]}")

    def prev(self):
        self.test_index = (self.test_index - 1) % len(TESTS)
        return self._notify(f"{self.test_name}. {TEST_HINTS[self.test]}")

    def finer(self):
        self.fineness = min(self.fineness + 1, FINENESS_LEVELS - 1)
        return self._notify(f"{self.test_name} · {self.fineness_name}")

    def coarser(self):
        self.fineness = max(self.fineness - 1, 0)
        return self._notify(f"{self.test_name} · {self.fineness_name}")

    # -- voice ---------------------------------------------------------------
    def interpret(self, text):
        """Decide what a dictation means while calibrating.

        Returns (command, detail) where command is "start" | "next" | "prev" |
        "finer" | "coarser" | "save" | "stop" | "none", and detail is e.g. the
        profile name for "save". The session is updated for navigation.
        """
        t = " ".join(text.strip().lower().split())
        if not t:
            return "none", ""

        if any(w in t for w in ("next", "forward", "another", "advance", "chart two")):
            msg = self.next()
            return "next", msg
        if any(w in t for w in ("back", "previous", "prev", "go back")):
            msg = self.prev()
            return "prev", msg
        if any(w in t for w in ("finer", "sharper", "smaller", "tighter")):
            msg = self.finer()
            return "finer", msg
        if any(w in t for w in ("coarser", "bigger", "larger", "looser")):
            msg = self.coarser()
            return "coarser", msg

        m = re.search(r"save(?: it)?(?: as)?[:\s]+(.+)", t)
        if m:
            name = m.group(1).strip().strip(".")
            if name:
                self._notify(f"Profile '{name}' ready to save.")
                return "save", name

        if any(w in t for w in ("done", "exit", "stop", "quit", "that's good",
                                "that looks good", "good enough")):
            self.stop()
            return "stop", ""

        return "none", ""


# -- rendering ---------------------------------------------------------------
def _qimage(w, h):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    img = QImage(w, h, QImage.Format_RGBA8888)
    img.fill(Qt.transparent)
    return img


def _buffer(img):
    """RGBA bytes of a QImage, sized for setOverlayRaw."""
    import ctypes
    data = bytes(img.constBits())
    return (ctypes.c_ubyte * len(data)).from_buffer_copy(data)


def render_chart(test, fineness=1, w=W, h=H):
    """Render the named chart at the chosen fineness into (w, h, 4) bytes.

    Pure Qt drawing into an RGBA image, same shape SteamVR's setOverlayRaw
    expects: a dark background with light line work.

    Drawing text needs a QGuiApplication (for QFontDatabase), so callers must
    have one: the app always does (charts render from inside the Qt process);
    tests create one with a fixture."""
    if test == "resolution":
        return _render_resolution(fineness, w, h)
    if test == "alignment":
        return _render_alignment(fineness, w, h)
    if test == "sweetspot":
        return _render_sweetspot(fineness, w, h)
    if test == "fov":
        return _render_fov(fineness, w, h)
    raise ValueError(f"unknown chart {test!r}")


def _spoke_star(painter, cx, cy, r, spokes, lw, color):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QPen
    import math
    pen = QPen(color, lw)
    pen.setCapStyle(Qt.FlatCap)
    painter.setPen(pen)
    for i in range(spokes * 2):
        a = math.pi * i / spokes
        painter.drawLine(QPointF(cx, cy),
                         QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))


def _render_resolution(fineness, w, h):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen
    img = _qimage(w, h)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(0, 0, w, h, QColor(10, 12, 16))
    white = QColor(238, 241, 246)
    grey = QColor(139, 148, 167)

    spokes = 24 + fineness * 16
    r = int(h * 0.62)
    _spoke_star(p, h, h, r, spokes, max(2, 6 - fineness * 2), white)

    # fine line-pair blocks to the right, getting finer with the setting
    lw = max(2, 8 - fineness * 2)
    x0 = int(h * 1.72)
    for band in range(3):
        bw = lw + band * (0 if fineness else 2)
        for i in range(12 // (band + 1) or 1):
            yy = int(h * 0.30) + i * (bw * 2 + 4) + band * int(h * 0.18)
            p.fillRect(x0 + band * 48, yy, int(w * 0.07), bw, white)

    p.setPen(white)
    p.drawText(60, 80, TEST_NAMES["resolution"])
    p.setPen(grey)
    p.drawText(60, 120, TEST_HINTS["resolution"])
    p.end()
    return _buffer(img)


def _render_alignment(fineness, w, h):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPen
    img = _qimage(w, h)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(0, 0, w, h, QColor(10, 12, 16))
    white = QColor(238, 241, 246)
    cx, cy = w // 2, h // 2

    pen = QPen(white, max(3, 8 - fineness * 2))
    p.setPen(pen)
    # big crosshair
    p.drawLine(cx - int(h * 0.55), cy, cx + int(h * 0.55), cy)
    p.drawLine(cx, cy - int(h * 0.55), cx, cy + int(h * 0.55))
    # two vertical bars either side of the cross (the IPD cue)
    off = int(h * 0.14)
    p.drawLine(cx - off, cy - int(h * 0.40), cx - off, cy + int(h * 0.40))
    p.drawLine(cx + off, cy - int(h * 0.40), cx + off, cy + int(h * 0.40))
    # concentric rings
    for rr in (int(h * 0.30), int(h * 0.38)):
        p.drawEllipse(cx - rr, cy - rr, rr * 2, rr * 2)
    # centre dot
    p.setBrush(white)
    p.setPen(Qt.NoPen)
    p.drawEllipse(cx - 6, cy - 6, 12, 12)

    from PySide6.QtGui import QColor as _C
    p.setPen(_C(238, 241, 246))
    p.drawText(60, 80, TEST_NAMES["alignment"])
    p.setPen(_C(139, 148, 167))
    p.drawText(60, 120, TEST_HINTS["alignment"])
    p.end()
    return _buffer(img)


def _render_sweetspot(fineness, w, h):
    from PySide6.QtGui import QColor, QPainter, QPen
    img = _qimage(w, h)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(0, 0, w, h, QColor(10, 12, 16))
    white = QColor(238, 241, 246)
    grey = QColor(139, 148, 167)
    cx, cy = w // 2, h // 2

    # fine centre detail: tight star + tiny rings
    _spoke_star(p, cx, cy, int(h * 0.30), 28 + fineness * 8, max(1, 4 - fineness), white)
    for rr in (28, 40, 52):
        p.drawEllipse(cx - rr, cy - rr, rr * 2, rr * 2)

    # corner identifiers
    pen = QPen(grey, 3)
    p.setPen(pen)
    for xx, yy in ((80, 80), (w - 120, 80), (80, h - 120), (w - 120, h - 120)):
        p.drawRect(xx, yy, 40, 40)

    p.setPen(white)
    p.drawText(60, 60, TEST_NAMES["sweetspot"])
    p.setPen(grey)
    p.drawText(60, 100, TEST_HINTS["sweetspot"])
    p.end()
    return _buffer(img)


def _render_fov(fineness, w, h):
    """FOV / fit chart: a big edge ring + radial ticks + corner notches so you
    can see the visible-field edge while adjusting the head strap or spacers.

    If the ring looks offset, cut off, or asymmetric, the fit is pulling the
    lens — re-seat the strap/spacers until the ring is even all around.
    """
    import math
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen
    img = _qimage(w, h)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(0, 0, w, h, QColor(10, 12, 16))
    white = QColor(238, 241, 246)
    grey = QColor(139, 148, 167)
    cx, cy = w // 2, h // 2

    # Outer ring = reference for the visible field edge; inner ring shades it.
    r = int(h * 0.46)
    p.setPen(QPen(white, max(2, 7 - fineness * 2)))
    p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
    p.setPen(QPen(grey, max(2, 4 - fineness)))
    r2 = int(h * 0.40)
    p.drawEllipse(cx - r2, cy - r2, r2 * 2, r2 * 2)

    # Radial ticks around the outer ring, denser at higher fineness.
    ticks = 16 + fineness * 8
    for i in range(ticks):
        a = 2 * math.pi * i / ticks
        ca, sa = math.cos(a), math.sin(a)
        x0, y0 = cx + int((r - int(0.12 * h)) * ca), cy + int((r - int(0.12 * h)) * sa)
        x1, y1 = cx + int((r - int(0.03 * h)) * ca), cy + int((r - int(0.03 * h)) * sa)
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    # Centre alignment: crosshair + dot.
    p.setPen(QPen(white, max(3, 8 - fineness * 2)))
    p.drawLine(QPointF(cx - int(h * 0.18), cy), QPointF(cx + int(h * 0.18), cy))
    p.drawLine(QPointF(cx, cy - int(h * 0.18)), QPointF(cx, cy + int(h * 0.18)))
    p.setBrush(white)
    p.setPen(Qt.NoPen)
    p.drawEllipse(cx - 8, cy - 8, 16, 16)

    # Corner notches so vignetting or a cut-off edge is easy to spot.
    m = int(h * 0.44)
    p.setPen(QPen(grey, 4))
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        xc, yc = cx + sx * m, cy + sy * m
        p.drawLine(QPointF(xc, yc - int(h * 0.05)), QPointF(xc, yc))
        p.drawLine(QPointF(xc - int(h * 0.05), yc), QPointF(xc, yc))

    p.setPen(white)
    p.drawText(60, 80, TEST_NAMES["fov"])
    p.setPen(grey)
    p.drawText(60, 120, TEST_HINTS["fov"])
    p.end()
    return _buffer(img)


# -- profiles ----------------------------------------------------------------
def profiles_path():
    from .config import CONFIG_DIR
    return CONFIG_DIR / "profiles.json"


def load_profiles(path=None):
    """[{name, test, fineness, created}] — named per-fitment sweet spots."""
    import json
    from pathlib import Path
    p = Path(path or profiles_path())
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("name")]


def save_profile(name, test="resolution", fineness=1, created=None, path=None):
    import json
    import time
    from pathlib import Path
    p = Path(path or profiles_path())
    profiles = load_profiles(p)
    profiles = [d for d in profiles if d.get("name") != name]
    profiles.append({
        "name": name,
        "test": test,
        "fineness": fineness,
        "created": created or time.strftime("%Y-%m-%d %H:%M"),
    })
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    return profiles


def remove_profile(name, path=None):
    import json
    from pathlib import Path
    p = Path(path or profiles_path())
    profiles = [d for d in load_profiles(p) if d.get("name") != name]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    return profiles


class CalibrationOverlay:
    """A SteamVR overlay showing the current calibration chart ~1.4 m in front
    and a little below eye level (like the "Listening…" badge, but larger)."""

    W, H = W, H

    def __init__(self, openvr):
        self.openvr = openvr
        self.overlay = openvr.VROverlay()
        self.handle = None
        try:
            from .steamvr import APP_KEY
            self.handle = self.overlay.createOverlay(APP_KEY + ".calibrate", "Calibrate")
            self.overlay.setOverlayWidthInMeters(self.handle, 1.1)
            m = openvr.HmdMatrix34_t()
            for r in range(3):
                for c in range(4):
                    m.m[r][c] = 1.0 if r == c else 0.0
            m.m[1][3] = -0.05   # just below centre
            m.m[2][3] = -1.4    # 1.4 m in front
            self.overlay.setOverlayTransformTrackedDeviceRelative(
                self.handle, openvr.k_unTrackedDeviceIndex_Hmd, m)
        except Exception:
            self.handle = None

    def show(self, test, fineness=1):
        if self.handle is None:
            return False
        try:
            # Rendering needs a QGuiApplication for text; the app has one when
            # it reaches here, but stay self-sufficient if called elsewhere.
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.instance() or QGuiApplication([])
            buf = render_chart(test, fineness, self.W, self.H)
            self.overlay.setOverlayRaw(self.handle, buf, self.W, self.H, 4)
            self.overlay.showOverlay(self.handle)
            return True
        except Exception:
            return False

    def close(self):
        if self.handle is None:
            return
        try:
            self.overlay.hideOverlay(self.handle)
        except Exception:
            pass
