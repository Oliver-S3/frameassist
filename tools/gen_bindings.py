"""Regenerate frameassist/steamvr/*.json (SteamVR action manifest + default
bindings). Run: python3 tools/gen_bindings.py

Each button the app uses is its own action set with one "press" action, bound
as a plain button. Combos (hold both grips, double-tap, stick flick) are
worked out in frameassist/gestures.py, so SteamVR chords aren't needed. One set
per button lets the app take only the buttons it needs at that moment.
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frameassist" / "steamvr"
APP_KEY = "frameassist.voicetyping"

BUTTONS = {
    "grip_l": ("Left grip", "Left grip: talk (with right), copy (double-tap)"),
    "grip_r": ("Right grip", "Right grip: talk (with left), paste (double-tap)"),
    "stick_l": ("Left stick", "Left stick: flick for Escape"),
    "stick_r": ("Right stick", "Right stick: flick to send"),
}

DESCRIPTION = ("Hold both grips to talk. Double-tap both for hands-free. "
               "Flick right stick to send, left for Escape.")

# Where each button is, per controller type: name -> (hand, input component).
CONTROLLERS = {
    # Steam Frame: grips and sticks on both hands.
    "frame_controller": {"grip_l": ("left", "grip"), "grip_r": ("right", "grip"),
                         "stick_l": ("left", "joystick"), "stick_r": ("right", "joystick")},
    # Quest / Touch: grips and thumbsticks.
    "oculus_touch": {"grip_l": ("left", "grip"), "grip_r": ("right", "grip"),
                     "stick_l": ("left", "thumbstick"), "stick_r": ("right", "thumbstick")},
    # Index: grips and joysticks.
    "knuckles": {"grip_l": ("left", "grip"), "grip_r": ("right", "grip"),
                 "stick_l": ("left", "joystick"), "stick_r": ("right", "joystick")},
}


def bindings(ctype, where):
    out = {}
    for button, (hand, comp) in where.items():
        out[f"/actions/{button}"] = {"sources": [{
            "path": f"/user/hand/{hand}/input/{comp}/click",
            "mode": "button",
            "parameters": {},
            "inputs": {"click": {"output": f"/actions/{button}/in/press"}},
        }]}
    return {
        "action_manifest_version": 0,
        "alias_info": {},
        "app_key": APP_KEY,
        "bindings": out,
        "category": "steamvr_input",
        "controller_type": ctype,
        "description": DESCRIPTION,
        "name": f"FrameAssist defaults ({ctype})",
        "options": {},
        "simulated_actions": [],
    }


def main():
    manifest = {
        "default_bindings": [{"controller_type": c, "binding_url": f"bindings_{c}.json"}
                             for c in CONTROLLERS],
        "action_sets": [{"name": f"/actions/{b}", "usage": "single"} for b in BUTTONS],
        "actions": [{"name": f"/actions/{b}/in/press", "type": "boolean"} for b in BUTTONS],
        "localization": [dict(
            {"language_tag": "en_US"},
            **{f"/actions/{b}": name for b, (name, _) in BUTTONS.items()},
            **{f"/actions/{b}/in/press": what for b, (_, what) in BUTTONS.items()},
        )],
    }
    for old in OUT.glob("*.json"):
        old.unlink()
    (OUT / "actions.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for ctype, where in CONTROLLERS.items():
        (OUT / f"bindings_{ctype}.json").write_text(json.dumps(bindings(ctype, where), indent=2) + "\n")


if __name__ == "__main__":
    sys.exit(main())
