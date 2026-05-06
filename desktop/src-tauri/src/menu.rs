//! Tray menu construction.
//!
//! The menubar app's entire UI is the tray + its dropdown menu. There are
//! no Tauri windows in v0 — "Open UI" delegates to the user's default
//! browser via `tauri-plugin-shell`'s opener.
//!
//! Menu item IDs are stable strings, used as the dispatch key in the
//! `on_menu_event` handler in `main.rs`.

use tauri::menu::{Menu, MenuBuilder, MenuItemBuilder, PredefinedMenuItem};
use tauri::{AppHandle, Wry};

// Stable IDs. Keep these in sync with the dispatcher in `main.rs`.
pub const ID_PROJECT_LABEL: &str = "project_label";
pub const ID_PORT_LABEL: &str = "port_label";
pub const ID_OPEN_UI: &str = "open_ui";
pub const ID_OPEN_BROWSER: &str = "open_browser";
pub const ID_SWITCH_PROJECT: &str = "switch_project";
pub const ID_TOGGLE_SERVER: &str = "toggle_server";
pub const ID_START_AT_LOGIN: &str = "start_at_login";
pub const ID_QUIT: &str = "quit";

/// State the menu builder needs to render the right labels.
pub struct MenuState {
    pub project_label: String,
    pub port_label: String,
    pub server_running: bool,
}

impl Default for MenuState {
    fn default() -> Self {
        Self {
            project_label: "No project selected".to_string(),
            port_label: "Server: stopped".to_string(),
            server_running: false,
        }
    }
}

/// Build (or rebuild) the tray menu for the current state.
///
/// Tauri 2.x doesn't support in-place menu mutation cleanly — the
/// idiomatic pattern is to construct a fresh `Menu` and call
/// `tray.set_menu(Some(menu))` whenever state changes. That's what we do.
pub fn build_menu(app: &AppHandle, state: &MenuState) -> tauri::Result<Menu<Wry>> {
    let project = MenuItemBuilder::with_id(ID_PROJECT_LABEL, &state.project_label)
        .enabled(false)
        .build(app)?;
    let port = MenuItemBuilder::with_id(ID_PORT_LABEL, &state.port_label)
        .enabled(false)
        .build(app)?;

    let open_ui = MenuItemBuilder::with_id(ID_OPEN_UI, "Open UI")
        .enabled(state.server_running)
        .build(app)?;
    let open_browser = MenuItemBuilder::with_id(ID_OPEN_BROWSER, "Open in Browser")
        .enabled(state.server_running)
        .build(app)?;

    let switch_project =
        MenuItemBuilder::with_id(ID_SWITCH_PROJECT, "Switch Project\u{2026}").build(app)?;

    let toggle_label = if state.server_running {
        "Stop Server"
    } else {
        "Start Server"
    };
    let toggle_server = MenuItemBuilder::with_id(ID_TOGGLE_SERVER, toggle_label).build(app)?;

    // v0: this is a stub. Toggling does nothing yet (UI-078 follow-up).
    let start_at_login = MenuItemBuilder::with_id(ID_START_AT_LOGIN, "Start at Login (TODO)")
        .enabled(false)
        .build(app)?;

    let quit = MenuItemBuilder::with_id(ID_QUIT, "Quit Flowstate").build(app)?;

    let menu = MenuBuilder::new(app)
        .item(&project)
        .item(&port)
        .separator()
        .item(&open_ui)
        .item(&open_browser)
        .separator()
        .item(&switch_project)
        .item(&toggle_server)
        .separator()
        .item(&start_at_login)
        .item(&PredefinedMenuItem::separator(app)?)
        .item(&quit)
        .build()?;

    Ok(menu)
}
