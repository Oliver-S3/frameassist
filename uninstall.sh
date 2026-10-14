#!/usr/bin/env bash
# Remove FrameAssist.
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/frameassist"
BIN="$HOME/.local/bin/frameassist"

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

printf '  \033[32m✓\033[0m FrameAssist removed. Your config and words were removed with it.\n'
printf '  To keep them, copy %s before uninstalling.\n\n' "${XDG_CONFIG_HOME:-$HOME/.config}/frameassist"
