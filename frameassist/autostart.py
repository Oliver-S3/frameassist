"""Start the app with SteamVR.

SteamOS on the Steam Frame runs SteamVR as the user service steamvr.service.
A user service of ours that SteamVR "wants" starts and stops with it (the way
overlay app helpers do) and runs the controller shortcuts without a window.
That's more reliable than SteamVR's own auto-launch list, which is also set
(vr.py) for systems without that service; if both start the app, the second
copy just exits.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .log import log

UNIT = "frameassist.service"
UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"


def launch_command():
    exe = Path(sys.executable).with_name("frameassist")
    return f'"{exe}"' if exe.exists() else f'"{sys.executable}" -m frameassist'


def update_env():
    """The in-app update source (FRAMEASSIST_UPDATE_BASE/BRANCH) from this
    process's environment, ready to bake into a systemd unit so the background
    copy knows where to check for updates too."""
    env = {}
    base = os.environ.get("FRAMEASSIST_UPDATE_BASE", "").strip()
    if base:
        env["FRAMEASSIST_UPDATE_BASE"] = base
        env["FRAMEASSIST_UPDATE_BRANCH"] = os.environ.get(
            "FRAMEASSIST_UPDATE_BRANCH", "main")
    return env


def unit_text(command, env=None):
    env_lines = "".join(
        f"Environment={key}={value}\n" for key, value in (env or {}).items())
    return f"""[Unit]
Description=FrameAssist: hold both grips and talk
# Starts and stops with SteamVR, which SteamOS runs as this user service.
After=steamvr.service
PartOf=steamvr.service
Requisite=steamvr.service

[Service]
ExecStart={command} --background
{env_lines}Restart=on-failure
RestartSec=5

[Install]
WantedBy=steamvr.service
"""


def systemctl(*args):
    """Run systemctl --user; returns the CompletedProcess, or None if it can't run."""
    tool = shutil.which("systemctl")
    if not tool:
        return None
    env = dict(os.environ)
    runtime = Path(f"/run/user/{os.getuid()}")
    if (runtime / "bus").exists():
        env["XDG_RUNTIME_DIR"] = str(runtime)
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime / 'bus'}"
    try:
        return subprocess.run([tool, "--user", *args], capture_output=True, text=True,
                              timeout=15, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def available():
    """True where SteamVR runs as a user service (the Steam Frame)."""
    out = systemctl("cat", "steamvr.service")
    return out is not None and out.returncode == 0


def enabled():
    out = systemctl("is-enabled", UNIT)
    return out is not None and out.stdout.strip() == "enabled"


def is_active():
    """True when the background service is currently running."""
    out = systemctl("is-active", UNIT)
    return out is not None and out.stdout.strip() == "active"


def start_now():
    """Start the background copy now, if it's set up. True if it started."""
    if not enabled():
        return False
    out = systemctl("start", UNIT)
    return out is not None and out.returncode == 0


def stop_now():
    """Stop the background copy now. True if it stopped (or wasn't running)."""
    if not is_active():
        return True
    out = systemctl("stop", UNIT)
    return out is not None and out.returncode == 0


def set_enabled(on, command=None):
    """Turn starting with SteamVR on or off. Returns True if the service is set up."""
    if not available():
        return False
    if on:
        path = UNIT_DIR / UNIT
        text = unit_text(command or launch_command(), env=update_env())
        try:
            if not path.exists() or path.read_text() != text:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                systemctl("daemon-reload")
        except OSError as err:
            log.warning("couldn't write %s: %s", path, err)
            return False
    out = systemctl("enable" if on else "disable", UNIT)
    ok = out is not None and out.returncode == 0
    if ok:
        log.info("start with SteamVR: %s", "on" if on else "off")
    else:
        log.warning("systemctl %s %s failed: %s", "enable" if on else "disable", UNIT,
                    out.stderr.strip()[:200] if out else "no systemctl")
    return ok


def power(on, command=None):
    """Turn the whole app off (stop now + disable autostart) or back on
    (enable autostart + start now). Returns (ok, summary) where summary is a
    short line the UI can show. Idempotent: turning off when already off is a
    success with an explanatory message."""
    if on:
        kept = set_enabled(True, command=command)
        started = start_now()
        if kept and started:
            return True, "Running now and will start with SteamVR next time."
        if kept:
            return True, "Will start with SteamVR next time (start it when you like)."
        return False, "Couldn't set up starting with SteamVR."
    # Turning off: stop now and don't start again with SteamVR.
    disabled = set_enabled(False)
    stopped = stop_now()
    if disabled and stopped:
        return True, "Off. FrameAssist won't start with SteamVR."
    if disabled:
        return True, "Won't start with SteamVR (the running copy will stop on its own)."
    if stopped:
        return True, "Stopped for now (may start again with SteamVR — re-run the installer)."
    return False, "Couldn't turn it off; see the log."
