// On Windows, hide the spawned-from-cmd console. No-op on macOS.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Flowstate menubar app — entry point.
//!
//! v0 scope: scaffold + plumbing. The app:
//! 1. Sets the macOS activation policy to `Accessory` so there's no Dock
//!    icon — it lives only in the menubar.
//! 2. Builds a tray icon with a dropdown menu (project label, port,
//!    Open UI / Switch Project / Stop Server / Quit).
//! 3. If the persisted `desktop_state.json` has a `last_project_root`,
//!    auto-starts the flowstate server child process and the /health
//!    poller. Otherwise the user picks a project via the menu.
//!
//! Out of scope for v0: bundled Python (UI-075), auto-updater (UI-076),
//! signed/notarized DMG (UI-077), Start-at-Login (UI-078), animated tray
//! icon while a flow is executing.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use tauri::image::Image;
use tauri::menu::Menu;
use tauri::path::BaseDirectory;
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Listener, Manager, Wry};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;
use tokio::sync::watch;

mod health;
mod menu;
mod project;
mod server;

use crate::menu::{build_menu, CliInstallState, MenuState};
use crate::project::{load_state, save_state, validate_project_root, DesktopState};
use crate::server::FlowstateServer;

/// Stable tray-icon id. Used to look the tray up via `app.tray_by_id()`
/// when we need to update its icon or menu.
const TRAY_ID: &str = "main";

/// Embedded tray icon assets. We include them in the binary so the app is
/// self-contained — no resource resolution needed at runtime.
const ICON_IDLE: &[u8] = include_bytes!("../icons/tray-idle.png");
const ICON_RUNNING: &[u8] = include_bytes!("../icons/tray-running.png");
const ICON_ERROR: &[u8] = include_bytes!("../icons/tray-error.png");

/// Application state shared across the tray, server-supervisor, and
/// /health poller. Wrapped in `Mutex` because Tauri commands run on a
/// pool of threads.
pub struct AppState {
    pub server: Option<FlowstateServer>,
    pub menu_state: MenuState,
    pub desktop_state: DesktopState,
    /// Cancellation handle for the currently running /health poller.
    /// Setting this to `true` tells the poller to stop on its next tick.
    pub poller_cancel: Option<watch::Sender<bool>>,
}

impl AppState {
    fn new() -> Self {
        Self {
            server: None,
            menu_state: MenuState::default(),
            desktop_state: load_state(),
            poller_cancel: None,
        }
    }
}

fn main() {
    // Initialize a no-op logger so `log::warn!` calls don't panic. Tauri
    // 2.x has `tauri-plugin-log` but we don't need it for v0 — wiring
    // that in is a polish follow-up.
    let _ = env_logger::try_init_from_env(env_logger::Env::default().default_filter_or("info"));

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Mutex::new(AppState::new()))
        .setup(|app| {
            // macOS: hide the Dock icon. Menubar-only.
            #[cfg(target_os = "macos")]
            {
                app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            }

            // Build the initial (idle) menu.
            let menu_state = MenuState::default();
            let initial_menu = build_menu(app.handle(), &menu_state)?;

            // Build the tray icon and attach the menu + handlers.
            let app_handle = app.handle().clone();
            TrayIconBuilder::with_id(TRAY_ID)
                .icon(Image::from_bytes(ICON_IDLE)?)
                .icon_as_template(true)
                .menu(&initial_menu)
                .on_menu_event(move |app, event| {
                    on_menu_event(app, event.id().as_ref());
                })
                .build(app)?;

            // Listen for /health events emitted by the poller. We update
            // the tray icon and menu labels in response.
            register_health_listeners(app.handle().clone());

            // UI-081: reflect the current CLI-install state in the tray.
            // Cheap filesystem probe — runs synchronously here so the
            // initial menu doesn't briefly show "Install CLI" and then
            // flip if a shim is already in place.
            refresh_cli_install_state(&app_handle);

            // If we have a remembered project, auto-start the server.
            let last_root = {
                let state_mutex = app_handle.state::<Mutex<AppState>>();
                let guard = state_mutex.lock().expect("AppState poisoned");
                guard.desktop_state.last_project_root.clone()
            };
            if let Some(root) = last_root {
                if validate_project_root(&root).is_ok() {
                    if let Err(e) = start_server_for(&app_handle, root.clone()) {
                        log::warn!("auto-start failed: {e:#}");
                    }
                    // UI-080: evaluate the SDK-claude warning for the
                    // remembered project as soon as the tray is up.
                    refresh_sdk_claude_warning(&app_handle, Some(&root));
                } else {
                    log::warn!(
                        "remembered project root no longer valid; user will need to pick again"
                    );
                }
            }

            // UI-076: kick off a background updater check. Doesn't block
            // the tray from rendering — silently logs network failures so
            // the user isn't bothered by transient errors.
            let updater_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                check_for_update(updater_handle).await;
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // When the main UI window is closed/destroyed, drop the
            // activation policy back to `Accessory` so the Dock icon
            // disappears again. We promote to `Regular` only while the
            // window is on screen (see `open_ui_window`).
            if window.label() == UI_WINDOW_LABEL {
                if let tauri::WindowEvent::Destroyed = event {
                    #[cfg(target_os = "macos")]
                    let _ = window
                        .app_handle()
                        .set_activation_policy(tauri::ActivationPolicy::Accessory);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while running flowstate-desktop")
        .run(|_app_handle, event| {
            // Menubar-app lifecycle: closing the UI window (or all
            // windows) must NOT terminate the process — the tray icon
            // and the supervised flowstate server should keep running.
            // Tauri fires `ExitRequested` after the last window closes
            // on macOS; calling `api.prevent_exit()` keeps the run loop
            // alive. The only legitimate exit path is the tray's
            // `Quit Flowstate` menu item, which calls `app.exit(0)`
            // directly and bypasses this guard.
            if let tauri::RunEvent::ExitRequested { api, code, .. } = event {
                if code.is_none() {
                    api.prevent_exit();
                }
            }
        });
}

/// Dispatcher for tray menu events. The string IDs come from `menu.rs`.
fn on_menu_event(app: &AppHandle, id: &str) {
    match id {
        crate::menu::ID_QUIT => {
            // Stop the server cleanly before exiting.
            stop_server(app);
            app.exit(0);
        }
        crate::menu::ID_OPEN_UI => {
            open_ui_window(app);
        }
        crate::menu::ID_OPEN_BROWSER => {
            open_ui_in_browser(app);
        }
        crate::menu::ID_SWITCH_PROJECT => {
            pick_project(app.clone());
        }
        crate::menu::ID_TOGGLE_SERVER => {
            let running = {
                let state_mutex = app.state::<Mutex<AppState>>();
                let mut guard = state_mutex.lock().expect("AppState poisoned");
                guard.server.as_mut().is_some_and(|s| s.is_running())
            };
            if running {
                stop_server(app);
            } else {
                // Start using whatever project is remembered.
                let root = {
                    let state_mutex = app.state::<Mutex<AppState>>();
                    let guard = state_mutex.lock().expect("AppState poisoned");
                    guard.desktop_state.last_project_root.clone()
                };
                if let Some(root) = root {
                    if let Err(e) = start_server_for(app, root) {
                        log::warn!("start failed: {e:#}");
                    }
                } else {
                    pick_project(app.clone());
                }
            }
        }
        crate::menu::ID_START_AT_LOGIN => {
            log::info!("Start-at-Login toggle is a TODO (UI-078).");
        }
        crate::menu::ID_UPDATE_AVAILABLE => {
            // UI-076: trigger the download/install/restart flow.
            let app_for_update = app.clone();
            tauri::async_runtime::spawn(async move {
                install_update(app_for_update).await;
            });
        }
        crate::menu::ID_INSTALL_CLI => {
            // UI-081: write the CLI shim with admin-privileges osascript.
            let app_for_install = app.clone();
            tauri::async_runtime::spawn(async move {
                install_cli_shim(app_for_install).await;
            });
        }
        // Disabled labels (project name, port) emit events too — ignore.
        _ => {}
    }
}

/// Resolve the running server's UI URL, or `None` if no server is running.
fn current_ui_url(app: &AppHandle) -> Option<String> {
    let state_mutex = app.state::<Mutex<AppState>>();
    let guard = state_mutex.lock().expect("AppState poisoned");
    let port = guard.server.as_ref().map(|s| s.port()).unwrap_or(0);
    if port == 0 {
        None
    } else {
        Some(format!("http://127.0.0.1:{port}/"))
    }
}

/// Stable label for the singleton Tauri webview window that hosts the
/// React UI. Used to look the window up so subsequent "Open UI" clicks
/// focus the existing window instead of creating duplicates.
const UI_WINDOW_LABEL: &str = "main_ui";

/// Open the running server's UI inside a Tauri webview window. If the
/// window already exists, focus it. Falls back to the browser if window
/// creation fails (rare; e.g. malformed URL).
fn open_ui_window(app: &AppHandle) {
    let Some(url) = current_ui_url(app) else {
        log::warn!("Open UI requested but no server is running.");
        return;
    };

    // If the window already exists, just focus it (don't reload).
    if let Some(window) = app.get_webview_window(UI_WINDOW_LABEL) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }

    let parsed_url: tauri::Url = match url.parse() {
        Ok(u) => u,
        Err(e) => {
            log::warn!("invalid UI URL {url}: {e}");
            return;
        }
    };

    // macOS Accessory policy: an app with no Dock icon can still host
    // webview windows, but bringing the window to the foreground is
    // unreliable from a tray-menu callback. Promote to Regular while the
    // window is being created so it actually surfaces; we leave the
    // policy as Regular while a window exists (drops back to Accessory
    // on the on_window_event Destroyed handler in main()).
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);

    let result = tauri::WebviewWindowBuilder::new(
        app,
        UI_WINDOW_LABEL,
        tauri::WebviewUrl::External(parsed_url),
    )
    .title("Flowstate")
    .inner_size(1280.0, 860.0)
    .min_inner_size(800.0, 600.0)
    .focused(true)
    .build();

    if let Err(e) = result {
        log::warn!("failed to open UI window: {e}; falling back to browser.");
        // Restore Accessory immediately on failure.
        #[cfg(target_os = "macos")]
        let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
        open_ui_in_browser(app);
    }
}

/// Resolve the Python interpreter the supervised flowstate server should
/// run under. Three-tier fallback:
///
/// 1. `FLOWSTATE_PYTHON` env var — the dev override. Useful for pointing
///    at a project venv during `cargo tauri dev` without bundling.
/// 2. The bundled portable Python shipped inside the `.app` at
///    `Contents/Resources/python/bin/python3` (UI-075). Tauri's path
///    resolver returns the absolute path inside the app bundle. We
///    require the file to actually exist on disk before accepting it,
///    so `cargo tauri dev` runs without a vendored tree fall through.
/// 3. `python3` from `PATH` — last resort. Assumes the developer ran
///    `pipx install flowstate` or has the source venv on PATH.
fn resolve_python(app: &AppHandle) -> OsString {
    if let Some(p) = std::env::var_os("FLOWSTATE_PYTHON") {
        log::info!("python: using FLOWSTATE_PYTHON override");
        return p;
    }
    if let Ok(bundled) = app
        .path()
        .resolve("python/bin/python3", BaseDirectory::Resource)
    {
        if bundled.is_file() {
            log::info!("python: using bundled {}", bundled.display());
            return bundled.into_os_string();
        }
    }
    log::info!("python: falling back to system python3 on PATH");
    OsString::from("python3")
}

/// Open the running server's UI in the user's default browser.
fn open_ui_in_browser(app: &AppHandle) {
    let Some(url) = current_ui_url(app) else {
        log::warn!("Open in Browser requested but no server is running.");
        return;
    };
    // `open` is being moved to `tauri-plugin-opener` upstream, but
    // `tauri-plugin-shell` still ships it for v2 and it's the simplest
    // dep to keep for v0. The #[allow] silences the deprecation warning
    // until we migrate (tracked in UI-077 follow-up).
    #[allow(deprecated)]
    if let Err(e) = app.shell().open(&url, None) {
        log::warn!("failed to open {url}: {e}");
    }
}

/// Show a native directory picker; if the user picks a directory that
/// contains `flowstate.toml`, switch to it.
fn pick_project(app: AppHandle) {
    // macOS menubar-only quirk: with `ActivationPolicy::Accessory` set,
    // the NSApp has no main window and no Dock presence, so native
    // NSOpenPanel dialogs can't anchor to a proper coordinate frame — the
    // panel renders at the wrong Y and clicks register at an offset from
    // their visual position. Workaround: temporarily promote to `Regular`
    // (briefly shows a Dock icon) so the dialog has a real activation
    // context, then drop back to `Accessory` once the user dismisses it.
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);

    // UI-082: defer the `pick_folder` call by one async tick + a short
    // sleep so NSApp's main run-loop can fully absorb the activation
    // policy switch above before NSOpenPanel performs its first chrome
    // layout. Without this, the panel's title bar is drawn while the app
    // is still mid-transition between Accessory and Regular — the chrome
    // gets re-decorated on the next frame, leaving stale glyphs from the
    // initial (Accessory) style overlaid on the title text and traffic-
    // light buttons. 50 ms is invisibly fast to the user but plenty for
    // AppKit to settle. The existing click-registration workaround
    // (Regular ↔ Accessory dance) is preserved exactly — only the timing
    // of when the dialog is opened changes.
    let app_for_dialog = app.clone();
    tauri::async_runtime::spawn(async move {
        #[cfg(target_os = "macos")]
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;

        let app_for_callback = app_for_dialog.clone();
        app_for_dialog
            .dialog()
            .file()
            .set_title("Select a Flowstate project directory")
            .pick_folder(move |maybe_path| {
                // Always restore the menubar-only policy, even on cancel/error.
                #[cfg(target_os = "macos")]
                let _ = app_for_callback
                    .set_activation_policy(tauri::ActivationPolicy::Accessory);

                let Some(file_path) = maybe_path else {
                    return; // user cancelled
                };
                // tauri-plugin-dialog returns a `FilePath` enum. Convert it
                // to a real PathBuf via `into_path()`.
                let path: PathBuf = match file_path.into_path() {
                    Ok(p) => p,
                    Err(e) => {
                        log::warn!("dialog returned a non-filesystem path: {e}");
                        return;
                    }
                };
                if let Err(e) = validate_project_root(&path) {
                    log::warn!("invalid project root: {e:#}");
                    // TODO(UI-074): show a native error dialog and offer to
                    // run `flowstate init` here. Stub for v0 — user just sees
                    // a log line.
                    return;
                }
                // Stop any existing server, then start a fresh one rooted at
                // the new path.
                stop_server(&app_for_callback);
                if let Err(e) = start_server_for(&app_for_callback, path.clone()) {
                    log::warn!("start failed after switch: {e:#}");
                }
                // UI-080: re-evaluate the SDK-claude warning for the new project.
                refresh_sdk_claude_warning(&app_for_callback, Some(&path));
            });
    });
}

/// UI-081: filesystem location for the CLI shim. /usr/local/bin is the
/// most-shells-have-it-on-PATH choice on macOS. Fresh Apple Silicon
/// machines without Homebrew may not have the directory; we let the
/// `install` invocation fail in that case and surface a dialog rather
/// than `mkdir -p`-ing an uncreated system directory ourselves.
const CLI_SHIM_PATH: &str = "/usr/local/bin/flowstate";

/// UI-081: shell-escape a string for safe interpolation into the
/// AppleScript `do shell script` arg. The double quoting (outer
/// AppleScript string + inner shell single quotes) handles paths with
/// spaces; the strip-single-quote step prevents an attacker-controlled
/// path from breaking out of the quote — though in practice the only
/// inputs here are paths resolved by Tauri or `std::env::temp_dir()`,
/// not user input.
fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// UI-081: resolve the bundled Python interpreter inside the running
/// `.app` (or the dev tree). Returns `None` when no bundled python
/// exists — typically a `cargo tauri dev` run without prior vendor.
fn bundled_python_path(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .resolve("python/bin/python3", BaseDirectory::Resource)
        .ok()
        .filter(|p| p.is_file())
}

/// UI-081: inspect /usr/local/bin/flowstate to decide what the tray menu
/// row should say. The shim is expected to be a plain file (not a
/// symlink, since the bundled `flowstate` entry-point has a hardcoded
/// shebang and can't be linked safely — see issue file for rationale).
fn current_cli_install_state(app: &AppHandle) -> CliInstallState {
    let shim_path = Path::new(CLI_SHIM_PATH);
    if !shim_path.is_file() {
        return CliInstallState::NotInstalled;
    }
    let Some(target_python) = bundled_python_path(app) else {
        // We don't know our own bundled python path (dev mode without
        // vendor). Treat any existing shim as "other" so we don't claim
        // ownership of something we can't verify.
        return CliInstallState::InstalledForOtherApp;
    };
    // The shim contains an `exec "<python>" -m flowstate "$@"` line.
    // Substring match — robust to formatting tweaks and our own
    // shell-quote helper's escaping. If we ever generate a fundamentally
    // different shim shape we'll need to bump a version marker.
    match std::fs::read_to_string(shim_path) {
        Ok(body) if body.contains(target_python.to_string_lossy().as_ref()) => {
            CliInstallState::InstalledForThisApp
        }
        Ok(_) => CliInstallState::InstalledForOtherApp,
        Err(_) => CliInstallState::InstalledForOtherApp,
    }
}

/// UI-081: refresh the tray's CLI-install state.
fn refresh_cli_install_state(app: &AppHandle) {
    let state = current_cli_install_state(app);
    update_menu(app, |m| m.cli_install_state = state);
}

/// UI-081: write the CLI shim to /usr/local/bin/flowstate via an
/// admin-privileges AppleScript prompt. The shim is a 2-line bash
/// wrapper that execs `<bundled-python> -m flowstate` with the user's
/// args — keeps the CLI version in lockstep with the `.app` because
/// it always points at this `.app`'s embedded interpreter.
async fn install_cli_shim(app: AppHandle) {
    let Some(python_path) = bundled_python_path(&app) else {
        log::warn!("install_cli: bundled python not found");
        return;
    };

    let shim_body = format!(
        "#!/bin/bash\n# Flowstate CLI shim (UI-081). Regenerated by the menubar app.\nexec {} -m flowstate \"$@\"\n",
        shell_quote(&python_path.to_string_lossy()),
    );

    // Write to a temp file first; AppleScript-with-admin moves it into
    // place atomically via `install -m 0755`. macOS clears /tmp on
    // reboot, so the tempfile is short-lived even if we crash.
    let tmp_path = std::env::temp_dir().join("flowstate-cli-shim");
    if let Err(e) = std::fs::write(&tmp_path, &shim_body) {
        log::warn!("install_cli: failed to write tempfile: {e}");
        return;
    }

    // Same Accessory→Regular toggle pattern as pick_project — the macOS
    // auth prompt needs a real activation context to surface reliably.
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);

    let script = format!(
        r#"do shell script "install -m 0755 {tmp} {dest}" with administrator privileges"#,
        tmp = shell_quote(&tmp_path.to_string_lossy()),
        dest = shell_quote(CLI_SHIM_PATH),
    );

    let app_for_restore = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        std::process::Command::new("osascript")
            .arg("-e")
            .arg(&script)
            .output()
    })
    .await;

    #[cfg(target_os = "macos")]
    let _ = app_for_restore.set_activation_policy(tauri::ActivationPolicy::Accessory);

    let _ = std::fs::remove_file(&tmp_path);

    match result {
        Ok(Ok(out)) if out.status.success() => {
            log::info!("install_cli: shim installed at {CLI_SHIM_PATH}");
        }
        Ok(Ok(out)) => {
            // Most common failure: user clicked "Cancel" in the auth dialog.
            let stderr = String::from_utf8_lossy(&out.stderr);
            if stderr.contains("User canceled") {
                log::info!("install_cli: user cancelled auth prompt");
            } else {
                log::warn!("install_cli: osascript exited {} — stderr={}", out.status, stderr);
            }
        }
        Ok(Err(e)) => log::warn!("install_cli: failed to spawn osascript: {e}"),
        Err(e) => log::warn!("install_cli: blocking task failed: {e}"),
    }

    refresh_cli_install_state(&app);
}

/// UI-076: probe the GitHub Releases updater manifest in the background.
///
/// Runs once at launch from a tokio task spawned in `setup`. On success,
/// stores the new version in `MenuState.update_available` and rebuilds
/// the tray menu so the user sees an "Update to X.Y.Z" action row.
/// On error (network down, no manifest yet, signature mismatch) we log
/// at warn-level and move on — the next launch will retry. Never bother
/// the user with a transient error dialog.
async fn check_for_update(app: AppHandle) {
    let updater = match app.updater() {
        Ok(u) => u,
        Err(e) => {
            log::warn!("updater unavailable: {e}");
            return;
        }
    };
    let result = updater.check().await;
    let update = match result {
        Ok(Some(update)) => update,
        Ok(None) => {
            log::info!("updater: no update available");
            return;
        }
        Err(e) => {
            log::warn!("updater check failed: {e}");
            return;
        }
    };
    let version = update.version.clone();
    log::info!("updater: {version} available");
    update_menu(&app, |m| m.update_available = Some(version));
}

/// UI-076: download + install the pending update, then restart the app.
///
/// Tauri's `download_and_install` handles signature verification against
/// the embedded `pubkey` from `tauri.conf.json` — if the manifest is
/// signed with anything other than the maintainer's private key the
/// install is rejected and the existing app keeps running. That's the
/// security boundary; never bypass it.
async fn install_update(app: AppHandle) {
    let updater = match app.updater() {
        Ok(u) => u,
        Err(e) => {
            log::warn!("updater unavailable at install time: {e}");
            return;
        }
    };
    let update = match updater.check().await {
        Ok(Some(u)) => u,
        Ok(None) => {
            log::info!("updater: no update at install time (raced?)");
            return;
        }
        Err(e) => {
            log::warn!("updater re-check before install failed: {e}");
            return;
        }
    };
    log::info!("updater: downloading {}", update.version);
    let result = update
        .download_and_install(|_chunk, _total| {}, || log::info!("updater: download finished"))
        .await;
    match result {
        Ok(()) => {
            log::info!("updater: install complete, restarting");
            // Stop the spawned flowstate server cleanly before restart so
            // the next launch isn't fighting an orphaned port binding.
            stop_server(&app);
            app.restart();
        }
        Err(e) => log::warn!("updater install failed: {e}"),
    }
}

/// UI-080: scan a project's `flows/*.flow` files for `harness = "sdk"`
/// declarations. The check is intentionally a substring match — fast,
/// good enough for "should we surface a missing-claude warning". A
/// false positive here is harmless (extra warning row); a false negative
/// is also harmless (the user just hits a clear `ProcessError` later).
fn project_uses_sdk_harness(project_root: &Path) -> bool {
    let flows_dir = project_root.join("flows");
    let Ok(entries) = std::fs::read_dir(&flows_dir) else {
        return false;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("flow") {
            continue;
        }
        let Ok(contents) = std::fs::read_to_string(&path) else {
            continue;
        };
        // Tolerate whitespace variants: `harness="sdk"`, `harness = "sdk"`, etc.
        for line in contents.lines() {
            let stripped = line.split_whitespace().collect::<String>();
            if stripped.contains("harness=\"sdk\"") {
                return true;
            }
        }
    }
    false
}

/// UI-080: probe `PATH` for a `claude` binary. Used to drive the tray
/// warning when a project uses `harness="sdk"` flows.
fn claude_on_path() -> bool {
    let Some(path) = std::env::var_os("PATH") else {
        return false;
    };
    std::env::split_paths(&path).any(|dir| {
        let candidate = dir.join("claude");
        // Either `claude` directly or e.g. `claude.exe` on Windows. The
        // file_type call avoids following dangling symlinks.
        candidate
            .symlink_metadata()
            .map(|m| m.file_type().is_file() || m.file_type().is_symlink())
            .unwrap_or(false)
    })
}

/// UI-080: refresh the tray's `sdk_claude_missing` flag based on the
/// current project (passed in or read from `desktop_state`) and the
/// presence of `claude` on PATH. No-op when no project is selected —
/// can't determine if SDK harness is in play without one.
fn refresh_sdk_claude_warning(app: &AppHandle, project_root: Option<&Path>) {
    let owned_root: Option<PathBuf> = project_root.map(Path::to_path_buf).or_else(|| {
        let state_mutex = app.state::<Mutex<AppState>>();
        let guard = state_mutex.lock().expect("AppState poisoned");
        guard.desktop_state.last_project_root.clone()
    });
    let warn = match owned_root.as_deref() {
        Some(root) => project_uses_sdk_harness(root) && !claude_on_path(),
        None => false,
    };
    update_menu(app, |m| m.sdk_claude_missing = warn);
}

/// Spin up a flowstate server child process for `project_root`, then
/// start the /health poller against the port it picked.
fn start_server_for(app: &AppHandle, project_root: PathBuf) -> anyhow::Result<()> {
    let port = {
        let state_mutex = app.state::<Mutex<AppState>>();
        let mut guard = state_mutex.lock().expect("AppState poisoned");

        // Cancel any existing poller before we replace the server.
        if let Some(tx) = guard.poller_cancel.take() {
            let _ = tx.send(true);
        }

        let python = resolve_python(app);
        let mut srv = FlowstateServer::new(project_root.clone(), python);
        let port = srv.start()?;
        guard.server = Some(srv);
        guard.desktop_state.last_project_root = Some(project_root.clone());
        save_state(&guard.desktop_state);
        port
    };

    // Spawn a fresh /health poller.
    let (tx, rx) = watch::channel(false);
    {
        let state_mutex = app.state::<Mutex<AppState>>();
        let mut guard = state_mutex.lock().expect("AppState poisoned");
        guard.poller_cancel = Some(tx);
    }
    health::spawn_poller(app.clone(), port, rx);

    // Reflect the new state in the tray immediately, even before the
    // first poll comes in. The poller will refine the project label
    // once /health responds.
    update_menu(
        app,
        |m| {
            m.port_label = format!("Port: {port}");
            m.project_label = format!(
                "Project: {}",
                project_root
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_else(|| project_root.display().to_string())
            );
            m.server_running = true;
        },
    );
    Ok(())
}

/// Stop the running server (if any) and clear the poller. Safe to call
/// when nothing is running.
fn stop_server(app: &AppHandle) {
    {
        let state_mutex = app.state::<Mutex<AppState>>();
        let mut guard = state_mutex.lock().expect("AppState poisoned");
        if let Some(tx) = guard.poller_cancel.take() {
            let _ = tx.send(true);
        }
        if let Some(mut srv) = guard.server.take() {
            if let Err(e) = srv.stop() {
                log::warn!("server stop returned error: {e:#}");
            }
        }
    }
    update_menu(app, |m| {
        m.port_label = "Server: stopped".to_string();
        m.server_running = false;
    });
    set_tray_icon(app, ICON_IDLE);
}

/// Subscribe to /health events and update the tray icon + menu labels.
fn register_health_listeners(app: AppHandle) {
    let app_running = app.clone();
    app.listen(health::EVENT_RUNNING, move |event| {
        let payload = event.payload();
        if let Ok(info) = serde_json::from_str::<health::HealthInfo>(payload) {
            let app = app_running.clone();
            update_menu(&app, |m| {
                m.server_running = true;
                if let Some(slug) = info.slug {
                    m.project_label = format!("Project: {slug}");
                }
                m.port_label = format!("Port: {}", info.port);
            });
            set_tray_icon(&app_running, ICON_RUNNING);
        }
    });

    let app_down = app.clone();
    app.listen(health::EVENT_DOWN, move |_event| {
        let app = app_down.clone();
        update_menu(&app, |m| {
            m.server_running = false;
            m.port_label = "Server: down".to_string();
        });
        set_tray_icon(&app_down, ICON_ERROR);
    });

    let app_err = app.clone();
    app.listen(health::EVENT_ERROR, move |_event| {
        let app = app_err.clone();
        update_menu(&app, |m| {
            m.server_running = false;
            m.port_label = "Server: error".to_string();
        });
        set_tray_icon(&app_err, ICON_ERROR);
    });
}

/// Apply a mutation to the menu state and rebuild the tray menu.
fn update_menu(app: &AppHandle, mutate: impl FnOnce(&mut MenuState)) {
    let new_menu: tauri::Result<Menu<Wry>> = {
        let state_mutex = app.state::<Mutex<AppState>>();
        let mut guard = state_mutex.lock().expect("AppState poisoned");
        mutate(&mut guard.menu_state);
        build_menu(app, &guard.menu_state)
    };
    match new_menu {
        Ok(menu) => {
            if let Some(tray) = app.tray_by_id(TRAY_ID) {
                if let Err(e) = tray.set_menu(Some(menu)) {
                    log::warn!("failed to update tray menu: {e}");
                }
            }
        }
        Err(e) => log::warn!("failed to build tray menu: {e}"),
    }
}

/// Swap the tray icon. PNG bytes are embedded in the binary.
fn set_tray_icon(app: &AppHandle, bytes: &'static [u8]) {
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    match Image::from_bytes(bytes) {
        Ok(img) => {
            if let Err(e) = tray.set_icon(Some(img)) {
                log::warn!("failed to set tray icon: {e}");
            }
        }
        Err(e) => log::warn!("failed to decode tray icon: {e}"),
    }
}
