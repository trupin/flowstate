#!/usr/bin/env bash
# Build the Flowstate desktop app (UI-077).
#
# Produces an unsigned `Flowstate-X.Y.Z-<arch>.dmg` in `desktop/dist/`.
# No Apple Developer cert, no notarization — first-launch users will see
# Gatekeeper's "developer cannot be verified" warning and must right-click
# → Open. The README documents this.
#
# Usage:
#   bash desktop/scripts/build.sh                       # auto-detect host arch
#   bash desktop/scripts/build.sh aarch64-apple-darwin  # explicit triple
#   bash desktop/scripts/build.sh x86_64-apple-darwin   # explicit triple
#
# Steps:
#   1. Vendor portable Python via UI-075's vendor_python.sh.
#   2. `cargo tauri build --target <triple>` produces the .app + .dmg.
#   3. Move the .dmg to desktop/dist/ with a versioned name.
#   4. Print final size.

set -euo pipefail

# Repo paths (resolved from this script, not cwd).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_DIR/.." && pwd)"
DIST_DIR="$DESKTOP_DIR/dist"
TAURI_DIR="$DESKTOP_DIR/src-tauri"
TAURI_CONF="$TAURI_DIR/tauri.conf.json"

# --- Step 0: detect arch + sanity-check tooling. ---
detect_triple() {
  local arch
  arch=$(uname -m)
  case "$arch" in
    arm64|aarch64) echo "aarch64-apple-darwin" ;;
    x86_64) echo "x86_64-apple-darwin" ;;
    *) echo "ERROR: unsupported arch $arch" >&2; exit 1 ;;
  esac
}

TRIPLE="${1:-$(detect_triple)}"
SHORT_ARCH="${TRIPLE%-apple-darwin}"  # aarch64 / x86_64

if ! command -v cargo >/dev/null 2>&1; then
  echo "ERROR: cargo not on PATH. Source ~/.cargo/env or install Rust:" >&2
  echo "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh" >&2
  exit 1
fi

if ! cargo tauri --version >/dev/null 2>&1; then
  echo "ERROR: tauri-cli not installed. Run:" >&2
  echo "  cargo install tauri-cli --locked --version '^2.0'" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq not on PATH. Install via 'brew install jq'." >&2
  exit 1
fi

# Tauri requires the rust target to be installed for cross-arch builds.
if ! rustup target list --installed 2>/dev/null | grep -q "^${TRIPLE}\$"; then
  echo "[build] installing rustup target $TRIPLE"
  rustup target add "$TRIPLE"
fi

VERSION=$(jq -r .version "$TAURI_CONF")
if [[ -z "$VERSION" || "$VERSION" == "null" ]]; then
  echo "ERROR: could not read version from $TAURI_CONF" >&2
  exit 1
fi

echo "[build] flowstate-desktop $VERSION ($TRIPLE)"

# UI-076: signing setup. `cargo tauri build` insists on signing once
# `bundle.createUpdaterArtifacts: true` is set and a pubkey lives in
# tauri.conf.json — otherwise it errors with "A public key has been
# found, but no private key". Auto-discover the key (and Keychain
# password) so maintainers don't have to remember to export two env
# vars in every shell session.
#
# Tauri reads:
#   TAURI_SIGNING_PRIVATE_KEY       (key contents — preferred)
#   TAURI_SIGNING_PRIVATE_KEY_PATH  (path to key file — fallback)
#   TAURI_SIGNING_PRIVATE_KEY_PASSWORD (optional, for password-protected keys)
DEFAULT_KEY_PATH="$HOME/.tauri/flowstate.key"
DEFAULT_KEY_KEYCHAIN_SERVICE="flowstate-tauri-signing"

# Key: respect explicit override, else fall back to the canonical path
# documented in RELEASING.md. Export the *contents* (TAURI_SIGNING_PRIVATE_KEY)
# rather than just the path (_PATH) — `cargo tauri build` honors the
# contents-form reliably across Tauri 2.x versions; _PATH support is
# inconsistent and fails with "A public key has been found, but no
# private key" on some builds.
if [[ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
  # If only _PATH was set, read from there. Else fall back to the default path.
  if [[ -n "${TAURI_SIGNING_PRIVATE_KEY_PATH:-}" && -f "${TAURI_SIGNING_PRIVATE_KEY_PATH}" ]]; then
    export TAURI_SIGNING_PRIVATE_KEY="$(cat "${TAURI_SIGNING_PRIVATE_KEY_PATH}")"
    echo "[build] auto-resolved TAURI_SIGNING_PRIVATE_KEY from \$TAURI_SIGNING_PRIVATE_KEY_PATH"
  elif [[ -f "$DEFAULT_KEY_PATH" ]]; then
    export TAURI_SIGNING_PRIVATE_KEY="$(cat "$DEFAULT_KEY_PATH")"
    echo "[build] auto-resolved TAURI_SIGNING_PRIVATE_KEY from $DEFAULT_KEY_PATH"
  fi
fi

# Password: if the key is password-protected and the password isn't
# already in env, try the Keychain service documented in RELEASING.md.
# Silent on miss — many keys have no password and this is fine.
if [[ -z "${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}" ]] && command -v security >/dev/null 2>&1; then
  if password=$(security find-generic-password -s "$DEFAULT_KEY_KEYCHAIN_SERVICE" -w 2>/dev/null); then
    export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$password"
    echo "[build] auto-resolved TAURI_SIGNING_PRIVATE_KEY_PASSWORD from Keychain ($DEFAULT_KEY_KEYCHAIN_SERVICE)"
  fi
fi

if [[ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
  echo "[build] signing enabled"
else
  echo "[build] ERROR: no signing key available." >&2
  echo "[build]        Expected one of:" >&2
  echo "[build]          - file at $DEFAULT_KEY_PATH" >&2
  echo "[build]          - env var TAURI_SIGNING_PRIVATE_KEY (key contents)" >&2
  echo "[build]          - env var TAURI_SIGNING_PRIVATE_KEY_PATH (path to key)" >&2
  echo "[build]        Generate one with:" >&2
  echo "[build]          cargo tauri signer generate -w $DEFAULT_KEY_PATH --ci" >&2
  echo "[build]        See RELEASING.md > Desktop app for the full walkthrough." >&2
  exit 1
fi

# --- Step 1: build the wheel + vendor Python. ---
# Tauri bundles `desktop/python/` as a resource (UI-075). Always rebuild
# the wheel + re-vendor so the bundled python matches the source tree.
echo "[build] (re)building flowstate wheel"
( cd "$REPO_ROOT" && rm -f dist/flowstate-*.whl && uv build --wheel ) >/dev/null

echo "[build] vendoring portable Python ($TRIPLE)"
bash "$SCRIPT_DIR/vendor_python.sh" "$TRIPLE"

# --- Step 2: cargo tauri build. ---
# `cargo tauri build` produces both the .app bundle and the .dmg by
# default (per tauri.conf.json `bundle.targets`). It expects to run from
# inside src-tauri/ in Tauri 2.x.
echo "[build] cargo tauri build --target $TRIPLE (this takes ~5-10 min on first run)"
( cd "$TAURI_DIR" && cargo tauri build --target "$TRIPLE" )

# --- Step 3: collect artifacts. ---
mkdir -p "$DIST_DIR"
BUILT_DIR="$REPO_ROOT/target/$TRIPLE/release/bundle"
DMG_SRC=$(ls "$BUILT_DIR/dmg"/*.dmg 2>/dev/null | head -1)
APP_SRC=$(ls -d "$BUILT_DIR/macos"/*.app 2>/dev/null | head -1)

if [[ -z "$DMG_SRC" ]]; then
  echo "ERROR: cargo tauri build did not produce a .dmg. Check $BUILT_DIR" >&2
  exit 1
fi

DMG_OUT="$DIST_DIR/Flowstate-${VERSION}-${SHORT_ARCH}.dmg"
cp "$DMG_SRC" "$DMG_OUT"

# UI-076: if Tauri produced a .sig file alongside the .dmg (signing was
# enabled), copy it next to the renamed DMG. Its contents are pasted
# into `desktop/updater/latest.json` for the GitHub Releases manifest.
SIG_SRC="${DMG_SRC}.sig"
SIG_OUT=""
if [[ -f "$SIG_SRC" ]]; then
  SIG_OUT="${DMG_OUT}.sig"
  cp "$SIG_SRC" "$SIG_OUT"
fi

# --- Step 4: report. ---
echo
echo "[build] done."
echo "  .app:  ${APP_SRC:-(missing)}"
echo "  .dmg:  $DMG_OUT"
if [[ -n "$SIG_OUT" ]]; then
  echo "  .sig:  $SIG_OUT"
fi
echo
if [[ -n "$APP_SRC" ]]; then
  printf '  app size: '; du -sh "$APP_SRC" | awk '{print $1}'
fi
printf '  dmg size: '; du -sh "$DMG_OUT" | awk '{print $1}'
echo
echo "Distribute the .dmg via GitHub Releases. First-launch users will hit"
echo "macOS Gatekeeper — see desktop/README.md for the right-click → Open"
echo "workaround."
if [[ -n "$SIG_OUT" ]]; then
  echo
  echo "Updater signature: paste the contents of"
  echo "  $SIG_OUT"
  echo "into desktop/updater/latest.json under platforms.<target>.signature"
  echo "and upload latest.json + the .dmg to the GitHub Release. (UI-076)"
fi
