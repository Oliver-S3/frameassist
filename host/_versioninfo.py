"""Windows version-resource metadata for the frozen helper exe.

Companion to the two PyInstaller specs. electron-builder / Quicksilver always
embeds CompanyName, ProductName, FileVersion etc. into its exes; a self-signed
binary with NO version info is exactly the profile SmartScreen and AV
heuristics flag as "packed / unknown". The exes embed this so they look like a
finished, legitimate application. The version is read from the repo-root
`version.json` at build time.
"""

import json
import os

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


def _version_tuple(ver):
    parts = [int(p) for p in ver.split(".")]
    parts += [0] * (4 - len(parts))
    return tuple(parts[:4])


def version_info(repodir):
    """A VSVersionInfo instance for the helper exe(s).

    ``repodir`` is the repo root containing version.json (pass
    ``os.path.dirname(SPECPATH)``).
    """
    try:
        with open(os.path.join(repodir, "version.json"), encoding="utf-8") as fh:
            ver = json.load(fh).get("version", "1.0.0")
    except Exception:
        ver = "1.0.0"
    tup = _version_tuple(ver)
    fields = {
        "CompanyName": "Oliver Meissner",
        "FileDescription": "FrameAssistHost PC typing helper",
        "FileVersion": ver,
        "InternalName": "FrameAssistHost",
        "LegalCopyright": "(c) 2026 Oliver Meissner",
        "OriginalFilename": "FrameAssistHost.exe",
        "ProductName": "FrameAssistHost",
        "ProductVersion": ver,
    }
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=tup, prodvers=tup, mask=0x3F, flags=0x0,
            OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable(
                "040904B0", [StringStruct(k, v) for k, v in fields.items()])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
