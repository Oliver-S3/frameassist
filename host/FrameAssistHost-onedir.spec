# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a ONEDIR build of the FrameAssist PC helper.

Onedir runs the exe in place beside its DLLs — no self-extraction to a temp
folder at launch, so it works even where a machine/session blocks the one-file
bootloader from creating its `_MEI` extraction dir (e.g. AV/EDR heuristics on
one-file self-extraction, or a sandboxed session denying spawned-process temp
writes). Distribution is a folder: ship `dist\FrameAssistHost\` as a zip; the
user extracts anywhere writable and double-clicks FrameAssistHost.exe.

Build from the repo ROOT:

    pip install pyinstaller
    pyinstaller --noconfirm --distpath dist --workpath build host/FrameAssistHost-onedir.spec

Output: dist\FrameAssistHost\FrameAssistHost.exe (+ _internal/ with the DLLs).
CI keeps the one-file build (`host/FrameAssistHost.spec`).
"""

import os
import sys

def _find_ffi_dll():
    """Bundle conda Python's libffi when present (see FrameAssistHost.spec)."""
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

companion = os.path.join(SPECPATH, "frameassist_host.py")

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
    [],
    exclude_binaries=True,
    name="FrameAssistHost",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FrameAssistHost",
)
