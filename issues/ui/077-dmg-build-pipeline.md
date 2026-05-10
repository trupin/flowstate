# [UI-077] Unsigned `.dmg` build pipeline + `desktop/scripts/build.sh`

## Domain
ui (with shared/release touch)

## Status
done

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
- [x] `desktop/scripts/build.sh` produces `desktop/dist/Flowstate-X.Y.Z-<arch>.dmg`. Both `aarch64-apple-darwin` and `x86_64-apple-darwin` are supported via the same script. Universal (lipo'd) binaries are deferred — left as a TODO in `RELEASING.md` because they require building/signing both arches in one pass and aren't worth the complexity for v0.
- [x] The script pulls the version from `desktop/src-tauri/tauri.conf.json` (single source of truth) via `jq`.
- [x] No `signingIdentity` is set; the build is intentionally unsigned.
- [x] Output `.app` and `.dmg` sizes are measured and printed at the end of the build.
- [x] `RELEASING.md` "Desktop app" section rewritten to a concrete per-release walkthrough (prereqs, build, sanity-check, upload, rollback, deferred work).
- [x] `desktop/README.md` (new) documents the Gatekeeper workaround (right-click → Open and the `xattr -d com.apple.quarantine` alternative). Linked from the main README's Install → Desktop app subsection.

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

### Build script run (aarch64-apple-darwin)
```
$ bash desktop/scripts/build.sh aarch64-apple-darwin
[build] flowstate-desktop 0.0.1 (aarch64-apple-darwin)
[build] (re)building flowstate wheel
[build] vendoring portable Python (aarch64-apple-darwin)
[vendor_python] cached tarball OK: ...
[vendor_python] python: Python 3.12.7
[vendor_python] installing .../dist/flowstate-0.1.0-py3-none-any.whl
flowstate 0.1.0
[vendor_python] done — .../desktop/python is ready for Tauri bundle.resources
[build] cargo tauri build --target aarch64-apple-darwin (this takes ~5-10 min on first run)
... (cargo compiles 200+ crates) ...
    Finished `release` profile [optimized] target(s) in 43.96s
       Built application at: target/aarch64-apple-darwin/release/flowstate-desktop
    Bundling Flowstate.app
    Bundling Flowstate_0.0.1_aarch64.dmg
     Running bundle_dmg.sh
    Finished 2 bundles at:
        target/aarch64-apple-darwin/release/bundle/macos/Flowstate.app
        target/aarch64-apple-darwin/release/bundle/dmg/Flowstate_0.0.1_aarch64.dmg

[build] done.
  .app:  target/aarch64-apple-darwin/release/bundle/macos/Flowstate.app
  .dmg:  desktop/dist/Flowstate-0.0.1-aarch64.dmg

  app size: 344M
  dmg size: 103M
```

### Bundled Python verification
The `.app`'s embedded interpreter (`Contents/Resources/python/bin/python3`)
runs Flowstate without any system Python on PATH:
```
$ Flowstate.app/Contents/Resources/python/bin/python3 -m flowstate --version
flowstate 0.1.0
```

### DMG validity
```
$ hdiutil imageinfo desktop/dist/Flowstate-0.0.1-aarch64.dmg
Format Description: UDIF read-only compressed (zlib)
Class Name: CUDIFDiskImage
Checksum Type: CRC32
Total Bytes: 403742208
```
The DMG is a valid macOS UDIF compressed disk image (~103 MB on disk;
expands to ~404 MB when mounted, dominated by the bundled Python tree).

### Sizes vs. UI-077 acceptance criteria
| artifact | size  |
|----------|-------|
| `.app`   | 344 MB|
| `.dmg`   | 103 MB|

The size is dominated by `claude_agent_sdk/_bundled/claude` (196 MB Mach-O,
inherited from UI-075). UI-079 trims this; once that lands the DMG should
drop into the 30-50 MB range.

### Gatekeeper / first-launch path
Cannot be exercised from this environment (no display). User-side test:
1. Drag `desktop/dist/Flowstate-0.0.1-aarch64.dmg` → Finder.
2. Drag `Flowstate.app` from the mounted DMG → `/Applications`.
3. Right-click `Flowstate.app` → Open → "Open anyway" in the Gatekeeper
   warning dialog.
4. Tray icon appears in menubar; `Switch Project…` → pick a Flowstate
   project; `Open UI` → window opens; `Quit` cleanly stops the spawned
   server.
The full Gatekeeper UX walkthrough lives in `desktop/README.md`.

### Out of scope (deferred to follow-ups)
- **Universal binaries** (`lipo` aarch64+x86_64) — would halve the
  released-asset count but doubles build time. Documented as a TODO in
  `RELEASING.md`.
- **`sign_macos_libs.sh` ad-hoc-signing helper** — not needed for the
  unsigned distribution path; only matters if/when we add codesign +
  notarization (deferred to a future P3 issue).
- **Gatekeeper UX validation** — manual user verification only; cannot
  be automated without a real macOS GUI session.

## Completion Checklist
- [ ] `build.sh` produces a `.dmg`
- [ ] `.app` launches via right-click → Open
- [ ] Server lifecycle works end-to-end from the bundled `.app`
- [ ] `RELEASING.md` walkthrough fleshed out
- [ ] First-launch Gatekeeper instructions in README/desktop/README.md
