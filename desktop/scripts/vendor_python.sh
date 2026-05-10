#!/usr/bin/env bash
# Vendor a portable Python runtime for the Flowstate menubar app (UI-075).
#
# Downloads a `python-build-standalone` install_only tarball pinned to
# CPYTHON_VERSION+PBS_TAG below, verifies its SHA256, extracts to
# `desktop/python/`, and installs the locally-built Flowstate wheel
# (`dist/flowstate-*.whl`) into it. The result is a self-contained
# Python tree that Tauri's `bundle.resources` ships inside `.app`.
#
# Usage:
#   bash desktop/scripts/vendor_python.sh                        # auto-detect host triple
#   bash desktop/scripts/vendor_python.sh aarch64-apple-darwin   # explicit triple
#
# Idempotent: skips re-download if the tarball is already cached and
# the SHA matches; wipes `desktop/python/` if the previously-vendored
# triple doesn't match the requested one.

set -euo pipefail

# Pin reproducibly. Bump these together when upgrading.
CPYTHON_VERSION="3.12.7"
PBS_TAG="20241016"

# Known-good SHA256s for the install_only tarballs at this tag. Source:
# https://github.com/indygreg/python-build-standalone/releases/tag/$PBS_TAG
# Looked up via `case` (not `declare -A`) because macOS still ships
# bash 3.2 by default, which lacks associative array support.
sha_for_triple() {
  case "$1" in
    aarch64-apple-darwin)
      echo "4c18852bf9c1a11b56f21bcf0df1946f7e98ee43e9e4c0c5374b2b3765cf9508" ;;
    x86_64-apple-darwin)
      echo "60c5271e7edc3c2ab47440b7abf4ed50fbc693880b474f74f05768f5b657045a" ;;
    *) return 1 ;;
  esac
}

KNOWN_TRIPLES="aarch64-apple-darwin x86_64-apple-darwin"

# Resolve repo paths from this script's location, not from cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_DIR/.." && pwd)"
PYTHON_DIR="$DESKTOP_DIR/python"
CACHE_DIR="$DESKTOP_DIR/.cache"

# Detect host triple if not passed.
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

if ! EXPECTED_SHA=$(sha_for_triple "$TRIPLE"); then
  echo "ERROR: no pinned SHA for triple '$TRIPLE'" >&2
  echo "Add it to sha_for_triple() in this script. Known triples:" >&2
  for t in $KNOWN_TRIPLES; do echo "  - $t" >&2; done
  exit 1
fi
TARBALL="cpython-${CPYTHON_VERSION}+${PBS_TAG}-${TRIPLE}-install_only.tar.gz"
TARBALL_URL="https://github.com/indygreg/python-build-standalone/releases/download/${PBS_TAG}/${TARBALL}"
TARBALL_PATH="$CACHE_DIR/$TARBALL"

# Stamp file records what's currently vendored. Used to detect arch
# mismatches when re-running with a different triple.
STAMP="$PYTHON_DIR/.vendor-stamp"
WANTED_STAMP="${CPYTHON_VERSION}+${PBS_TAG}-${TRIPLE}"

mkdir -p "$CACHE_DIR"

# --- Step 1: download & verify the tarball (skip if cached + valid). ---
verify_sha() {
  # macOS ships shasum; Linux usually ships sha256sum. Try both.
  local actual
  if command -v shasum >/dev/null 2>&1; then
    actual=$(shasum -a 256 "$1" | awk '{print $1}')
  else
    actual=$(sha256sum "$1" | awk '{print $1}')
  fi
  [[ "$actual" == "$EXPECTED_SHA" ]]
}

if [[ -f "$TARBALL_PATH" ]] && verify_sha "$TARBALL_PATH"; then
  echo "[vendor_python] cached tarball OK: $TARBALL_PATH"
else
  echo "[vendor_python] downloading $TARBALL_URL"
  curl --fail --location --progress-bar -o "$TARBALL_PATH" "$TARBALL_URL"
  if ! verify_sha "$TARBALL_PATH"; then
    echo "ERROR: SHA256 mismatch for $TARBALL" >&2
    echo "  expected: $EXPECTED_SHA" >&2
    rm -f "$TARBALL_PATH"
    exit 1
  fi
  echo "[vendor_python] SHA256 OK"
fi

# --- Step 2: extract to desktop/python/. ---
# Wipe an existing tree if it doesn't match what we want (different triple
# or different version). Otherwise skip extraction — pip-install is cheap
# enough to redo every run.
if [[ -f "$STAMP" ]] && [[ "$(cat "$STAMP")" != "$WANTED_STAMP" ]]; then
  echo "[vendor_python] existing python/ stamp '$(cat "$STAMP")' != wanted '$WANTED_STAMP'; wiping"
  rm -rf "$PYTHON_DIR"
fi

if [[ ! -d "$PYTHON_DIR" ]]; then
  echo "[vendor_python] extracting to $PYTHON_DIR"
  TMP_EXTRACT=$(mktemp -d)
  tar -xzf "$TARBALL_PATH" -C "$TMP_EXTRACT"
  # The install_only tarball extracts to a top-level directory called `python`.
  # Move its contents to our target so the layout is `desktop/python/bin/...`.
  mv "$TMP_EXTRACT/python" "$PYTHON_DIR"
  rm -rf "$TMP_EXTRACT"
  echo "$WANTED_STAMP" >"$STAMP"
fi

PYTHON_BIN="$PYTHON_DIR/bin/python3"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: extracted but $PYTHON_BIN is missing or not executable" >&2
  exit 1
fi

echo "[vendor_python] python: $($PYTHON_BIN --version)"

# --- Step 3: install Flowstate wheel into the vendored Python. ---
WHEEL_GLOB="$REPO_ROOT/dist/flowstate-*.whl"
# shellcheck disable=SC2206
WHEELS=( $WHEEL_GLOB )
if [[ ! -f "${WHEELS[0]}" ]]; then
  echo "ERROR: no wheel found at $WHEEL_GLOB" >&2
  echo "  run 'uv build --wheel' first" >&2
  exit 1
fi
WHEEL="${WHEELS[0]}"
echo "[vendor_python] installing $WHEEL"

# Use --upgrade so re-running picks up wheel changes; --no-deps would
# break if Flowstate's deps weren't already vendored, so let pip resolve.
"$PYTHON_BIN" -m pip install --quiet --upgrade --force-reinstall "$WHEEL"

# --- Step 4: post-install pruning (UI-079). ---
# `claude_agent_sdk` ships a 196 MB bundled `claude` Mach-O binary at
# `_bundled/claude` for its own subprocess management. The desktop app
# defaults to AcpHarness (which spawns `claude-agent-acp` from PATH, not
# the SDK's bundled `claude`), so the binary is dead weight in the .app.
# Strip it to drop ~196 MB from the vendored tree.
#
# Trade-off: flows that opt into ``harness="sdk"`` will need a `claude`
# binary on PATH at runtime. The SDK's own resolver falls back to PATH
# when the bundled copy is missing. Document the trade-off in
# `desktop/README.md` and the UI-079 issue.
SDK_BUNDLED="$PYTHON_DIR/lib/python3.12/site-packages/claude_agent_sdk/_bundled"
if [[ -d "$SDK_BUNDLED" ]]; then
  pre_size=$(du -sk "$PYTHON_DIR" | awk '{print $1}')
  rm -rf "$SDK_BUNDLED"
  post_size=$(du -sk "$PYTHON_DIR" | awk '{print $1}')
  freed=$((pre_size - post_size))
  echo "[vendor_python] stripped claude_agent_sdk/_bundled ($((freed / 1024)) MB freed)"
fi

# Sanity-check: confirm we can run flowstate from the vendored interpreter.
"$PYTHON_BIN" -m flowstate --version

echo "[vendor_python] done — $PYTHON_DIR is ready for Tauri bundle.resources"
