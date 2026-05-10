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
use std::path::PathBuf;
use std::sync::Mutex;

use tauri::image::Image;
use tauri::menu::Menu;
use tauri::path::BaseDirectory;
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Listener, Manager, Wry};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::ShellExt;
use tokio::sync::watch;

mod health;
mod menu;
mod project;
mod server;

use crate::menu::{build_menu, MenuState};
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

            // If we have a remembered project, auto-start the server.
            let last_root = {
                let state_mutex = app_handle.state::<Mutex<AppState>>();
                let guard = state_mutex.lock().expect("AppState poisoned");
                guard.desktop_state.last_project_root.clone()
            };
            if let Some(root) = last_root {
                if validate_project_root(&root).is_ok() {
                    if let Err(e) = start_server_for(&app_handle, root) {
                        log::warn!("auto-start failed: {e:#}");
                    }
                } else {
                    log::warn!(
                        "remembered project root no longer valid; user will need to pick again"
                    );
                }
            }

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

    let app_for_callback = app.clone();
    app.dialog()
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
            // tauri-plugin-dialog returns a `FilePath` enum. Convert it to
            // a real PathBuf via `into_path()`.
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
            if let Err(e) = start_server_for(&app_for_callback, path) {
                log::warn!("start failed after switch: {e:#}");
            }
        });
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
