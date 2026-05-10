# [UI-075] Bundle portable Python via `python-build-standalone`

## Domain
ui (with light shared touch)

## Status
done

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
- [x] `desktop/scripts/vendor_python.sh <triple>` downloads and extracts a `python-build-standalone` build for the requested target triple (`aarch64-apple-darwin`, `x86_64-apple-darwin`) into `desktop/python/`. Idempotent.
- [x] The script then installs the locally-built Flowstate wheel into the vendored Python: `desktop/python/bin/python3 -m pip install ../../dist/flowstate-*.whl`.
- [x] `desktop/python/` is gitignored (already added in UI-074) but listed in `tauri.conf.json` `bundle.resources` so it lands inside `Flowstate.app/Contents/Resources/python/`.
- [x] `main::resolve_python` resolves the bundled Python via Tauri's resource resolver (`app.path().resolve("python/bin/python3", BaseDirectory::Resource)`) when in production, falls back to system `python3` when the resource is missing, and honors `FLOWSTATE_PYTHON` as a dev override. (`FlowstateServer::new` itself was simplified to take a pre-resolved path so it stays decoupled from Tauri.)
- [x] Total bundled size is documented — actual: **~330 MB** for the vendored tree; target was < 120 MB but `claude-agent-sdk` ships a 196 MB bundled `claude` Mach-O which dominates. Trimming this is filed as UI-079 follow-up.
- [x] README adds a one-line "the desktop app ships its own Python — no install needed" note (under Install → Desktop app subsection).

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

### Vendor script run (aarch64-apple-darwin)
```
$ bash desktop/scripts/vendor_python.sh aarch64-apple-darwin
[vendor_python] downloading https://github.com/indygreg/python-build-standalone/releases/download/20241016/cpython-3.12.7+20241016-aarch64-apple-darwin-install_only.tar.gz
[vendor_python] SHA256 OK
[vendor_python] extracting to /Users/theophanerupin/code/flowstate/desktop/python
[vendor_python] python: Python 3.12.7
[vendor_python] installing /Users/theophanerupin/code/flowstate/dist/flowstate-0.1.0-py3-none-any.whl
flowstate 0.1.0
[vendor_python] done — /Users/theophanerupin/code/flowstate/desktop/python is ready for Tauri bundle.resources
```

### Vendored Python smoke check
```
$ desktop/python/bin/python3 --version
Python 3.12.7

$ desktop/python/bin/python3 -m flowstate --version
flowstate 0.1.0
```

### Idempotence check
A second `bash desktop/scripts/vendor_python.sh aarch64-apple-darwin` printed
`[vendor_python] cached tarball OK` and skipped re-download — the on-disk
SHA matched the expected one. Re-running with a different triple wipes
`desktop/python/` (gated by the `.vendor-stamp` file).

### Size breakdown
```
$ du -sh desktop/python
330M    desktop/python

$ du -sh desktop/python/lib/python3.12/site-packages/* | sort -rh | head -3
196M    .../site-packages/claude_agent_sdk          # bundled claude Mach-O binary
 24M    .../site-packages/cryptography
2.1M    .../site-packages/flowstate
```
The vendored Python interpreter itself is ~50 MB; the rest is Flowstate's
transitive dep tree, dominated by claude-agent-sdk's bundled `claude`
binary. Stripping that to spawn the user's PATH `claude` instead is filed
as UI-079.

### Rust resolution path verified
`cargo build --manifest-path desktop/src-tauri/Cargo.toml` is clean
(no warnings, no errors). The new `resolve_python(app)` helper in
`main.rs` falls back through three tiers: `FLOWSTATE_PYTHON` env var →
`Resource/python/bin/python3` (only present in production `.app` bundle)
→ `python3` from PATH.

### Out of scope for this verification
Producing a real `.app` via `cargo tauri build` and launching it on a
machine without system Python is **UI-077**'s job (build pipeline +
unsigned DMG). UI-075 lands the vendor script + bundle wiring; UI-077
will exercise the bundled-Python path end-to-end on a clean machine.

## Completion Checklist
- [ ] `vendor_python.sh` lands and is idempotent
- [ ] `bundle.resources` includes `python/**`
- [ ] `FlowstateServer` resolves bundled Python
- [ ] Final `.app` size measured and documented
- [ ] E2E verified on a machine without system Python
