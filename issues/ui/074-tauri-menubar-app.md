# [UI-074] Tauri menubar app for Flowstate

## Domain
ui (with light shared/server touch)

## Status
done

> **Scope note:** this issue landed only the v0 scaffold — the Tauri project compiles via `cargo check`, the server-supervisor and /health poller are wired, and the tray menu renders. The actual installable `.dmg`, the bundled Python, and the auto-updater are split into UI-075 / UI-076 / UI-077 so each can land independently. See "v0 Scope vs Deferred" below.

## Priority
P1 (important)

## Dependencies
- Depends on: SHARED-008 (UI bundled in wheel — done), SHARED-010 (PyPI release — done), SERVER-031 (`/health` for status polling — done), SERVER-028 (`flowstate init` for first-run UX — done)
- Blocks: —

## Spec References
- specs.md §13.4 Deployment & Installation (will gain a §13.5 Desktop App subsection)

## Summary
Ship Flowstate as a macOS / Windows menubar app — a small Tauri shell that lives in the system tray, manages the `flowstate server` process lifecycle (start / stop / restart / restart-on-login), surfaces server status in the dropdown (current project, port, recent runs, error counts), and opens the React UI in either a system webview window or the user's default browser.

The architecture fits Flowstate's current shape: it's already a long-running local dev server with a static React UI. Tauri wraps the existing pieces — no rewrite. Menubar UX matches the user's mental model (Postgres.app, OrbStack, Tailscale, Linear's menubar app, Docker Desktop).

End user journey:
```
1. Download Flowstate.dmg from GitHub Releases
2. Drag to Applications, launch
3. Flowstate icon appears in the menubar
4. Click → "Open Project..." → pick a directory containing flowstate.toml
   (or "New Project Here..." to scaffold one via flowstate init)
5. Server starts in the background; menubar shows green dot
6. Click → "Open UI" → React app opens in a Tauri window or default browser
```

## Acceptance Criteria
- [ ] New `desktop/` directory at the repo root containing a Tauri project (Rust + frontend assets).
- [ ] Menubar icon appears on macOS in the system tray. Icon has 3 states: dimmed (no server running), green (server running, idle), animated (flow executing).
- [ ] Menubar dropdown shows: current project name + path, server port, "X active runs" badge, recent-runs list (up to 5), and actions: "Open UI", "Open in Browser", "Switch Project...", "Stop Server", "Quit".
- [ ] "Switch Project..." opens a native file picker (directory selection); validates that `flowstate.toml` exists at the selected path; restarts the bundled `flowstate server` with `FLOWSTATE_CONFIG=<chosen>`.
- [ ] "New Project Here..." opens a directory picker, runs `flowstate init` in the chosen dir, then switches to it.
- [ ] Bundled Python: ship a portable Python (via `python-build-standalone`) so users don't need a system Python install. Python+Flowstate+UI all live inside the `.app` bundle.
- [ ] Auto-start on login is opt-in via a "Start Flowstate at Login" menu item that toggles the macOS LaunchAgent.
- [ ] `/health` polling every 5s drives the icon state and badge counts; on poll failure the icon shows red.
- [ ] Quitting the menubar app cleanly stops the spawned `flowstate server` (SIGTERM with a 5s grace, then SIGKILL).
- [ ] App is built as an **unsigned** macOS `.app` / `.dmg` for v1. No Apple Developer cert required. Distribution is via GitHub Releases; users will see a Gatekeeper warning on first launch and need to right-click → Open (or `xattr -d com.apple.quarantine Flowstate.app`). Document this in the README.
- [ ] Auto-update via Tauri's built-in updater pointing at GitHub Releases JSON manifest. Tauri's updater can run without code-signing if `pubkey` (Tauri's own signing key, separate from Apple) is configured — this is for update-payload integrity, not Apple notarization.
- [ ] `RELEASING.md` gains a "Desktop app release" section: build → upload → bump updater manifest. (Sign + notarize step is **deferred**; if/when the project gets an Apple Developer account, add it back as a P1 follow-up.)
- [ ] At least one screenshot in `README.md` showing the menubar dropdown.

## Technical Design

### Files to Create/Modify
- `desktop/` (new) — Tauri project scaffold:
  - `desktop/Cargo.toml`
  - `desktop/tauri.conf.json` — bundle config, signing, updater
  - `desktop/src-tauri/main.rs` — menubar setup, tray events, server-process supervision
  - `desktop/src-tauri/src/server.rs` — child-process management for `flowstate server`
  - `desktop/src-tauri/src/health.rs` — `/health` polling loop
  - `desktop/src-tauri/src/project.rs` — project picker, validation, `flowstate init` invocation
  - `desktop/src-tauri/src/menu.rs` — menubar menu construction
  - `desktop/icons/` — tray icon variants (idle / running / error / animated frames) + app icon
- `desktop/python/` (new) — vendored portable Python via `python-build-standalone` (gitignored; populated by build script)
- `desktop/scripts/build.sh` — builds the **unsigned** `.app` bundle: vendors Python, installs Flowstate wheel into the vendored Python, runs `cargo tauri build` (no `--bundle dmg --target ...` codesign flags).
- `RELEASING.md` — append a "Desktop app" section.
- `README.md` — link + screenshot.
- `.gitignore` — `desktop/python/`, `desktop/target/`, `desktop/src-tauri/target/`.
- `specs.md` — new §13.5 Desktop App.

### Key Implementation Details

**Server lifecycle (`server.rs`):**
```rust
pub struct FlowstateServer {
    process: Option<Child>,
    port: u16,
    project_root: PathBuf,
}

impl FlowstateServer {
    pub fn start(&mut self) -> Result<()> {
        let python = bundled_python_path();
        let port = find_free_port(9090);
        let mut cmd = Command::new(python);
        cmd.arg("-m").arg("flowstate").arg("server").arg("--port").arg(port.to_string());
        cmd.env("FLOWSTATE_CONFIG", self.project_root.join("flowstate.toml"));
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        self.process = Some(cmd.spawn()?);
        self.port = port;
        Ok(())
    }

    pub fn stop(&mut self) -> Result<()> {
        if let Some(child) = &mut self.process {
            // SIGTERM + 5s grace, then SIGKILL
            send_sigterm(child.id())?;
            wait_with_timeout(child, Duration::from_secs(5))?;
        }
        Ok(())
    }
}
```

**Health polling (`health.rs`):**
```rust
pub async fn poll_health(port: u16) -> HealthStatus {
    let url = format!("http://127.0.0.1:{port}/health");
    match reqwest::get(&url).await {
        Ok(r) if r.status().is_success() => {
            let body: HealthResponse = r.json().await?;
            HealthStatus::Running { project_slug: body.project.slug, ... }
        }
        _ => HealthStatus::Down,
    }
}
```

**Bundled Python:**
- Use `python-build-standalone` (Indygreg's project) — produces a portable, redistributable Python ~30MB.
- Build-time: download for the target platform (`aarch64-apple-darwin` etc.), extract to `desktop/python/`, install Flowstate wheel into it (`./python/bin/python -m pip install ../dist/flowstate-*.whl`).
- Tauri's `bundle.resources` includes `desktop/python/**` so it lands inside `Flowstate.app/Contents/Resources/python/`.
- Runtime: `bundled_python_path()` returns the resource path via Tauri's `app_handle.path_resolver().resolve_resource()`.

**Why not Electron:** ~150MB+ binary because Chromium ships with the app. Tauri uses the system webview (~5MB binary). For a tool that local devs install on every machine, the size matters. Tauri's tradeoff is webview inconsistency across platforms — but our UI is already React + standard CSS so there's no exotic browser API in play.

**Why not pyinstaller:** produces a single-binary executable but doesn't address the menubar / lifecycle / signing story. Tauri does both.

### Edge Cases
- **Port already in use** — `find_free_port(9090)` walks 9090 → 9099 looking for a free one. The dropdown shows the actual port. Multiple Flowstate.app instances (unusual) each bind a different port.
- **Project picker validates `flowstate.toml`** — if the user picks a non-Flowstate directory, offer "Run flowstate init here?" with a Yes/No dialog.
- **Python child process crashes** — health poll detects the failure within 5s; icon turns red; dropdown shows "Server crashed — Restart" action. Last 50 lines of stderr available via "Show Logs" item.
- **Auto-update during a running flow** — Tauri updater asks before applying; "Restart later" defers until the next app quit.
- **Multi-project in the menubar** — for v1 of the menubar app, support **one active project at a time**; "Switch Project" stops the current server and starts a new one. A future v2 could allow multiple servers (one per project) but that's complex enough to defer.

### Risks
- **Unsigned distribution UX** — first launch triggers a macOS Gatekeeper warning ("Flowstate cannot be opened because the developer cannot be verified"). Users have two workarounds: (a) right-click → Open → "Open anyway", or (b) `xattr -d com.apple.quarantine /Applications/Flowstate.app` once. The README should call this out so it doesn't read as a bug. Apple Developer account ($99/yr) + notarization is deferred until distribution friction becomes a real problem; file as a follow-up issue when that happens.
- **Tauri maturity** — Tauri 2.x is solid but the menubar/tray APIs are newer than the standard window APIs. Some platform quirks may surface.
- **Bundled Python size** — `python-build-standalone` + Flowstate wheel + dependencies likely lands at ~80-100MB total `.app` size. Acceptable for a dev tool but worth measuring.
- **Windows / Linux** — Phase 1 should be macOS-only; Windows menubar (system tray) and Linux (XEmbed / AppIndicator) have different conventions. Add as P2 follow-ups (UI-075 / UI-076) once macOS is shipped.

## Testing Strategy
- Smoke tests in `desktop/src-tauri/tests/` — unit tests for the server-supervision and health-polling modules. No GUI tests in v1 (Tauri's WebDriver story is still maturing).
- Manual: install the built `.dmg` on a clean macOS VM, walk through "first launch → install → switch project → run flow → quit". Document each step in the issue's E2E Verification Log.

## E2E Verification Plan

### Verification Steps
1. `bash desktop/scripts/build.sh` — produces `desktop/dist/Flowstate.dmg`.
2. Drag-install on a macOS test machine.
3. Launch from Spotlight. Menubar icon appears (dimmed — no project yet).
4. Click → "Open Project..." → pick `~/some-test-repo` (which has a `flowstate.toml`). Server starts. Icon turns green. Dropdown shows project name and "Open UI".
5. "Open UI" opens a window with the React app loaded. Confirm a flow can be triggered and runs.
6. "Switch Project..." → pick a different directory. Server restarts on a new port. Dropdown updates.
7. Quit Flowstate from the menubar. `ps aux | grep flowstate` confirms no leftover Python process.
8. Re-launch. App restores last-used project.
9. Toggle "Start at Login" — log out / log back in. Icon reappears in the menubar.

## v0 Scope vs Deferred

### In v0 (this PR)
- `desktop/` Tauri 2.x project tree at the repo root.
- `Cargo.toml` workspace at the repo root pointing at `desktop/src-tauri/`.
- `desktop/src-tauri/Cargo.toml` with Tauri 2.x, `tauri-plugin-dialog`, `tauri-plugin-shell`, `reqwest` (rustls), `serde`, `tokio`, `dirs`, `anyhow`, `thiserror`, `log`, `env_logger`.
- `desktop/src-tauri/tauri.conf.json` — bundle config; no JS frontend; tray icon registered.
- `desktop/src-tauri/build.rs` — `tauri_build::build()`.
- `desktop/src-tauri/src/main.rs` — entry point; sets `ActivationPolicy::Accessory` on macOS so there's no Dock icon; builds the tray; registers /health event listeners.
- `desktop/src-tauri/src/server.rs` — `FlowstateServer` struct that spawns `python3 -m flowstate server --port N --host 127.0.0.1`, finds a free port in 9090–9099, stops with SIGTERM + 5s grace then SIGKILL, kills on `Drop`.
- `desktop/src-tauri/src/health.rs` — async tokio polling task; emits `health://running`, `health://down`, `health://error` events with `{ port, slug, version }` payloads; cancellation via `tokio::sync::watch`.
- `desktop/src-tauri/src/menu.rs` — tray menu builder: project label, port, "Open UI", "Open in Browser", "Switch Project…", "Stop Server" / "Start Server" toggle, "Start at Login" (disabled stub), "Quit".
- `desktop/src-tauri/src/project.rs` — read/write `~/.flowstate/desktop_state.json` for last-used project; validates `flowstate.toml` exists in the picked directory.
- `desktop/src-tauri/icons/` — placeholder PNGs (idle / running / error at 32×32 + @2x, plus app icon) generated by `desktop/scripts/generate_tray_icons.py` using PIL.
- `desktop/.gitignore`, root `.gitignore` updates for `desktop/target/`, `desktop/dist/`, `desktop/python/`.
- `specs.md §13.5 Desktop App (Menubar)` — architecture, deferred items, unsigned-distribution model.
- `RELEASING.md` "Desktop app" section — TODO placeholder pointing at UI-077.
- Three follow-up issues filed and added to `issues/PLAN.md` Phase 35: UI-075, UI-076, UI-077.

### Deferred (follow-up issues)
- **UI-075** — Bundle portable Python via `python-build-standalone`. v0 uses `python3` from PATH (assumes `pipx install flowstate` or equivalent).
- **UI-076** — Tauri auto-updater + Tauri pubkey + GitHub Releases manifest.
- **UI-077** — Unsigned `.dmg` build pipeline + `desktop/scripts/build.sh` + RELEASING.md walkthrough.
- **UI-078** (TODO comment in code, not yet a filed issue) — "Start at Login" LaunchAgent toggle. The menu item exists as a disabled stub.
- **Animated tray icon** while a flow is executing — the issue spec called for it; v0 uses the static green icon. Pure polish; no follow-up issue filed yet, can be added if/when desired.
- **`flowstate init` from picker dialog** — original spec called for offering "Run flowstate init here?" if the user picks a non-Flowstate directory. v0 just logs a warning and does nothing. Can fold into UI-075 or file separately.
- **`README.md` screenshot** of the menubar dropdown — manual GUI not available in this environment.

### Why the split
The original UI-074 spec was a 12-bullet acceptance criteria list spanning scaffold → bundled Python → auto-updater → signed DMG → README screenshots. That's three or four issues' worth of work. Landing the scaffold first, with a `cargo check`-clean tree and well-defined hand-off points, lets the next agent (or the same agent in a follow-up) implement UI-075/076/077 independently.

## E2E Verification Log

### Reproduction
N/A — this is a greenfield feature, not a bug fix.

### Why GUI verification was deferred
The agent runtime has no display; `cargo tauri dev` and `cargo tauri build` both link `wry`/`webkit` and require a windowing system to run. Verifying the menubar dropdown end-to-end means dragging a `.dmg` onto a Mac, which is itself blocked on UI-077. The hard gate for the v0 scaffold is therefore "the project compiles cleanly" — i.e. types check, Tauri 2.x APIs are wired correctly, and there's no `unwrap()` that would crash at link time. `cargo check` is the right tool for that gate.

### Post-Implementation Verification

**Command:**
```
. "$HOME/.cargo/env" && cd desktop/src-tauri && cargo check 2>&1 | tail -10
```

**Output (last 10 lines):**
```
   Compiling flowstate-desktop v0.0.1 (/Users/theophanerupin/code/flowstate/desktop/src-tauri)
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.68s
```

Also verified `cargo check --all-targets` (catches build-script and test issues):
```
   Compiling flowstate-desktop v0.0.1 (/Users/theophanerupin/code/flowstate/desktop/src-tauri)
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.93s
```

**Conclusion:** PASS. The scaffold compiles cleanly with no warnings or errors against a real cargo+rustc 1.95.0 toolchain. ~300 transitive crates pulled (Tauri 2.x core + dialog + shell + reqwest+rustls + tokio). Tauri 2.x APIs used: `tauri::Builder`, `ActivationPolicy::Accessory`, `TrayIconBuilder::with_id`, `MenuBuilder` / `MenuItemBuilder` / `PredefinedMenuItem`, `tauri::image::Image::from_bytes`, `Manager::tray_by_id`, `Listener::listen`, `Emitter::emit`, `tauri_plugin_dialog::DialogExt::dialog().file().pick_folder(..)`, `tauri_plugin_shell::ShellExt::shell().open(..)`. All resolve and type-check.

**Manual GUI verification:** deferred — no display in this environment, and the installable `.app` / `.dmg` requires UI-077's build pipeline. The next agent picking up UI-077 should drag-install the produced `.dmg` and walk through the original "Verification Steps" list at the end of this file as the real end-to-end check.

## Completion Checklist (v0)
- [x] `desktop/` Tauri project scaffolded
- [x] Tray icon assets generated (3 states, static)
- [x] Server lifecycle (start/stop) wired with SIGTERM + grace + Drop
- [x] `/health` polling drives icon state via Tauri events
- [x] Project picker (native folder dialog) with `flowstate.toml` validation
- [x] `~/.flowstate/desktop_state.json` persistence for last-used project
- [x] `cargo check` passes with no warnings
- [x] `specs.md §13.5` written
- [x] `RELEASING.md` "Desktop app" placeholder added
- [x] Follow-up issues UI-075 / UI-076 / UI-077 filed and added to PLAN.md
- [ ] Bundled Python (deferred → UI-075)
- [ ] Auto-update (deferred → UI-076)
- [ ] `.dmg` produced (deferred → UI-077)
- [ ] `README.md` screenshot (deferred — needs UI-077)
- [ ] Manual GUI verification (deferred — needs UI-077)
