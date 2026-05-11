#!/usr/bin/env bash
# Install the latest Flowstate.app from desktop/dist/ into /Applications.
#
# Why this script exists (UI-082 root-cause discovery):
#
# Drag-overwriting /Applications/Flowstate.app from a newly-mounted DMG
# does NOT replace the bundle cleanly. macOS's LaunchServices keeps a
# cache keyed on the old bundle's inode, and double-clicking the
# replaced .app can re-launch the OLD binary from cache. Worse: any
# Tauri / NSApp process already running keeps running with the old
# code loaded — even after the user clicks Quit. We chased a notched-
# display NSOpenPanel positioning bug for ~30 commits before realising
# the user was never actually running the fix builds.
#
# This script removes the install in-place, kills any in-flight
# `flowstate-desktop` process, mounts the latest DMG produced by
# build.sh, copies the .app over, and ejects. Subsequent launches are
# guaranteed to load the freshly-built binary.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$DESKTOP_DIR/dist"

# --- 1. Find the most recent .dmg in desktop/dist/. ---
# `ls -t` sorts mtime-desc; first entry is the newest.
DMG=""
if compgen -G "$DIST_DIR/*.dmg" >/dev/null; then
  # shellcheck disable=SC2012
  DMG=$(ls -t "$DIST_DIR"/*.dmg | head -1)
fi
if [[ -z "$DMG" || ! -f "$DMG" ]]; then
  echo "ERROR: no .dmg found in $DIST_DIR" >&2
  echo "       Run 'bash desktop/scripts/build.sh' first." >&2
  exit 1
fi
echo "[install] using $DMG"

# --- 2. Stop any in-flight Flowstate process. ---
# tray → Quit gets the Tauri parent; SIGTERM here is the seatbelt for
# orphaned cases (process hung, crashed mid-quit, or never quit at
# all). SIGKILL after a brief grace if SIGTERM isn't enough.
if pgrep -fl flowstate-desktop >/dev/null 2>&1; then
  echo "[install] sending SIGTERM to existing flowstate-desktop process(es)"
  pkill -TERM -f flowstate-desktop || true
  # macOS pgrep returns 0 if any matched; 1 if none. We want to loop
  # while there are still survivors, capped at ~3s.
  for _ in 1 2 3 4 5 6; do
    if ! pgrep -f flowstate-desktop >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  if pgrep -f flowstate-desktop >/dev/null 2>&1; then
    echo "[install] still alive after 3s; sending SIGKILL"
    pkill -KILL -f flowstate-desktop || true
  fi
fi

# --- 3. Remove the existing install (NOT a drag-overwrite). ---
# `rm -rf` invalidates LaunchServices' inode cache for the old bundle
# so the next launch resolves against the freshly-copied .app, not the
# stale one. Drag-overwrite (Finder's "Replace") preserves the inode
# and triggers the cache-hit bug that wasted ~30 commits of debugging.
if [[ -d "/Applications/Flowstate.app" ]]; then
  echo "[install] removing /Applications/Flowstate.app"
  rm -rf "/Applications/Flowstate.app"
fi

# --- 4. Mount the DMG, copy, eject. ---
# `hdiutil attach -nobrowse` mounts without surfacing a Finder window.
# The mount point lives under /Volumes/<Volume Name> — for Tauri DMGs
# this matches `productName` from tauri.conf.json. Parse it out of
# hdiutil's plist-ish output instead of hardcoding so a future product
# rename doesn't quietly break the install.
echo "[install] mounting DMG"
MOUNT_INFO=$(hdiutil attach -nobrowse -plist "$DMG")
MOUNT_POINT=$(echo "$MOUNT_INFO" | grep -A1 '<key>mount-point</key>' | tail -1 | sed -E 's/.*<string>(.+)<\/string>.*/\1/')
if [[ -z "$MOUNT_POINT" || ! -d "$MOUNT_POINT" ]]; then
  echo "ERROR: could not determine DMG mount point from hdiutil output" >&2
  echo "$MOUNT_INFO" >&2
  exit 1
fi
trap 'hdiutil detach "$MOUNT_POINT" -quiet 2>/dev/null || true' EXIT

APP_SRC=$(ls -d "$MOUNT_POINT"/*.app 2>/dev/null | head -1)
if [[ -z "$APP_SRC" ]]; then
  echo "ERROR: no .app inside the mounted DMG at $MOUNT_POINT" >&2
  exit 1
fi
echo "[install] copying $APP_SRC → /Applications/"
cp -R "$APP_SRC" /Applications/

echo "[install] ejecting"
hdiutil detach "$MOUNT_POINT" -quiet
trap - EXIT

echo
echo "[install] done. Launch with:"
echo "  open /Applications/Flowstate.app"
echo
echo "First launch hits Gatekeeper — right-click → Open → 'Open anyway',"
echo "or: xattr -d com.apple.quarantine /Applications/Flowstate.app"
