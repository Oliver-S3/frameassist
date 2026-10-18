# Building `FrameAssistHost.exe` (Windows)

`host/frameassist_host.py` is the PC-side companion FrameAssist talks to when
its delivery mode is **"A streamed PC"**. It is a single, stdlib-only Python
file, so it freezes cleanly into one double-clickable `FrameAssistHost.exe`
with [PyInstaller](https://pyinstaller.org/). The target PC then needs **no
Python, no pip, and no source** — just the exe.

> PyInstaller does **not** cross-compile: build this on a Windows machine (or
> the `windows-latest` CI runner). The exe is Windows-only.

## Prerequisites

- A Windows PC (the build host and the target are both Windows).
- Python 3.10+ — **3.12 is recommended** to match CI
  (`.github/workflows/windows-build.yml`).

## Build

Run everything from the **repo root** (the folder containing `host/`).

1. *(Optional but recommended)* use a clean venv so nothing stray is bundled:

   ```bat
   python -m venv .venv-build
   .venv-build\Scripts\activate
   ```

2. Install PyInstaller:

   ```bat
   pip install pyinstaller
   ```

3. Build from the committed spec (one-file, `console=True`, output name
   `FrameAssistHost`):

   ```bat
   pyinstaller --noconfirm host/FrameAssistHost.spec
   ```

4. **Output:** `dist\FrameAssistHost.exe` (created relative to the folder you
   ran the command from — the repo root). The intermediate `build\` folder can
   be deleted afterwards.

To pin the output location explicitly (what CI does), add the paths:

```bat
pyinstaller --noconfirm --distpath dist --workpath build host/FrameAssistHost.spec
```

## Smoke test

1. Double-click `dist\FrameAssistHost.exe` (or run it from a terminal).
2. Confirm the console window prints, near the top:
   - one or more `PC address : <LAN IPv4>:8763` lines, and
   - `Pairing code : XXXX-XXXX` (a fresh code on first run), and
   - `Listening on 0.0.0.0:8763. Leave this running. Press Ctrl+C to stop.`
3. Enter that address and pairing code into the FrameAssist headset under
   **Settings → Type into: "A streamed PC"**, then press **Test connection** —
   it should report the helper is reachable.
4. **Keep the helper window minimised or in the tray.** `SendInput` types into
   whatever window currently has focus; if this console were focused it would
   type into itself. Never foreground it.

## Firewall (first run)

On first launch Windows Defender Firewall prompts about the new app. Tick
**Private networks** and click **Allow access on Private networks**. Without
this the LAN bind is blocked and the headset cannot connect.

If the headset still can't connect (`frameassist --check` shows
`FIX PC helper reachable` while the console shows `Listening on 0.0.0.0:8763`),
the Private-only app rule is not enough on machines with a mixed-profile setup
(e.g. a Public-classified link in the path, or the helper exe path changed
between builds). From an **Administrator PowerShell on the PC**, allow the port
on ALL profiles:

```powershell
netsh advfirewall firewall add rule name="FrameAssistHost TCP 8763" dir=in action=allow protocol=TCP localport=8763 enable=yes profile=any
```

Then retest. Pair only on a network you trust.

## Where the pairing code lives

The helper stores its port and pairing code at
`%APPDATA%\FrameAssistHost\config.json`, created on first run:

```json
{ "port": 8763, "secret": "K7QF-3MZX" }
```

- The code is stable across restarts (so the headset keeps working).
- Delete that file to generate a **new** pairing code on the next launch.

## Troubleshooting: the exe opens then closes instantly

If double-clicking `FrameAssistHost.exe` shows a console flash and the process
exits, open a terminal and run it (or use `run-FrameAssistHost.cmd`) so the
message stays on screen. The known failure is:

```
[PYI-xxxxx:ERROR] Could not create temporary directory!
```

The one-file bootloader extracts its bundled runtime to the OS temp folder
before Python even starts. If that folder is unwritable for the process, the
exe cannot start. Fixes, in order:

1. Run it from a normal Command Prompt (not from inside a confined/sandboxed
   terminal); the bootloader then uses a normal temp path.
2. Relocate the extraction explicitly at run time by pointing `PYINSTALLER_TMPDIR`
   at a user-writable folder (e.g. `%LOCALAPPDATA%\FrameAssistHost\runtime`,
   create it first) before starting the exe.
3. If it happens on a dev box with a restricted session (e.g. this DSH dev
   sandbox denies spawned-process temp writes even into the workspace), run the
   helper from source instead: `host\run-from-source.cmd` (or
   `python host\frameassist_host.py`). The CI `windows-build` smoke test boots
   the exe on a clean `windows-latest` runner, where temp is normal.

This is an environment/temp-path issue, not a defect in the companion code.

### `_ctypes` fails with "DLL load failed (specified module could not be found)"

If the exe boots but dies at `import ctypes` with that error, the build was made
with a **conda** Python: conda's `_ctypes.pyd` imports `ffi.dll`, which
PyInstaller's dependency scan misses. The spec now bundles `conda-prefix\Library\bin\ffi.dll`
automatically when the build interpreter provides it (see `_find_ffi_dll()` in
`host/FrameAssistHost.spec`). Rebuild and re-check with:

```
pyi-archive_viewer -l dist\FrameAssistHost.exe | findstr ffi
```

A build from a python.org interpreter statically links libffi and never needs it,
so CI (`setup-python`) is unaffected.

### The one-file variant (optional, on demand)

`host/FrameAssistHost.spec` builds a single `dist\FrameAssistHost.exe`, used when
a genuinely single file is wanted (e.g. CI uploads it as a download artifact).
Its bootloader extracts to `%TEMP%` on launch; if a machine/session denies that,
a launcher `dist\run-FrameAssistHost.cmd` can point `PYINSTALLER_TMPDIR` at a
`_frameassist_runtime` folder beside the exe instead. **The released `dist`
folder ships only the onedir build; build the one-file explicitly when needed.**

### Preferred: the onedir build (no admin, no extraction)

`host/FrameAssistHost-onedir.spec` builds a `dist\FrameAssistHost\` folder
(exe + `_internal\` DLLs) that runs **in place** — it extracts nothing at
launch, so it works even where a machine/session blocks the one-file
bootloader's temp extraction (EDR/AV heuristics on one-file self-extraction,
or a sandboxed desktop session). Verified to boot without elevation in a
restricted session. Ship the folder as a zip; users extract anywhere writable
and double-click `FrameAssistHost.exe`. This is the build kept in `dist`.

### Elevation on locked-down / managed sessions

On most Windows PCs the helper types with no special privileges. But a managed
or sandboxed session (e.g. a development sandbox like DSH) may silently filter
out `SendInput` events coming from non-elevated child processes — the helper
accepts the request and replies `{"ok":true}`, but no text appears in the
focused window. If you see that, **run `FrameAssistHost.exe` as administrator**
(open an Administrator PowerShell and start it there, keep the window
minimised). Verified: elevation bypasses the filter and text lands normally.
Normal end-user machines do not need elevation.

### Ctrl+C stops the helper cleanly

The accept loop runs with a short socket timeout so KeyboardInterrupt is
delivered on Windows; `Ctrl+C` prints `Stopping.` and exits 0.

## Notes

- **`console=True` is deliberate** — it shows the IP + pairing code and the
  "keep minimised" reminder. A windowed (`console=False`) build would hide the
  very information the user needs to pair.
- The companion imports **only the standard library**, so the spec collects no
  third-party packages and no data files. The `excludes` list in
  `host/FrameAssistHost.spec` is just a guard against a stray heavy dependency
  bloating the exe.
- **UPX is not required** (`upx=False`). Install UPX and set `upx=True` in the
  spec if you want a smaller binary.
- The automated build lives in `.github/workflows/windows-build.yml`: it builds
  on `windows-latest` and uploads `FrameAssistHost.exe` as a downloadable
  **build artifact** (attach it to a GitHub release for end users).

## Signing (optional but recommended)

The helper exes can be code-signed with a personal **self-signed** certificate
so Windows stops showing "Unknown publisher" once the signer is trusted. This
silences SmartScreen **on machines that run the trust script**; it is not a
CA-issued certificate, so general public distribution would still need a real
OV/EV cert + a trusted timestamp.

1. **Create the certificate** (one-time, from `host/signing/`):

   ```powershell
   powershell -ExecutionPolicy Bypass -File host\signing\make-cert.ps1
   ```

   Writes `FrameAssistHost.cer` (public — commit), `FrameAssistHost.p12`
   (PRIVATE — gitignored), and `.signing-password.txt` (PRIVATE — gitignored).
   A fresh 10-year code-signing cert (CN=FrameAssistHost) is created each run.

2. **Sign the builds** (after `pyinstaller`):

   ```powershell
   powershell -ExecutionPolicy Bypass -File host\signing\sign.ps1
   ```

   Signs `dist\FrameAssistHost.exe` (one-file) and
   `dist\FrameAssistHost\FrameAssistHost.exe` (onedir) with the p12, and
   **dual-signs** them: a SHA-256 primary signature plus an appended SHA-1
   signature (the same profile electron-builder/Quicksilver ships — a
   SHA-256-only signature is what SmartScreen classifies as an "unrecognized
   app"). Check both with `signtool verify /pa /all /v <exe>` (`/all` is
   required; without it you only see the primary signature). The primary also
   gets a best-effort RFC3161 timestamp.
   
   > **Freeze the artifact after signing.** Re-signing changes the file's
   > SHA-256, which resets SmartScreen's "Run anyway" memory. Build once, sign
   > once, then distribute that exact file.

3. **Trust it once per PC** (recipients):

   ```powershell
   powershell -ExecutionPolicy Bypass -File host\signing\trust-FrameAssistHost.ps1
   ```

   Adds the cert to the user's Trusted Root + Trusted Publishers (per-user, no
   admin, reversible with the same script + `-Remove`).

> **Key hygiene:** never commit `FrameAssistHost.p12` or
> `.signing-password.txt` — anyone holding both can sign binaries as you. They
> are already gitignored (`host/signing/*.p12`, `host/signing/.signing-password.txt`).
>
> Signing does **not** change the elevation requirement on locked-down /
> sandboxed sessions — that filter is about process trust, not publisher trust.
>
> **Version metadata is embedded in every build** (CompanyName `Oliver Meissner`,
> ProductName `FrameAssistHost`, FileDescription, FileVersion read from
> `version.json`, via `host/_versioninfo.py`, wired into both specs). A real app
> always carries this; a signed binary with *no* version info is the exact
> profile SmartScreen/AV heuristics flag as "packed", and it's what made the
> helper look less legitimate than the Quicksilver (electron-builder) exe until
> it was added.
