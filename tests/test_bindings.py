"""Tests for the SteamVR binding JSON.

These lock in the fix for the Steam Frame's grip: the frame_controller grip is
an analog squeeze converted to a button via activate/deactivate thresholds
(Valve's own binding uses /input/grip, mode 'button', click_activate_threshold
0.1). Binding it as a plain '/input/grip/click' button never fired, which was
the whole 'grips don't activate' saga. These tests keep that from regressing.
"""

import json

from tools import gen_bindings  # noqa: F401  (imports fine from repo root)

from frameassist.steamvr import HERE as STEAMVR_DIR


def _load(name):
    return json.loads((STEAMVR_DIR / name).read_text())


def test_frame_controller_grip_uses_valve_style_binding():
    b = _load("bindings_frame_controller.json")
    for side in ("grip_l", "grip_r"):
        src = b["bindings"][f"/actions/{side}"]["sources"][0]
        # grip must bind the component itself, not a 'click' sub-path
        assert src["path"].endswith("/input/grip"), src["path"]
        assert not src["path"].endswith("/input/grip/click")
        assert src["mode"] == "button"
        # the squeeze -> button threshold parameters that make it fire
        assert src["parameters"].get("click_activate_threshold") == "0.1"
        assert "click" in src["inputs"] and "touch" in src["inputs"]


def test_grip_binding_generation_matches_committed():
    """Regenerating must reproduce the committed files exactly."""
    from tools import gen_bindings as g

    for ctype in g.CONTROLLERS:
        where = g.CONTROLLERS[ctype]
        generated = g.bindings(ctype, where)
        committed = _load(f"bindings_{ctype}.json")
        assert generated == committed, f"{ctype} bindings drifted"
