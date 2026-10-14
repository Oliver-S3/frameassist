# FrameAssist

**Voice typing for the Steam Frame.** Hold both grips, talk, let go — your
words land in whatever text box you're in: Steam chat, the browser, the
terminal, desktop apps.

No on-screen keyboard, no Bluetooth keyboard, no cloud. Speech recognition
runs on the headset itself, with no account and nothing sent online. (There's
an optional AI polish that sends only the *text* of a dictation through your
own OpenRouter account.)

Inspired by [GripSpeak](https://gripspeak.itch.io/gripspeak), built as an
independent, open-source implementation reusing the proven Steam Frame
architecture of [frame-voice](https://github.com/juanramosjr1/frame-voice)
(MIT).

---

## How you use it

| What you want | How (default buttons) |
| --- | --- |
| Type with your voice | Hold **both grips**, talk, let go |
| Hands-free dictation | **Double-tap both grips**, put the controllers down and talk (up to 10 minutes); tap both grips once to stop |
| Copy your last dictation | **Double-tap the left grip** |
| Paste | **Double-tap the right grip** |
| Send (Enter) | **Flick the right stick** |
| Escape / back | **Flick the left stick** |

Every dictation is also copied to the clipboard, so you can paste it again
anywhere. In games, FrameAssist's buttons rest and the game keeps them — turn
on **Keep shortcuts on during VR games** in Settings to override.

A single **"Listening…" / "Typing…"** badge floats below your view while you
talk, so you don't need the window open.

## Install (about 5 minutes, once)

1. **Open the desktop on your Frame** (switch to Linux desktop / KDE) and open **Konsole**.
2. **Copy and paste this line, then press Enter:**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/YOU/frameassist/main/install.sh | bash && exec bash
   ```
   The installer shows 5 steps and tells you when it's done. Running it again
   updates the app.
   > If it asks for a password: voice typing needs permission to act like a
   > keyboard. No password yet? Press `Ctrl+C`, type `passwd`, set a password,
   > then run the line again.
3. **Open FrameAssist** from your apps. If the window says the shortcuts need a
   SteamVR setting, tap **Turn on** (that's SteamVR's *Enable global input
   from overlays*).

That's it. From now on FrameAssist starts in the background every time SteamVR
starts, so the shortcuts work whenever the headset is on.

To make sure everything is set up, run:
```bash
~/.local/bin/frameassist --check
```

## Getting help / diagnostics

If something doesn't work, run `~/.local/bin/frameassist --check` and share the
output. There's also a real-device test checklist in
[docs/DEVICE_TEST_CHECKLIST.md](docs/DEVICE_TEST_CHECKLIST.md).

| Problem | Fix |
| --- | --- |
| Nothing gets typed | Click the text box first, then hold both grips. If it still doesn't work, run `frameassist --check`. If "Can type into apps" says FIX, run the installer again and restart. |
| Controller shortcuts do nothing | In Settings use **Test your buttons**. If it says a SteamVR setting is needed, tap **Turn on** (or SteamVR → Developer → *Enable global input from overlays*), then restart the headset. |
| Shortcuts stop during a game | That's on purpose. Open the Steam menu, or turn on **Keep shortcuts on during VR games**. |
| "Didn't catch that" | Speak a little closer/longer, or pick **Most accurate** in Settings. |
| Words come out wrong | Pick **Most accurate**, talk close to the mic. For names, add them to **My words**. |
| Wrong characters | FrameAssist types as a US keyboard layout. |

## Settings

- **Speech model**: Fast / Balanced / Most accurate (tiny / base / small
  Whisper model, picked once and used every time).
- **Text**: add a space after each dictation; press Enter after each dictation;
  type into the text box or only copy to the clipboard.
- **Buttons**: keep shortcuts on during VR games; start automatically with
  SteamVR.
- **AI polish**: tidy each dictation through your own OpenRouter account
  (off by default; only the text is sent).

## Calibrate

The Frame's IPD wheel shows a number, but lens spacers and face fit shift the
real optical alignment, so what you *see* matters more than the dial. **Calibrate**
(button in the window) shows on-headset charts ~1.4 m away while you roll the
wheel and talk:

- **Resolution** — a Siemens star and fine line pairs; roll the wheel until the
  centre is crisp and free of doubling.
- **Alignment** — a crosshair with two vertical bars and rings; with the right
  IPD the bars look centred and single.
- **Sweet spot** — fine centre detail plus corner markers to find your best lens
  position.

Navigation reuses your gestures: **flick right stick = next**, **flick left =
back**, and talking to the chart works too — "finer", "coarser", "next", and
"save as with-spacers" to remember a profile per fitment. Profiles live in
`~/.config/frameassist/profiles.json`.

## My words

Names and words the speech model keeps getting wrong (like a game's name).
Tap **Edit my words**, put one on each line, and save. Your next dictation
spells them your way. The list is `~/.config/frameassist/words.txt` if you'd
rather edit it yourself — keep it short, only words it gets wrong.

## Uninstall

```bash
bash ~/.local/share/frameassist/uninstall.sh
```

This removes the app and stops it starting with SteamVR, but **keeps** your
settings and My Words (in `~/.config/frameassist`). Add `--purge` to remove
those and the logs too.

```bash
bash ~/.local/share/frameassist/uninstall.sh --purge
```

## How it works (for the curious)

- **Speech to text:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
  running locally on the headset's ARM CPU.
- **Typing:** a virtual USB keyboard through Linux `uinput`, so anything that
  accepts a real keyboard accepts it, including the Steam client. Pure Python,
  nothing to compile.
- **Controller shortcuts:** SteamVR Input, with each button (left grip, right
  grip, left stick, right stick) in its own action set, so the app only takes
  what it needs and games keep their buttons. Gestures (hold, double-tap,
  flick) are worked out by the app, not by SteamVR. Default bindings for the
  Steam Frame controllers (`frame_controller`), plus Quest/Touch and Index.
  The app uses Valve's official Linux ARM64 OpenVR library.
- **Starting with SteamVR:** a user service that starts and stops with
  `steamvr.service` (the way SteamOS runs SteamVR), and runs the shortcuts
  without a window. Opening the app hands the shortcuts to the window until
  it closes, so nothing is ever typed twice.
- **Window:** Qt (PySide6). On KDE the app adds a window rule so its window
  never takes keyboard focus, so pressing buttons leaves your cursor in the
  text box. The window also ignores typed keys.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
QT_QPA_PLATFORM=offscreen pytest
```

The test suite runs on GitHub on both x86 and ARM64 Linux and drives the
controller logic frame by frame against a stand-in for SteamVR. See
`.github/workflows/test.yml`.

## Credits

- Architecture patterns and mechanisms (uinput typing, action-set-per-button
  SteamVR Input, systemd/SteamVR service tie-in, KWin focus handling): adapted
  from [frame-voice](https://github.com/juanramosjr1/frame-voice) (MIT,
  © its authors), a similar app for the Frame. See `THIRD_PARTY_NOTICES.md`.
- Feature design and gesture set: the interaction model is inspired by
  [GripSpeak](https://gripspeak.itch.io/gripspeak), a commercial app by a
  different author; FrameAssist is an independent, from-scratch
  reimplementation with a compatible interaction model, not a copy of its
  code.

MIT licensed. Not affiliated with or endorsed by Valve.
