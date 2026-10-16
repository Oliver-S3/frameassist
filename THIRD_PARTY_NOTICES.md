# Third-party notices

FrameAssist is an independent implementation. It does not contain or derive from
any copywrited code. The Steam Frame integration *mechanisms* below are reused
(and where necessary adapted) from open-source projects, under their licenses.

## frame-voice (fuelCell Voice Typing)

- Project: https://github.com/juanramosjr1/frame-voice
- License: MIT. Copyright (c) 2025 the frame-voice authors (fuelCell).

FrameAssist's architecture reuses proven mechanisms first demonstrated by
frame-voice, an open-source voice-typing app for the Steam Frame:

- a tiny pure-Python layer over Linux `uinput` for a virtual USB keyboard,
  including the ioctl packing and event format;
- a text→keystroke mapper for a US keyboard layout with smart-quote/punctuation
  normalisation;
- SteamVR Input integration where **each button is its own action set**, with
  one boolean "press" action per set, so an overlay app only takes the buttons
  it needs right now;
- `actions.json` + per-controller `bindings_*.json` for Steam Frame controllers
  (`frame_controller`), Quest/Touch and Index/Knuckles;
- using Valve's official Linux ARM64 `openvr` library on the Frame;
- reading SteamVR's "Enable global input from overlays" setting and enabling it
  with one tap;
- a negative-Y overlay badge just below the user's view ("Listening…"),
  rendered with Qt into an RGBA overlay texture;
- starting with SteamVR via a user systemd unit that `PartOf=steamvr.service`,
  plus a tribunal of a window copy and a background copy that hand the
  controller shortcuts to each other over a same-user Unix socket, so nothing
  is typed twice;
- a KDE KWin window rule so the app window never takes keyboard focus, and the
  window ignores typed keys;
- local speech-to-text with faster-whisper (int8 on CPU), VAD filtering, a
  "phantom" transcript filter, and a user word list passed as hotwords;
- an installer (`install.sh`) that provisions the udev `uinput` rule and keeps
  it across SteamOS atomic updates.

Portions of the code for these mechanisms were adapted from frame-voice under
the MIT license. The implementation, gesture engine, and engineering are
original to FrameAssist; its interaction model (hold both grips to talk,
double-tap for hands-free, grip double-taps for copy/paste, stick flicks for
Enter/Escape, word learning, optional AI polish) is an original reimplementation
of the interaction model common to this type of platform — the design language is
inspired by it, the code is not copied from it (see below).

## faster-whisper

- Project: https://github.com/SYSTRAN/faster-whisper
- License: MIT. Used as a runtime dependency.

## PySide6 / openvr

- PySide6: LGPL-3.0 (Qt). Runtime dependency.
- openvr: BSD-3-Clause (Valve). Runtime dependency.
