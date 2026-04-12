#!/usr/bin/env bash
#
# verify_wheel_ui.sh — smoke test that a built Flowstate wheel contains a
# bundled React UI. Run during release or CI before publishing to PyPI.
#
# Usage:
#   ./scripts/verify_wheel_ui.sh dist/flowstate-0.1.0-py3-none-any.whl
#
# Exits 0 if the wheel contains flowstate/_ui_dist/index.html (the UI
# bundle entry point); non-zero otherwise. Prints a short diagnostic on
# failure.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <path-to-flowstate-*.whl>" >&2
    exit 2
fi

WHEEL="$1"

if [[ ! -f "$WHEEL" ]]; then
    echo "error: wheel file not found: $WHEEL" >&2
    exit 2
fi

# A wheel is a zip file. List its contents and look for the UI entry point.
if ! unzip -l "$WHEEL" >/dev/null 2>&1; then
    echo "error: $WHEEL does not appear to be a valid zip/wheel" >&2
    exit 2
fi

if ! unzip -l "$WHEEL" | grep -q "flowstate/_ui_dist/index\.html"; then
    echo "FAIL: $WHEEL does not contain flowstate/_ui_dist/index.html" >&2
    echo "" >&2
    echo "The Hatchling build hook (SHARED-008) should have bundled the" >&2
    echo "React UI into the wheel as package data. Common causes:" >&2
    echo "  - Node/npm not installed at build time" >&2
    echo "  - FLOWSTATE_SKIP_UI_BUILD=1 was set (escape hatch for dev builds)" >&2
    echo "  - ui/ directory missing from the source tree" >&2
    echo "" >&2
    echo "Rebuild with a clean environment: rm -rf dist && uv build" >&2
    exit 1
fi

if ! unzip -l "$WHEEL" | grep -q "flowstate/_ui_dist/assets/"; then
    echo "WARN: $WHEEL contains index.html but no assets/ directory." >&2
    echo "The UI bundle may be incomplete; check the ui/ build output." >&2
    # Non-fatal — index.html without assets is weird but not definitively broken.
fi

echo "PASS: $WHEEL contains a bundled UI (flowstate/_ui_dist/index.html)"
exit 0
