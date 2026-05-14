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
pub const ID_CLAUDE_MISSING: &str = "claude_missing";
pub const ID_UPDATE_AVAILABLE: &str = "update_available";
pub const ID_INSTALL_CLI: &str = "install_cli";
pub const ID_QUIT: &str = "quit";

/// Where the bundled `flowstate` CLI shim is (or isn't) installed on
/// PATH. Drives the tray menu's "Install CLI" row.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum CliInstallState {
    /// `/usr/local/bin/flowstate` doesn't exist.
    NotInstalled,
    /// A shim is present and points at the running app's bundled python.
    InstalledForThisApp,
    /// A shim is present but points at a different python (older `.app`
    /// install, hand-written wrapper, etc.). Offer to replace.
    InstalledForOtherApp,
}

/// State the menu builder needs to render the right labels.
#[derive(Clone)]
pub struct MenuState {
    pub project_label: String,
    pub port_label: String,
    pub server_running: bool,
    /// When `true`, the active project has at least one flow declaring
    /// `harness = "sdk"` *and* no `claude` binary was found on PATH.
    /// Surfaces a warning row above the project label so the user sees
    /// the missing-dependency state before triggering a flow that needs
    /// it. UI-080.
    pub sdk_claude_missing: bool,
    /// `Some(version)` when `tauri-plugin-updater` reported a newer
    /// release on the GitHub Releases manifest. Surfaces an `Update to
    /// X.Y.Z — restart to install` action row that triggers
    /// `download_and_install`. `None` means we're up to date or the
    /// check hasn't run yet. UI-076.
    pub update_available: Option<String>,
    /// Whether the bundled CLI shim is installed at /usr/local/bin/flowstate.
    /// Drives the `Install / Update CLI in PATH` row. UI-081.
    pub cli_install_state: CliInstallState,
}

impl Default for MenuState {
    fn default() -> Self {
        Self {
            project_label: "No project selected".to_string(),
            port_label: "Server: stopped".to_string(),
            server_running: false,
            sdk_claude_missing: false,
            update_available: None,
            cli_install_state: CliInstallState::NotInstalled,
        }
    }
}

/// Build (or rebuild) the tray menu for the current state.
///
/// Tauri 2.x doesn't support in-place menu mutation cleanly — the
/// idiomatic pattern is to construct a fresh `Menu` and call
/// `tray.set_menu(Some(menu))` whenever state changes. That's what we do.
pub fn build_menu(app: &AppHandle, state: &MenuState) -> tauri::Result<Menu<Wry>> {
    // UI-080: warning row for SDK-harness flows when `claude` isn't on PATH.
    // Disabled (clickable=false) so it reads as an indicator, not an action.
    let claude_warning = if state.sdk_claude_missing {
        Some(
            MenuItemBuilder::with_id(
                ID_CLAUDE_MISSING,
                "\u{26A0} `claude` not on PATH (SDK-harness flow needs it)",
            )
            .enabled(false)
            .build(app)?,
        )
    } else {
        None
    };

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

    // UI-081: "Install CLI to /usr/local/bin" — label reflects current state.
    // Always shown (even when correctly installed) so users can
    // reinstall after an app update. Dropping backticks from the label
    // since some macOS menu renderers swallow text containing them.
    let install_cli_label = match state.cli_install_state {
        CliInstallState::NotInstalled => "Install flowstate CLI to /usr/local/bin",
        CliInstallState::InstalledForOtherApp => "Update CLI shim in /usr/local/bin",
        CliInstallState::InstalledForThisApp => "Reinstall CLI shim in /usr/local/bin",
    };
    let install_cli = MenuItemBuilder::with_id(ID_INSTALL_CLI, install_cli_label).build(app)?;

    // v0: this is a stub. Toggling does nothing yet (UI-078 follow-up).
    let start_at_login = MenuItemBuilder::with_id(ID_START_AT_LOGIN, "Start at Login (TODO)")
        .enabled(false)
        .build(app)?;

    let quit = MenuItemBuilder::with_id(ID_QUIT, "Quit Flowstate").build(app)?;

    // UI-076: "Update to X.Y.Z — restart to install" surfaced when the
    // updater plugin reports a newer release on GitHub. Enabled (clickable)
    // since clicking triggers the download + install + restart.
    let update_item = state.update_available.as_ref().map(|version| {
        MenuItemBuilder::with_id(
            ID_UPDATE_AVAILABLE,
            format!("Update to {version} — restart to install"),
        )
        .build(app)
    });
    let update_item = match update_item {
        Some(Ok(item)) => Some(item),
        Some(Err(e)) => {
            log::warn!("failed to build update menu item: {e}");
            None
        }
        None => None,
    };

    let mut builder = MenuBuilder::new(app);
    if let Some(warning) = claude_warning.as_ref() {
        builder = builder.item(warning).separator();
    }
    if let Some(update) = update_item.as_ref() {
        builder = builder.item(update).separator();
    }
    let menu = builder
        .item(&project)
        .item(&port)
        .separator()
        .item(&open_ui)
        .item(&open_browser)
        .separator()
        .item(&switch_project)
        .item(&toggle_server)
        .separator()
        .item(&install_cli)
        .item(&start_at_login)
        .item(&PredefinedMenuItem::separator(app)?)
        .item(&quit)
        .build()?;

    Ok(menu)
}
