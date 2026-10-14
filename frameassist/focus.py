"""Keep keyboard focus on the user's text box, not our window.

Clicking our window can make it the active window (KWin ignores Qt's "never
take focus" hint), and then typed text would land in our own window instead
of the text box the user picked. Two fixes for KDE (the SteamOS desktop):

1. A KWin window rule that forces "accept focus: no" for our window, the way
   on-screen keyboards do it.
2. As a backup, right before sending keys, hand focus back to the window the
   user was in, using a tiny KWin script over D-Bus, or plain X11 when KWin
   isn't there.
"""

import os
import shutil
import subprocess
import tempfile

from .log import log

OUR_CLASS = "frameassist"
OUR_TITLE = "FrameAssist"

KWIN_SCRIPT = """
(function () {
    var plasma6 = typeof workspace.stackingOrder !== "undefined" && "activeWindow" in workspace;
    var list = plasma6 ? workspace.stackingOrder : workspace.clientList();
    for (var i = list.length - 1; i >= 0; i--) {
        var w = list[i];
        if (!w || w.minimized || !w.normalWindow || w.skipTaskbar) continue;
        var cls = String(w.resourceClass || "").toLowerCase();
        var title = String(w.caption || "");
        if (cls.indexOf("%(cls)s") !== -1 || title.indexOf("%(title)s") !== -1) continue;
        if (plasma6) { workspace.activeWindow = w; } else { workspace.activeClient = w; }
        break;
    }
})();
""" % {"cls": OUR_CLASS, "title": OUR_TITLE}

_NAME = "frameassist-refocus"


def _dbus(*args):
    """Call KWin over D-Bus with dbus-send; returns stdout or None."""
    tool = shutil.which("dbus-send")
    if not tool or not os.environ.get("DBUS_SESSION_BUS_ADDRESS", "x"):
        return None
    try:
        out = subprocess.run([tool, "--session", "--print-reply", "--dest=org.kde.KWin", *args],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def activate_previous_kwin():
    """Ask KWin to activate the window under ours. Returns True if KWin ran it."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", prefix="frameassist-", delete=False) as f:
        f.write(KWIN_SCRIPT)
        path = f.name
    try:
        _dbus("/Scripting", "org.kde.kwin.Scripting.unloadScript", f"string:{_NAME}")
        out = _dbus("/Scripting", "org.kde.kwin.Scripting.loadScript",
                    f"string:{path}", f"string:{_NAME}")
        if out is None:
            return False
        script_id = out.strip().split()[-1]
        ran = (_dbus(f"/Scripting/Script{script_id}", "org.kde.kwin.Script.run") is not None
               or _dbus(f"/{script_id}", "org.kde.kwin.Script.run") is not None)
        _dbus("/Scripting", "org.kde.kwin.Scripting.unloadScript", f"string:{_NAME}")
        return ran
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def activate_previous():
    """Hand focus to the window under ours (KWin script)."""
    return activate_previous_kwin()


# -- KWin window rule ------------------------------------------------------------
RULE_GROUP = "frameassist"
RULE = {
    "Description": "FrameAssist: never take keyboard focus",
    "wmclass": OUR_CLASS,
    "wmclassmatch": "1",
    "wmclasscomplete": "false",
    "types": "1",
    "acceptfocus": "false",
    "acceptfocusrule": "2",
}


class KConfig:
    """Reads and writes ~/.config/kwinrulesrc with KDE's own tools."""

    FILE = "kwinrulesrc"

    def __init__(self):
        self.tools = None
        for version in ("6", "5"):
            write, read = shutil.which(f"kwriteconfig{version}"), shutil.which(f"kreadconfig{version}")
            if write and read:
                self.tools = (write, read)
                break

    def _run(self, args):
        out = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            raise subprocess.SubprocessError(f"{os.path.basename(args[0])}: {out.stderr.strip()[:200]}")
        return out.stdout.strip()

    def get(self, group, key):
        return self._run([self.tools[1], "--file", self.FILE, "--group", group, "--key", key])

    def put(self, group, key, value):
        self._run([self.tools[0], "--file", self.FILE, "--group", group, "--key", key, value])

    def delete(self, group, key):
        self._run([self.tools[0], "--file", self.FILE, "--group", group, "--key", key, "--delete"])


def _rule_list(conf):
    rules = [r for r in conf.get("General", "rules").split(",") if r]
    if not rules:
        count = conf.get("General", "count")
        rules = [str(i) for i in range(1, int(count) + 1)] if count.isdigit() else []
    return rules


def _set_rule_list(conf, rules):
    conf.put("General", "rules", ",".join(rules))
    conf.put("General", "count", str(len(rules)))


def install_kwin_rule(conf=None):
    """Add our KWin rule if it isn't there yet. Returns True if it's in place."""
    conf = conf or KConfig()
    if getattr(conf, "tools", True) is None:
        return False
    try:
        rules = _rule_list(conf)
        if RULE_GROUP in rules and all(conf.get(RULE_GROUP, k) == v for k, v in RULE.items()):
            return True
        for key, value in RULE.items():
            conf.put(RULE_GROUP, key, value)
        if RULE_GROUP not in rules:
            _set_rule_list(conf, rules + [RULE_GROUP])
    except (OSError, subprocess.SubprocessError, ValueError) as err:
        log.info("couldn't add the KWin rule: %s", err)
        return False
    _dbus("/KWin", "org.kde.KWin.reconfigure")
    log.info("added KWin rule so our window never takes keyboard focus")
    return True


def remove_kwin_rule(conf=None):
    conf = conf or KConfig()
    if getattr(conf, "tools", True) is None:
        return False
    try:
        rules = _rule_list(conf)
        if RULE_GROUP in rules:
            _set_rule_list(conf, [r for r in rules if r != RULE_GROUP])
        for key in RULE:
            conf.delete(RULE_GROUP, key)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    _dbus("/KWin", "org.kde.KWin.reconfigure")
    return True
