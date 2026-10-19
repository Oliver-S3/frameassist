#!/usr/bin/env bash
# Remove FrameAssist.
#
# Keeps your settings and My Words in ~/.config/frameassist — run this with
# --purge to remove those too.
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/frameassist"
BIN="$HOME/.local/bin/frameassist"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/frameassist"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/frameassist"

PURGE=0
if [[ "${1:-}" == "--purge" ]]; then
  PURGE=1
elif [[ -n "${1:-}" ]]; then
  printf '\033[31mUnknown option: %s (use --purge to also remove settings)\033[0m\n' "$1"
  exit 2
fi

printf '\n\033[1mFrameAssist uninstall\033[0m\n'

# Stop any running copy and the service.
pkill -f "frameassist/venv/bin/(frameassist|python[0-9.]* -m frameassist)" 2>/dev/null || true
local rt="/run/user/$(id -u)"
if [[ -S "$rt/bus" ]]; then
  XDG_RUNTIME_DIR="$rt" DBUS_SESSION_BUS_ADDRESS="unix:path=$rt/bus" systemctl --user disable --now frameassist.service 2>/dev/null || true
  XDG_RUNTIME_DIR="$rt" DBUS_SESSION_BUS_ADDRESS="unix:path=$rt/bus" systemctl --user daemon-reload 2>/dev/null || true
else
  systemctl --user disable --now frameassist.service 2>/dev/null || true
fi

# Files we added.
rm -f "$BIN"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/frameassist.desktop"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/frameassist.svg"
rm -rf "$APP_DIR"

if [[ $PURGE -eq 1 ]]; then
  rm -rf "$CONFIG_DIR" "$STATE_DIR"
  printf '  \033[32m✓\033[0m FrameAssist removed (settings, My Words and logs included).\n'
  printf '  The uinput keyboard permission is left in /etc/udev/rules.d so a reinstall\n'
  printf '  works without a password; remove it by hand if you want it gone.\n'
else
  printf '  \033[32m✓\033[0m FrameAssist removed. Your settings and My Words are kept in:\n'
  printf '    %s\n' "$CONFIG_DIR"
  printf '  Rerun with \033[1m--purge\033[0m to remove them and the logs too.\n'
fi
printf '\n'
