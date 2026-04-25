# [UI-074] Tauri menubar app for Flowstate

## Domain
ui (with light shared/server touch)

## Status
todo

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

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `desktop/` Tauri project scaffolded
- [ ] Menubar icon with 3 states implemented
- [ ] Server lifecycle (start/stop/restart) wired
- [ ] `/health` polling drives icon state
- [ ] Project picker + `flowstate init` flow
- [ ] Bundled Python via `python-build-standalone`
- [ ] Auto-start on login (opt-in)
- [ ] Auto-update via Tauri updater (with Tauri's own pubkey; no Apple signing)
- [ ] Unsigned macOS `.dmg` produced; first-launch Gatekeeper workaround documented
- [ ] `RELEASING.md` desktop section (no sign/notarize step)
- [ ] `README.md` screenshot + link
- [ ] `specs.md §13.5` written
- [ ] Manual E2E verified end-to-end on a clean macOS install
