"""Logging and the state directory (~/.local/share/frameassist)."""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

APP = "frameassist"

STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
) / APP
DATA_DIR = Path(
    os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
) / APP

# The debug log is rotated by size so it can never grow without bound on the
# Frame's limited storage: 512 KB current + 3 rotated = ~2 MB max total.
LOG_MAX_BYTES = int(os.environ.get("FRAMEASSIST_LOG_MAX", 512 * 1024))
LOG_BACKUPS = 3


def setup_logging(level=logging.INFO, stream=None):
    root = logging.getLogger(APP)
    if root.handlers:
        return root
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # A read-only/unwritable state dir (a confined or unusual environment)
    # must never stop the app from starting: fall back to the console only.
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            STATE_DIR / "frameassist.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUPS,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass

    console = logging.StreamHandler(stream or sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)
    return root


log = logging.getLogger(APP)
