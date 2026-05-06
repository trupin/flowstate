# [UI-077] Unsigned `.dmg` build pipeline + `desktop/scripts/build.sh`

## Domain
ui (with shared/release touch)

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: UI-074 (scaffold — done), UI-075 (bundled Python — gates the build script)
- Blocks: UI-076 (auto-updater needs a real .dmg to update _to_)

## Spec References
- specs.md §13.5 Desktop App (Menubar)
- RELEASING.md "Desktop app" section (TODO; this issue fills it in)

## Summary
Produce a reproducible build script that turns the `desktop/` source tree into an unsigned `Flowstate.dmg` ready to upload to GitHub Releases. The script must vendor the portable Python (via UI-075's `vendor_python.sh`), build the `.app` with `cargo tauri build`, and wrap it in a `.dmg` — no Apple Developer cert, no notarization. The first-launch Gatekeeper workaround is documented in `RELEASING.md`.

## Acceptance Criteria
- [ ] `desktop/scripts/build.sh` produces `desktop/dist/Flowstate-X.Y.Z-aarch64.dmg` (and ideally `x86_64` and a universal binary too — universal is a stretch goal).
- [ ] The script pulls the version from `desktop/src-tauri/tauri.conf.json` (single source of truth) so version bumps don't get out of sync.
- [ ] No `signingIdentity` is set; the build is intentionally unsigned.
- [ ] Output `.app` size is measured and printed at the end of the build.
- [ ] `RELEASING.md` "Desktop app" section is rewritten to be a real walkthrough (replacing the current TODO placeholder).
- [ ] README (or a new `desktop/README.md`) documents the Gatekeeper workaround end users will see on first launch.

## Technical Design

### Files to Create/Modify
- `desktop/scripts/build.sh` (new).
- `desktop/scripts/sign_macos_libs.sh` (new, optional) — ad-hoc-signs the bundled Python's `.dylib`s if the user has `codesign` available; without this, hardened-runtime macOS builds may fail to load the bundled Python.
- `RELEASING.md` — fill in the "Desktop app" section with the build/upload walkthrough.
- `desktop/README.md` (new) — first-launch Gatekeeper instructions for end users.

### Key Implementation Details
```bash
# desktop/scripts/build.sh sketch
set -euo pipefail
ARCH="${1:-aarch64-apple-darwin}"
VERSION=$(jq -r .version desktop/src-tauri/tauri.conf.json)

# 1. Vendor Python (UI-075).
bash desktop/scripts/vendor_python.sh "$ARCH"

# 2. Build the .app + .dmg with Tauri.
(cd desktop/src-tauri && cargo tauri build --target "$ARCH")

# 3. Move artifacts to desktop/dist/ with a versioned name.
mkdir -p desktop/dist
mv desktop/src-tauri/target/"$ARCH"/release/bundle/dmg/Flowstate_*.dmg \
   desktop/dist/Flowstate-"$VERSION"-"${ARCH%-apple-darwin}".dmg

du -sh desktop/dist/Flowstate-"$VERSION"-*.dmg
```

### Edge Cases
- `cargo tauri build` requires `tauri-cli` installed. The script checks for it and prints a clear "run `cargo install tauri-cli --version ^2`" message if missing.
- Universal binaries (lipo) are nice-to-have. If we ship arch-specific DMGs the user-visible URL needs to surface that — document in README.

## Testing Strategy
- Manual: run the script on a clean checkout; confirm a usable `.dmg` lands in `desktop/dist/`.
- Verify the produced `.app` launches on a clean macOS install (right-click → Open the first time).

## E2E Verification Plan

### Verification Steps
1. `bash desktop/scripts/build.sh aarch64-apple-darwin` — produces `desktop/dist/Flowstate-0.0.1-aarch64.dmg`.
2. Drag-install the `.dmg` onto a test Mac.
3. First launch: confirm Gatekeeper warning appears; right-click → Open succeeds.
4. Confirm the menubar app starts a server and `/health` responds.
5. Quit, confirm no leftover Python process.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `build.sh` produces a `.dmg`
- [ ] `.app` launches via right-click → Open
- [ ] Server lifecycle works end-to-end from the bundled `.app`
- [ ] `RELEASING.md` walkthrough fleshed out
- [ ] First-launch Gatekeeper instructions in README/desktop/README.md
