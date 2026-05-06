# Releasing Flowstate

This document describes how to cut a Flowstate release and publish it to
PyPI. The audience is maintainers; end users should not need to read this.

Flowstate releases are **manual** for v0.1 — there is no CI-driven publish
pipeline. Run everything locally on a trusted machine.

## Prerequisites

- **Python 3.12+** — the runtime requirement
- **Node 20+** and **npm** — the Hatchling UI build hook (SHARED-008) runs
  `npm ci && npm run build` inside `ui/` during `uv build` and bundles the
  resulting `ui/dist/*` into `src/flowstate/_ui_dist/` as package data. If
  Node is missing the build fails loudly (intentional — a UI-less wheel
  must not be shipped).
- **[uv](https://github.com/astral-sh/uv)** for building and publishing.
- A **PyPI account** with upload rights on the `flowstate` project, plus
  an API token stored in `~/.pypirc` or `UV_PUBLISH_TOKEN` env var.
- A **TestPyPI account** for the dry-run upload.
- All tests green on `main`: `uv run pytest tests/dsl tests/state tests/server`
  plus a spot-check on `tests/engine/`.
- Working tree clean, no uncommitted changes.

## Release procedure

```bash
# 1. Bump version
vim pyproject.toml                # update `version = "X.Y.Z"`
git add pyproject.toml
git commit -m "Bump version to X.Y.Z"

# 2. Clean build
rm -rf dist src/flowstate/_ui_dist ui/dist ui/node_modules
uv build                          # produces dist/flowstate-X.Y.Z.{whl,tar.gz}

# 3. Smoke test the wheel's UI bundle
./scripts/verify_wheel_ui.sh dist/flowstate-X.Y.Z-py3-none-any.whl
# Expected: "PASS: ... contains a bundled UI"

# 4. Install the wheel into a throwaway venv and smoke test the full
#    user journey (wheel-install → init → check → server → /health).
rm -rf /tmp/fs-release-venv /tmp/fs-release-project /tmp/fs-release-data
uv venv /tmp/fs-release-venv
uv pip install --python /tmp/fs-release-venv/bin/python \
    dist/flowstate-X.Y.Z-py3-none-any.whl
/tmp/fs-release-venv/bin/flowstate --version    # expect "flowstate X.Y.Z"
mkdir /tmp/fs-release-project && cd /tmp/fs-release-project
echo '{}' > package.json
/tmp/fs-release-venv/bin/flowstate init
/tmp/fs-release-venv/bin/flowstate check flows/example.flow
FLOWSTATE_DATA_DIR=/tmp/fs-release-data \
    nohup /tmp/fs-release-venv/bin/flowstate server --port 9090 \
    > /tmp/fs-release-server.log 2>&1 &
sleep 4
curl -sf http://127.0.0.1:9090/health | python3 -m json.tool
curl -sf http://127.0.0.1:9090/ | head -3       # expect <!doctype html>
kill %1
rm -rf /tmp/fs-release-venv /tmp/fs-release-project /tmp/fs-release-data \
       /tmp/fs-release-server.log
cd -

# 5. Upload to TestPyPI first
UV_PUBLISH_URL=https://test.pypi.org/legacy/ uv publish dist/*

# 6. Install from TestPyPI and re-run steps 4 to validate the round-trip
rm -rf /tmp/fs-testpypi-venv /tmp/fs-testpypi-project /tmp/fs-testpypi-data
uv venv /tmp/fs-testpypi-venv
uv pip install --python /tmp/fs-testpypi-venv/bin/python \
    --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    flowstate==X.Y.Z
/tmp/fs-testpypi-venv/bin/flowstate --version
# ... repeat init/check/server journey ...

# 7. If TestPyPI works, publish to production PyPI
uv publish dist/*

# 8. Tag and push
git tag vX.Y.Z
git push --tags
```

## `[lumon]` extra

The `lumon` optional extra pulls Lumon from a git URL, which PyPI does not
allow in `Requires-Dist`. The extra is present in the wheel metadata under
`Provides-Extra: lumon` with a `Requires-Dist: lumon @ git+... ; extra == 'lumon'`
line — this is permitted by PEP 508 because the git URL is gated behind the
extra, not a hard dependency of the core package.

When installing from PyPI without the extra, users get core Flowstate with
zero git dependencies. When installing with `pip install 'flowstate[lumon]'`,
pip resolves the git URL and installs Lumon. If that fails (e.g., private
repo inaccessible), the user sees a clear error and can retry without the
extra.

## Rollback

If a release is broken, do NOT delete it from PyPI (PyPI does not allow
reusing version numbers). Instead:

1. Yank the release on PyPI: `uv publish --yank flowstate X.Y.Z "reason"`
   (or via the PyPI web UI).
2. Bump the version and release a fix: `X.Y.Z+1`.
3. The yanked version stays installable with an explicit `==X.Y.Z` pin but
   is invisible to pip's resolver.

## Out of scope for v0.1 releases

- No CI-driven release (GitHub Actions for `uv build` + publish).
- No signed releases (no sigstore/PGP yet).
- No Homebrew formula, no Docker image, no systemd units.
- No release notes automation — write them by hand in the GitHub Release.

## Desktop app

> **Status: TODO — not yet automated.** UI-074 landed only the v0 scaffold (Tauri project, server supervisor, /health poller, tray menu). The actual build/release pipeline is tracked in UI-075 (bundled Python), UI-076 (auto-updater + Tauri pubkey), and UI-077 (unsigned `.dmg` build script + this section's walkthrough).

When UI-077 lands, this section will be filled in with concrete steps. The intended shape:

```bash
# (TODO — UI-077)
# 1. Vendor portable Python via python-build-standalone (TODO — UI-075):
#    bash desktop/scripts/vendor_python.sh aarch64-apple-darwin
# 2. Install the freshly built flowstate wheel into the vendored Python:
#    desktop/python/bin/python3 -m pip install dist/flowstate-X.Y.Z-*.whl
# 3. Build the unsigned .app + .dmg:
#    bash desktop/scripts/build.sh
#    -> writes desktop/dist/Flowstate.dmg
# 4. Bump the Tauri updater manifest (TODO — UI-076):
#    edit desktop/updater/latest.json, set version + .dmg URL + signature
# 5. Upload the .dmg to GitHub Releases alongside the wheel/sdist.
# 6. Document the Gatekeeper workaround in the release notes:
#    "First launch: right-click Flowstate.app → Open → Open anyway,
#     or run `xattr -d com.apple.quarantine /Applications/Flowstate.app`."
```

**Distribution is unsigned.** Apple Developer cert + notarization is intentionally deferred — see specs.md §13.5 for the rationale. If/when the project gets a Developer ID, add a notarization step to UI-077 and update this section.

For now, contributors who want to try the menubar app build it from source:

```bash
# Prereqs: Rust toolchain (cargo 1.77+) and Flowstate installed on PATH.
cd desktop/src-tauri
cargo check                   # compiles (this is the v0 gate)
# cargo tauri dev             # interactive run — requires a display
# cargo tauri build           # local unsigned bundle — requires a display
```
