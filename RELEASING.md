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

The macOS menubar app is built from `desktop/` and shipped as an unsigned
`.dmg` alongside the PyPI release. The pipeline is fully scripted —
`desktop/scripts/build.sh` produces a versioned DMG ready to upload to
GitHub Releases. Apple Developer cert + notarization is intentionally
deferred (see `specs.md §13.5` for the rationale).

### Prerequisites (one-time)

```bash
# Rust toolchain.
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
. "$HOME/.cargo/env"

# Tauri CLI.
cargo install tauri-cli --locked --version "^2.0"

# jq (build script reads version from tauri.conf.json).
brew install jq

# UI-076: updater signing keypair. Generate ONCE per project; the
# pubkey is committed in `tauri.conf.json` under
# `plugins.updater.pubkey` and is shared by every release. The
# privkey signs every release — losing it means losing the auto-
# update path for installed copies (you'd have to re-key + re-publish
# from scratch). Treat it like an SSH host key.
mkdir -p ~/.tauri
cargo tauri signer generate -w ~/.tauri/flowstate.key --ci
# -> ~/.tauri/flowstate.key      (private — store in 1Password / vault)
# -> ~/.tauri/flowstate.key.pub  (public  — already embedded in tauri.conf.json)
```

### Per-release procedure

```bash
# 0. Bump the desktop version. Single source of truth: tauri.conf.json.
#    Keep it in sync with pyproject.toml's flowstate version where
#    practical, but they don't have to match — the .dmg has its own
#    cadence and may iterate on bundling fixes between PyPI releases.
vim desktop/src-tauri/tauri.conf.json   # update "version": "X.Y.Z"

# 1. Export the signing key path so cargo tauri build signs the bundle.
#    Without this, build.sh prints a WARNING: bundle will be UNSIGNED
#    and produces no .sig — never publish an unsigned release.
export TAURI_SIGNING_PRIVATE_KEY_PATH="$HOME/.tauri/flowstate.key"
# If you set a password during `signer generate`, also export:
# export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="..."

# 2. Build for Apple Silicon. (Builds for both arches in two passes —
#    universal binaries are a stretch goal, see UI-077.)
bash desktop/scripts/build.sh aarch64-apple-darwin
# -> desktop/dist/Flowstate-X.Y.Z-aarch64.dmg
# -> desktop/dist/Flowstate-X.Y.Z-aarch64.dmg.sig    (UI-076)

# 3. Build for Intel.
bash desktop/scripts/build.sh x86_64-apple-darwin
# -> desktop/dist/Flowstate-X.Y.Z-x86_64.dmg
# -> desktop/dist/Flowstate-X.Y.Z-x86_64.dmg.sig

# 4. Build the updater manifest from the example + signatures.
#    `latest.json` is what `tauri-plugin-updater` fetches to decide
#    "is there a newer version?". Tag URL convention: vX.Y.Z.
cp desktop/updater/latest.json.example desktop/updater/latest.json
vim desktop/updater/latest.json
#   - bump "version" to X.Y.Z
#   - bump "pub_date" to current ISO 8601 UTC
#   - replace each platform's "signature" field with the contents of
#     desktop/dist/Flowstate-X.Y.Z-<arch>.dmg.sig (cat the file, paste verbatim)
#   - bump each platform's "url" to the v X.Y.Z download URL

# 5. Sanity-check both DMGs locally:
#    a) drag-install onto /Applications
#    b) right-click → Open → Open anyway (Gatekeeper)
#    c) menubar icon appears, "Switch Project…" works, "Open UI" works
#    d) Quit cleanly stops the spawned server (`ps aux | grep flowstate`)

# 6. Upload both DMGs + latest.json to the GitHub Release alongside the
#    PyPI wheel. Release notes should include the Gatekeeper workaround
#    verbatim:
#       "First launch: right-click Flowstate.app → Open → Open anyway,
#        or run `xattr -d com.apple.quarantine /Applications/Flowstate.app`"
gh release upload vX.Y.Z \
    desktop/dist/Flowstate-X.Y.Z-aarch64.dmg \
    desktop/dist/Flowstate-X.Y.Z-x86_64.dmg \
    desktop/updater/latest.json
# Existing installs poll
# https://github.com/<org>/flowstate/releases/latest/download/latest.json
# on next launch and surface "Update to X.Y.Z — restart to install"
# in the tray within ~30s.
```

### What the script does internally

1. Rebuilds the Flowstate wheel via `uv build --wheel`.
2. Calls `desktop/scripts/vendor_python.sh <triple>` (UI-075) to populate
   `desktop/python/` with a `python-build-standalone` runtime + the
   freshly-built wheel installed into it. Tarball is SHA256-verified
   and cached at `desktop/.cache/`.
3. Runs `cargo tauri build --target <triple>` to produce the `.app` and
   `.dmg`. Tauri reads `bundle.resources` from `tauri.conf.json` to ship
   the vendored Python inside `Contents/Resources/python/`.
4. Copies the DMG to `desktop/dist/Flowstate-X.Y.Z-<short-arch>.dmg`,
   prints the `.app` and `.dmg` sizes.

### Known sizes

After UI-079 stripped `claude_agent_sdk`'s 196 MB embedded `claude`
binary, the artifacts are roughly:

- `.app`: ~150 MB (mostly the bundled `python-build-standalone` runtime).
- `.dmg`: ~30-50 MB after DMG compression.

Re-run `build.sh` to confirm with `du -sh` — sizes shift slightly with
each Python or dependency update.

### Rollback

DMGs published on GitHub Releases can be deleted (unlike PyPI). If a
release is broken: delete the asset from the Release page, fix the bug,
re-run `build.sh`, re-upload. No version bump required as long as no
user has installed the broken artifact yet.

### What's deferred (later P3 work)

- **Code signing + notarization** — requires a paid Apple Developer
  account ($99/yr). When we sign up, add `signingIdentity` +
  `notarize` config to `tauri.conf.json` and a `signing` step to
  `build.sh`. Removes the right-click → Open friction.
- **Universal binaries** (`lipo`-merge of aarch64 + x86_64). Avoids
  shipping two DMGs per release. See UI-077 follow-up.
- **CI-driven release** (GitHub Actions building the DMG on macOS
  runners). Currently maintainers run `build.sh` locally on a
  trusted machine.
