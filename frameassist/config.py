"""Settings and the user's word list, saved under ~/.config/frameassist."""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "frameassist"
AUTOSTART_FILE = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "autostart" / "frameassist.desktop"

# Speech model presets. FrameAssist pairs "Fast / Balanced / Most accurate"
# with a local model that fits the Frame's ARM CPU.
MODELS = {
    "tiny.en": "Fast",
    "base.en": "Balanced",
    "small.en": "Most accurate",
}

DEFAULTS = {
    "model": "base.en",
    "add_space": True,
    "press_enter": False,
    "in_games": False,
    "autostart": True,
    "after_talking": "type",  # "type" | "clipboard"
    "ai_polish": False,
    "ai_key": "",
    # Where each dictation gesture ends up. "copy" or "paste" set the
    # clipboard / Ctrl+C; "enter"/"escape" send a key.
    "punct_model": "default",
    "recent_limit": 20,
}

# Known keys, so an old config with a bad value is repaired, not trusted.
_ENUM_KEYS = {
    "after_talking": ("type", "clipboard"),
}


def load(path=None):
    path = Path(path or CONFIG_DIR / "config.json")
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if not isinstance(saved, dict):
        return cfg
    for key, value in saved.items():
        if key in _ENUM_KEYS and value not in _ENUM_KEYS[key]:
            cfg[key] = DEFAULTS[key]
        elif key in cfg:
            cfg[key] = value
    if cfg["model"] not in MODELS:
        cfg["model"] = DEFAULTS["model"]
    for key in ("autostart",):
        cfg[key] = bool(cfg[key])
    return cfg


def save(cfg, path=None):
    path = Path(path or CONFIG_DIR / "config.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


WORDS_HEADER = """\
# My words: names and words the speech model should spell your way.
# Put one on each line, then save. Your next dictation uses them.
# Only add words it gets wrong: a short list works best.
# Lines starting with # are ignored.
"""


def words_path():
    return CONFIG_DIR / "words.txt"


def words_file(path=None):
    """The word list's path, created with instructions if it's missing."""
    path = Path(path or words_path())
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(WORDS_HEADER, encoding="utf-8")
    return path


def load_words(path=None):
    """The user's word list (Settings > My words), without repeats."""
    try:
        text = Path(path or words_path()).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    words = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for word in line.split(","):
            word = " ".join(word.split())
            if word and word not in words:
                words.append(word)
    return words


def remove_desktop_autostart():
    """Older versions could start the app with the desktop session; SteamVR
    starts it now. Turning auto-start off removes that file too."""
    if AUTOSTART_FILE.exists():
        AUTOSTART_FILE.unlink()
