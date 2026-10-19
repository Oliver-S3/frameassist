# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the FrameAssist PC helper (Phase 2 companion).

Build on a Windows box (PyInstaller does not cross-compile) from the REPO ROOT:

    pip install pyinstaller
    pyinstaller --noconfirm host/FrameAssistHost.spec

This produces a single, double-clickable ``dist\\FrameAssistHost.exe`` — the
whole point of the companion is that the target PC needs no Python, no pip and
no source, just this one file (see host/build.md for the full build + smoke
test, and .github/workflows/windows-build.yml for the automated Windows build).

Design notes
------------
* ``onefile`` (the default EXE layout used here) + ``console=True``: the console
  is intentional so the LAN IPv4(s) and the pairing code the helper prints at
  startup are visible to the user, along with the "keep this window minimised"
  reminder (SendInput types into whatever window has focus).
* The companion imports only the standard library — ``hashlib, hmac, json, os,
  socket, sys, threading, ctypes`` — so there are NO third-party hidden imports
  and NO data files to collect. ``hiddenimports`` and ``datas`` are empty on
  purpose.
* ``excludes`` is a best-effort guard, not a correctness requirement: the
  companion never imports any of these, but excluding the heavy optional stacks
  (GUI toolkits, NumPy/SciPy/pandas, matplotlib) keeps the one-file exe small
  and stops a stray environment from being pulled in. PyInstaller ignores
  excludes it did not find, so this is safe even when they are not installed.
* ``upx=False``: UPX compression is optional and not installed on the CI runner;
  leaving it off keeps the build deterministic. Set it to True locally if you
  have UPX and want a smaller binary.
"""

import os
import sys

# PyInstaller injects SPECPATH: the absolute directory containing this spec
# (the host/ folder). Building the script path from it makes
# `pyinstaller host/FrameAssistHost.spec` work from the repo root, independent
# of the current working directory.
companion = os.path.join(SPECPATH, "frameassist_host.py")

sys.path.insert(0, SPECPATH)  # resolve host/_versioninfo.py in this build
from _versioninfo import version_info  # noqa: E402
_repo_root = os.path.dirname(SPECPATH)


def _find_ffi_dll():
    """Bundle conda Python's libffi when the BUILD interpreter provides it.

    A conda-built `_ctypes.pyd` imports `ffi.dll`, which PyInstaller's
    dependency scan misses (a known conda+PyInstaller gap). Without it the
    frozen app dies on `import ctypes` with "DLL load failed while importing
    _ctypes". python.org builds statically link libffi and never need this
    file, so we only bundle it when it actually exists next to the build
    interpreter (keeps the CI build, which uses setup-python, unchanged).
    """
    candidates = (
        os.path.join(sys.base_prefix, "Library", "bin", "ffi.dll"),
        os.path.join(os.path.dirname(sys.executable), "ffi.dll"),
    )
    for cand in candidates:
        if os.path.exists(cand):
            return cand
    return None


_ffi_dll = _find_ffi_dll()
_binaries = [(_ffi_dll, ".")] if _ffi_dll else []

a = Analysis(
    [companion],
    pathex=[],
    binaries=_binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "PySide6",
        "PySide2",
        "PyQt5",
        "PyQt6",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="FrameAssistHost",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_info(_repo_root),
    icon=os.path.join(_repo_root, "assets", "frameassist.ico"),
)
