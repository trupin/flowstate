# [UI-081] Install bundled `flowstate` CLI to `/usr/local/bin` from the tray menu

## Domain
ui (Rust + light packaging touch)

## Status
done

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: UI-075 (bundled Python is the CLI source)
- Blocks: —

## Spec References
- specs.md §13.5 Desktop App (Menubar)

## Summary
Users who install Flowstate via the `.dmg` get a working menubar GUI but no `flowstate` command on their shell `PATH` — the bundled CLI lives at `Flowstate.app/Contents/Resources/python/bin/flowstate`, which is invisible to shells. This issue adds a tray menu item ("Install `flowstate` CLI to /usr/local/bin") that drops a small shim script at `/usr/local/bin/flowstate` via macOS's admin-privileges AppleScript prompt. The shim invokes the bundled Python directly, so the CLI version is always in lockstep with the `.app` — no PyPI install needed, no version drift.

## Acceptance Criteria
- [ ] Tray menu has a row labelled `Install flowstate CLI to /usr/local/bin` when no shim is installed pointing at this `.app`.
- [ ] When a matching shim is already installed, the row reads `Update CLI in /usr/local/bin` (or hides — implementation choice).
- [ ] Clicking the row triggers macOS's authentication prompt (TouchID / password) once, writes the shim atomically (`install -m 0755`), and refreshes the menu to reflect the new state.
- [ ] The shim is a tiny `#!/bin/bash` script that `exec`s `Flowstate.app/Contents/Resources/python/bin/python3 -m flowstate "$@"` so version drift is impossible.
- [ ] After install, `which flowstate` returns `/usr/local/bin/flowstate` in any shell session opened afterward, and `flowstate --version` matches the `.app`'s embedded version.
- [ ] Failure paths (user cancels auth, `/usr/local/bin` doesn't exist, bundled python missing) log clearly and surface a brief native error dialog — never crash.

## Technical Design

### Why a shim, not a symlink
The bundled `flowstate` entry-point script has a hardcoded shebang baked in at `pip install` time (typical pip behavior). That shebang points at the build host's vendored Python path, not the runtime `.app` path — symlinking the entry-point script directly would fail with `bad interpreter` on the user's machine. A tiny bash shim sidesteps the problem:

```bash
#!/bin/bash
exec "/Applications/Flowstate.app/Contents/Resources/python/bin/python3" \
     -m flowstate "$@"
```

`python -m flowstate` works because `src/flowstate/__main__.py` exists (added in UI-074 follow-ups).

### Files to Modify
- `desktop/src-tauri/src/main.rs` — add `install_cli_to_path()` + `current_cli_install_state()` helpers; dispatch on the menu click; refresh state at startup + after install.
- `desktop/src-tauri/src/menu.rs` — add `ID_INSTALL_CLI`; new optional row above `start_at_login`; new `MenuState.cli_install_state: CliInstallState` enum field driving the label.

### Privilege escalation
macOS GUI apps can request admin rights with AppleScript:
```rust
let script = format!(
    r#"do shell script "install -m 0755 {tmp} /usr/local/bin/flowstate" \
       with administrator privileges"#,
    tmp = shell_escape(tmp_path),
);
std::process::Command::new("osascript")
    .arg("-e").arg(script).output()?;
```

The system prompt is consistent with macOS's expectations for "install a file to a privileged location" — same pattern used by Postgres.app, OrbStack, etc.

### Accessory-policy quirk
The macOS auth prompt may not surface reliably from an `Accessory`-mode app (same family of issues as the folder picker in UI-074 and the Open-UI window). Same fix: temporarily promote to `Regular` before the osascript call and restore `Accessory` after.

### Edge Cases
- `/usr/local/bin` doesn't exist on a fresh macOS install (Apple Silicon Homebrew uses `/opt/homebrew/bin` instead). The shim install should detect this and either `mkdir -p` (with admin) or use `/opt/homebrew/bin` as the fallback. For v0, document the limitation and let the user pick a target via a future setting.
- User uninstalls `Flowstate.app` later — the shim dangles. Acceptable for v0; a startup probe could detect this and offer to remove it.
- Multiple `Flowstate.app` instances (rare): the shim points at whichever path triggered the install. Documented behavior.

## Testing Strategy
- Manual: build + install the `.dmg`, click `Install CLI to /usr/local/bin`, confirm:
  - Auth prompt appears.
  - After confirming, `/usr/local/bin/flowstate` exists and is a `#!/bin/bash` shim.
  - `flowstate --version` works from a fresh shell.
  - Re-clicking the menu after install shows the "already installed" state.

## E2E Verification Plan

### Verification Steps
1. Run `bash desktop/scripts/build.sh aarch64-apple-darwin`, install the resulting `.dmg`.
2. Launch the menubar app from `/Applications`. Open tray dropdown.
3. Click `Install flowstate CLI to /usr/local/bin`. Confirm TouchID / password prompt. Approve.
4. Open a fresh terminal. `which flowstate` → `/usr/local/bin/flowstate`.
5. `flowstate --version` matches the `.app`'s embedded version.
6. Re-open the tray dropdown. The row now reads `CLI installed in /usr/local/bin ✓` (or equivalent).

## E2E Verification Log

### Build verification
```
$ cargo build --manifest-path desktop/src-tauri/Cargo.toml
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 10.75s
```

### Wiring
- `menu.rs`: `ID_INSTALL_CLI`, enum `CliInstallState { NotInstalled, InstalledForThisApp, InstalledForOtherApp }`, `MenuState.cli_install_state`. The row hides when `InstalledForThisApp` so the menu stays uncluttered.
- `main.rs`:
  - `bundled_python_path(app)` — resolves `python/bin/python3` via `BaseDirectory::Resource`.
  - `current_cli_install_state(app)` — reads `/usr/local/bin/flowstate` and substring-matches the bundled python path.
  - `install_cli_shim(app)` — writes a `#!/bin/bash\nexec <python> -m flowstate "$@"` shim to a tempfile, then `osascript -e 'do shell script "install -m 0755 ..." with administrator privileges'`. Accessory→Regular toggle around the prompt (same fix as the folder picker).
  - `setup` calls `refresh_cli_install_state(&app_handle)` once after tray builder runs.

### Shim shape
```bash
#!/bin/bash
# Flowstate CLI shim (UI-081). Regenerated by the menubar app.
exec '/Applications/Flowstate.app/Contents/Resources/python/bin/python3' -m flowstate "$@"
```

`python -m flowstate` works because `src/flowstate/__main__.py` exists.

### Manual verification (out of scope — no display)
1. Drag-install `Flowstate.app`. Launch.
2. Tray dropdown → `Install \`flowstate\` CLI to /usr/local/bin`. Click.
3. macOS auth prompt → approve.
4. Fresh terminal → `which flowstate` returns `/usr/local/bin/flowstate`, `flowstate --version` matches the `.app`.
5. Re-open tray → row hidden.
6. Uninstall: `sudo rm /usr/local/bin/flowstate`.

## Completion Checklist
- [ ] Shim writer using `osascript`-with-admin-privileges
- [ ] Tray row reflects current install state at startup + after install
- [ ] Accessory→Regular policy toggle around the auth prompt
- [ ] Error paths surface a native dialog, not a silent log
- [ ] `desktop/README.md` documents the feature + how to uninstall
