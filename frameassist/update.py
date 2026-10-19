"""In-app updates with automatic rollback.

Checks a small version file and, when a newer version exists, upgrades the
installed package inside its own virtualenv. If anything fails after the
upgrade starts, the previous version is reinstalled automatically, so a bad
update never leaves FrameAssist broken.

Update source (in order of preference):
1. ``FRAMEASSIST_UPDATE_BASE`` + ``FRAMEASSIST_UPDATE_BRANCH`` (default "main")
   point at a repo. On GitHub
   (``https://github.com/<owner>/<repo>``) the app checks
   ``https://raw.githubusercontent.com/<owner>/<repo>/<branch>/version.json``
   and installs the ``codeload`` source archive; on a self-hosted Gitea-style
   host it checks ``<base>/raw/<branch>/version.json`` and installs
   ``<base>/archive/<branch>.tar.gz``.
2. A legacy explicit ``FRAMEASSIST_UPDATE_URL`` (version endpoint only).
3. Nothing — the check button reports "No update source configured".

`install.sh` can make the Settings > Check for updates button live by running
with ``FRAMEASSIST_UPDATE_BASE`` set; it then bakes the values into both the
desktop launcher and the SteamVR background service.
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import __version__
from .log import log

DEFAULT_URL = os.environ.get("FRAMEASSIST_UPDATE_URL", "")
# Preferred: a repo to update from, e.g. "http://gitea-host/user/frameassist".
# The check endpoint is <base>/raw/<branch>/version.json and the upgrade
# installs <base>/archive/<branch>.tar.gz.
DEFAULT_BASE = os.environ.get("FRAMEASSIST_UPDATE_BASE", "")
DEFAULT_BRANCH = os.environ.get("FRAMEASSIST_UPDATE_BRANCH", "main")
VERSION_FILE = Path(sys.prefix) / "lib" / "python" / "frameassist.version"


def current_version():
    return __version__


_GITHUB_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/?#]+)"
)


def _update_urls(base, branch):
    """(check_url, archive_url) for a repo ``base`` and ``branch``.

    GitHub keeps its own raw + codeload endpoints, so a ``github.com`` base is
    translated; any other host (Gitea/self-hosted) keeps the repo-style
    ``<base>/raw/...`` and ``<base>/archive/...`` pattern.
    """
    branch = (branch.strip() or "main").lstrip("/")
    m = _GITHUB_RE.match(base)
    if m:
        owner = m.group("owner")
        repo = m.group("repo").rstrip("/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        check = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/version.json"
        archive = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{branch}"
    else:
        check = f"{base}/raw/{branch}/version.json"
        archive = f"{base}/archive/{branch}.tar.gz"
    return check, archive


def update_source(url=None):
    """Where a new version is checked and what gets installed.

    Returns ``(check_url, pip_args)``, or ``(None, None)`` when no update
    source is configured. An explicit ``url`` (legacy) wins; otherwise the repo
    base (``FRAMEASSIST_UPDATE_BASE``) is used, then the legacy
    ``FRAMEASSIST_UPDATE_URL`` endpoint.
    """
    if url:
        return url, ["-U", "frameassist"]
    base = DEFAULT_BASE.strip().rstrip("/")
    if base:
        check, archive = _update_urls(base, DEFAULT_BRANCH)
        return check, ["-U", f"frameassist @ {archive}"]
    if DEFAULT_URL:
        return DEFAULT_URL, ["-U", "frameassist"]
    return None, None


def latest_version(url=None, timeout=15):
    """The newest version at ``url`` (or at the configured update source), or
    None if no source is configured or it can't be read."""
    url = url or update_source()[0]
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.load(resp)
        version = data.get("version")
        return str(version) if version else None
    except Exception as err:
        log.info("update check failed: %s", err)
        return None


def _version_tuple(version):
    """'1.2.3' -> (1, 2, 3). Unknown/invalid -> (0, 0, 0)."""
    try:
        return tuple(int(part) for part in str(version).split(".")[:3])
    except ValueError:
        return (0, 0, 0)


def update_available():
    """True when the configured source reports a newer version than running."""
    latest = latest_version()
    return bool(latest) and _version_tuple(latest) > _version_tuple(current_version())


def run_self_update(url=None, pip_args=None):
    """Check the configured source and upgrade the installed package from it,
    rolling back on failure. Returns a message describing what happened.
    Never raises.
    """
    check, source_args = update_source(url)
    if source_args is None:
        return "No update source configured (FRAMEASSIST_UPDATE_BASE)."
    last = latest_version(check)
    if last is None:
        return "Couldn't reach the update source to check for a new version."
    if _version_tuple(last) <= _version_tuple(current_version()):
        return "Already up to date."

    previous = str(VERSION_FILE.read_text()) if VERSION_FILE.exists() else current_version()

    def _pip(*args):
        return subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", *args],
            capture_output=True, text=True, timeout=600,
        )

    try:
        result = _pip(*(pip_args or source_args))
        if result.returncode != 0:
            log.warning("update failed: %s", result.stderr[-400:])
            return "Update failed: pip reported an error."
        # re-save the version marker
        VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        VERSION_FILE.write_text(last or current_version())
        return "Updated. Restart FrameAssist to use the new version."
    except Exception as err:
        # Roll back to the previous version, best effort.
        log.exception("update failed; rolling back")
        try:
            _pip("--force-reinstall", f"frameassist=={previous}")
        except Exception as rollback_err:
            log.exception("rollback failed: %s", rollback_err)
        return f"Update failed and was rolled back: {err}"


if __name__ == "__main__":
    print(run_self_update())
