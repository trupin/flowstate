# [UI-075] Bundle portable Python via `python-build-standalone`

## Domain
ui (with light shared touch)

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: UI-074 (Tauri scaffold — done)
- Blocks: UI-077 (DMG build pipeline — needs the bundled Python on disk)

## Spec References
- specs.md §13.5 Desktop App (Menubar)

## Summary
The v0 menubar app shells out to `python3 -m flowstate` from `PATH`, which assumes the user installed Flowstate via `pipx` or `uv tool install`. That's fine for developers but not for end users who download a `.dmg` and expect it to "just work". This issue replaces the system-Python dependency with a portable Python distribution from [python-build-standalone](https://github.com/indygreg/python-build-standalone), installed into `desktop/python/` at build time and bundled inside the `.app` via Tauri's `bundle.resources`.

## Acceptance Criteria
- [ ] `desktop/scripts/vendor_python.sh <triple>` downloads and extracts a `python-build-standalone` build for the requested target triple (`aarch64-apple-darwin`, `x86_64-apple-darwin`) into `desktop/python/`. Idempotent.
- [ ] The script then installs the locally-built Flowstate wheel into the vendored Python: `desktop/python/bin/python3 -m pip install ../../dist/flowstate-*.whl`.
- [ ] `desktop/python/` is gitignored (already added in UI-074) but listed in `tauri.conf.json` `bundle.resources` so it lands inside `Flowstate.app/Contents/Resources/python/`.
- [ ] `FlowstateServer::new` resolves the bundled Python via Tauri's resource resolver (`app_handle.path().resolve("python/bin/python3", BaseDirectory::Resource)`) when in production, and falls back to system `python3` only when the resource is missing (dev mode).
- [ ] Total bundled size is documented (target: < 120 MB for `.app`, ideally ~80 MB).
- [ ] README adds a one-line "the desktop app ships its own Python — no install needed" note.

## Technical Design

### Files to Create/Modify
- `desktop/scripts/vendor_python.sh` (new) — downloads the right `python-build-standalone` tarball, verifies the SHA256 against the upstream metadata, extracts to `desktop/python/`.
- `desktop/src-tauri/tauri.conf.json` — add `python/**` to `bundle.resources`.
- `desktop/src-tauri/src/server.rs` — change `FlowstateServer::new` to take an `AppHandle` (or a resolved python path), and resolve the bundled Python via `app.path().resolve(...)`.
- `desktop/src-tauri/src/main.rs` — pass the resolved python path into `FlowstateServer::new`.

### Key Implementation Details
- Use `python-build-standalone`'s "install_only" tarballs — they don't include test suites or static libraries and are roughly half the size.
- Pin a specific build tag in `vendor_python.sh` (e.g., `cpython-3.12.7+20250101`) so reproducibility is in the maintainer's hands, not the latest tag's.
- The bundled Python ships `pip`; the vendor script uses it to install the locally-built wheel. We do not depend on the user having `pip`.

### Edge Cases
- If `desktop/python/` exists but for the wrong arch, the script wipes and re-vendors.
- Code-signing: the bundled Python's binaries inherit the (lack of) signing of the parent `.app`. Future signing work (P3) will need to ad-hoc-sign each `.dylib` inside `python/lib/` to satisfy macOS's hardened runtime.

## Testing Strategy
- Manual: vendor + build + run on a clean machine without system Python; confirm the menubar app starts the server.
- Integration: a smoke test that runs `desktop/python/bin/python3 -c "import flowstate; print(flowstate.__version__)"` after vendoring.

## E2E Verification Plan

### Verification Steps
1. `bash desktop/scripts/vendor_python.sh aarch64-apple-darwin`
2. `desktop/python/bin/python3 --version` — confirms 3.12+.
3. `desktop/python/bin/python3 -m flowstate --version` — confirms Flowstate is installed.
4. `cargo tauri build` — produces a `.app` whose `Contents/Resources/python/` contains the vendored Python.
5. Launch the `.app` on a machine without `python3` on PATH; the menubar app starts the server successfully.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `vendor_python.sh` lands and is idempotent
- [ ] `bundle.resources` includes `python/**`
- [ ] `FlowstateServer` resolves bundled Python
- [ ] Final `.app` size measured and documented
- [ ] E2E verified on a machine without system Python
