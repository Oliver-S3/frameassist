# FrameAssistHost for Windows - what to run once

Both `FrameAssistHost.exe` builds are signed with a personal self-signed
certificate (CN=FrameAssistHost). Windows doesn't know the signer yet, so the
first time it will warn "Unknown publisher". Trust it once and the warning goes
away for good (per user, no admin needed).

**DO THIS ONCE, FIRST** - in the folder with the files, in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\trust-FrameAssistHost.ps1

This adds the FrameAssistHost certificate to YOUR user's trusted-publisher and
trusted-root lists (no admin rights, takes one second). After that, the signed
helper just runs.

To undo later:

    powershell -ExecutionPolicy Bypass -File .\trust-FrameAssistHost.ps1 -Remove

**If you skipped the trust step**, Windows may show "Windows protected your PC" -
that's SmartScreen not knowing the certificate, not a virus. Click "More info"
then "Run anyway", or better: run the trust script above.

Notes for the FrameAssist maintainer
------------------------------------
- Regenerate / refresh the certificate + scripts: run `make-cert.ps1` (writes a
  fresh `.cer` + `.p12` + a gitignored password file), then `sign.ps1` (signs
  both builds). Never commit the `.p12` or the password file - they are the
  signing key.
- Re-sign before the certificate's 10-year expiry.
