# Releasing FrameAssist to the public (GitHub)

This captures the hygiene already done plus the steps for a clean public
release on GitHub (the repo is hosted at `github.com/Oliver-S3/frameassist`).

## 1) Repo hygiene — done, keep it that way

- **No secrets in tracked files** (verified: `git grep` for private keys /
  passwords / the SSH password → none).
- **Private signing material is gitignored** and never committed:
  - `host/signing/FrameAssistHost.p12`
  - `host/signing/.signing-password.txt`
  - Anything holding the p12 password must stay out of the repo (use a CI
    secret if you ever hook CI signing).
- The **public** parts of the signing setup are safe to publish:
  `host/signing/FrameAssistHost.cer`, `make-cert.ps1`, `sign.ps1`,
  `trust-FrameAssistHost.ps1`, `README-for-recipients.md`.
- Temp/scratch is cleaned (`build/`, the one-file `dist\FrameAssistHost.exe`,
  its `.cmd` launcher, `_frameassist_runtime`, stray `.log`/`.err`).
- License: MIT (`LICENSE`).

## 2) Version + update endpoint

Bump all three together on each release:

```text
pyproject.toml            version = "..."
frameassist/__init__.py   __version__ = "..."
version.json              {"version": "..."}
```

The in-app **Check for updates** now supports both host styles via
`FRAMEASSIST_UPDATE_BASE` (choose whichever your public repo is):

| Base you set | Check endpoint | Install archive |
|---|---|---|
| Gitea / self-host: `FRAMEASSIST_UPDATE_BASE=https://host/you/frameassist` | `<base>/raw/<branch>/version.json` | `<base>/archive/<branch>.tar.gz` |
| GitHub: `FRAMEASSIST_UPDATE_BASE=https://github.com/you/frameassist` | `https://raw.githubusercontent.com/you/frameassist/<branch>/version.json` | `https://codeload.github.com/you/frameassist/tar.gz/refs/heads/<branch>` |

GitHub is auto-detected (`update._update_urls`); Gitea keeps the repo pattern.

## 3) The certificate / SmartScreen question — honest verdict

A **self-signed** certificate **does not** suppress SmartScreen's
"unrecognized app" prompt for public users. That prompt is a *reputation*
check; local trust, dual-signing (SHA-256 + SHA-1) and a timestamp all make the
binary legitimate and verifiable, but they don't change SmartScreen's
reputation decision for a never-seen, self-signed publisher on a machine that
hasn't approved the file.

- **Your own machines:** run `host/signing/trust-FrameAssistHost.ps1`
  (per-user) or `-Machine` (admin) once; keep the built exe **frozen** (a new
  hash = one new "Run anyway").
- **Public users:** expect exactly one "More info → Run anyway" per download.
  Options, in increasing effort:
  1. Ship the onedir folder as a zip + document the one-time click (free).
  2. Add a small **installer** later — an *installed* app is a different,
     trusted class to Windows, which is precisely why Quicksilver (installed)
     never complains while a portable exe repeatedly does. Most "it worked for
     that app" puzzles trace back to installed-vs-portable, not the cert.
  3. Buy a real **OV code-signing cert** (≈$100–300/yr) to fully remove the
     prompt everywhere.

Keep the self-signed infra — it's correct for your machines and CI — but do
not treat it as a public-UX fix.

## 4) Release checklist

1. Bump the version in all three places (§2) and commit.
2. Build + sign + **freeze** the release artifact:
   ```bat
   pyinstaller --noconfirm --distpath dist --workpath build host/FrameAssistHost-onedir.spec
   powershell -ExecutionPolicy Bypass -File host\signing\sign.ps1
   :: then DO NOT rebuild/re-sign this artifact again (new hash = new prompt)
   ```
3. Zip `dist\FrameAssistHost\` → `FrameAssistHost-<ver>-win64.zip`;
   `host/FrameAssistHost.spec` can also produce a one-file exe for users who
   prefer a single file.
4. Create the GitHub Release **tag `v<ver>`**; attach the zip (and optional
   one-file exe). Ensure `version.json` on the tagged branch matches.
5. Users install with the GitHub URL:
   ```bash
   FRAMEASSIST_UPDATE_BASE=https://github.com/<you>/frameassist ./install.sh
   ```
6. Smoke the update path: `~/.local/bin/frameassist --check` and
   Settings → Check for updates.
7. CI: `.github/workflows/windows-build.yml` already builds + uploads the
   one-file as a build artifact on Windows.

## 5) Future niceties

- Real OV cert + per-release signing.
- CI signing with the pfx as a repo secret (never in the repo).
- Optional portable `.exe` single-file artifact in the Release.
