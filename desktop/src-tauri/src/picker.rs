//! UI-082: HTML folder picker hosted in a Tauri webview window.
//!
//! NSOpenPanel — whether invoked from our menubar NSApp, from osascript
//! via System Events, or from `tell application "Finder"` — mispositions
//! its title bar at y=0 of the main display on notched MacBook Pros.
//! Menubar items composite over the dialog title. No combination of
//! activation-policy toggles, defer-then-open sleeps, or host-process
//! delegation fixes the geometry calculation upstream.
//!
//! This module replaces the picker with a custom WebviewWindow that
//! renders an HTML tree view. We control the window position, so the
//! notch / menubar artifact disappears.
//!
//! Communication:
//! - JS in the picker calls `invoke('picker_list_directory', { path })`
//!   to populate the tree.
//! - JS calls `invoke('picker_submit', { path })` with the chosen path,
//!   or `invoke('picker_submit', { path: null })` to cancel.
//! - The Rust side emits `picker:result` on the AppHandle so the
//!   caller (main::pick_project) can react.

use std::fs;
use std::path::{Path, PathBuf};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

/// Stable window label so we can focus an existing picker instead of
/// spawning duplicates.
const PICKER_WINDOW_LABEL: &str = "folder_picker";

/// Tauri event the picker emits when the user submits or cancels.
/// Payload is `Option<String>` — `None` means cancel.
pub const PICKER_RESULT_EVENT: &str = "picker:result";

/// One row in the picker tree. Only directories are listed — files
/// would clutter the view since users are picking a project root.
#[derive(Serialize)]
pub struct DirEntry {
    pub name: String,
    pub path: String,
    /// `true` when the directory contains a `flowstate.toml` — the
    /// picker UI highlights these so users can spot valid roots fast.
    pub has_flowstate_toml: bool,
}

/// Tauri command: list immediate subdirectories of `path`.
///
/// Hidden entries (leading `.`) are skipped. Items the user can't
/// stat (permission errors, broken symlinks) are silently omitted —
/// the picker is a UI surface, not a debugging tool, and noisy errors
/// would obscure the legitimate hits.
#[tauri::command]
pub fn picker_list_directory(path: String) -> Result<PickerListing, String> {
    // Tolerate `~` as a shortcut from the picker UI — saves a separate
    // "get home path" round-trip on the home button.
    let expanded = if path == "~" || path.starts_with("~/") {
        let home = std::env::var("HOME").map_err(|_| "$HOME unset".to_string())?;
        if path == "~" {
            home
        } else {
            format!("{home}/{}", &path[2..])
        }
    } else {
        path
    };
    let dir = PathBuf::from(&expanded);
    let read = fs::read_dir(&dir).map_err(|e| format!("read_dir({}): {e}", dir.display()))?;
    let mut entries: Vec<DirEntry> = read
        .filter_map(|res| res.ok())
        .filter(|entry| {
            entry
                .file_name()
                .to_str()
                .is_some_and(|name| !name.starts_with('.'))
        })
        .filter(|entry| entry.file_type().ok().is_some_and(|ft| ft.is_dir()))
        .map(|entry| {
            let entry_path = entry.path();
            let has_toml = entry_path.join("flowstate.toml").is_file();
            DirEntry {
                name: entry.file_name().to_string_lossy().into_owned(),
                path: entry_path.to_string_lossy().into_owned(),
                has_flowstate_toml: has_toml,
            }
        })
        .collect();
    entries.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));

    let parent = dir.parent().map(|p| p.to_string_lossy().into_owned());

    Ok(PickerListing {
        path: dir.to_string_lossy().into_owned(),
        parent,
        entries,
    })
}

#[derive(Serialize)]
pub struct PickerListing {
    pub path: String,
    pub parent: Option<String>,
    pub entries: Vec<DirEntry>,
}

/// Tauri command: the picker UI calls this with the chosen path
/// (or `None` to cancel). We emit a Tauri event so the caller can
/// react, then close the window.
#[tauri::command]
pub fn picker_submit(app: AppHandle, path: Option<String>) -> Result<(), String> {
    app.emit(PICKER_RESULT_EVENT, path.clone())
        .map_err(|e| format!("emit({PICKER_RESULT_EVENT}): {e}"))?;
    if let Some(window) = app.get_webview_window(PICKER_WINDOW_LABEL) {
        let _ = window.close();
    }
    Ok(())
}

/// Open (or focus) the picker window.
///
/// We write the HTML to a tempfile and point a `file://` URL at it.
/// Embedding via `data:` URL would also work but the relative size
/// (~9 KB) makes tempfile-based delivery cleaner — JS in a `data:` URL
/// can't reload itself for re-renders, and Tauri's CSP defaults are
/// friendlier to `file://` than to inline payloads.
pub fn open_picker(app: &AppHandle, initial_path: &Path) -> Result<(), String> {
    if let Some(existing) = app.get_webview_window(PICKER_WINDOW_LABEL) {
        let _ = existing.show();
        let _ = existing.set_focus();
        return Ok(());
    }

    let html = render_picker_html(initial_path);
    let tmp_path = std::env::temp_dir().join("flowstate-picker.html");
    fs::write(&tmp_path, &html).map_err(|e| format!("write picker tempfile: {e}"))?;

    let url = format!("file://{}", tmp_path.display());
    let parsed: tauri::Url = url
        .parse()
        .map_err(|e| format!("parse picker url {url}: {e}"))?;

    // Promote to Regular activation policy so the window surfaces from
    // a tray-menu callback (same trick as Open UI). Drop back when the
    // window closes — handled in main.rs on_window_event.
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);

    WebviewWindowBuilder::new(app, PICKER_WINDOW_LABEL, WebviewUrl::External(parsed))
        .title("Select a Flowstate project directory")
        .inner_size(720.0, 520.0)
        .min_inner_size(540.0, 380.0)
        .resizable(true)
        .focused(true)
        .build()
        .map_err(|e| format!("build picker window: {e}"))?;

    Ok(())
}

/// Render the picker HTML with the initial path embedded so the JS
/// doesn't need a separate "get default start path" round-trip.
fn render_picker_html(initial_path: &Path) -> String {
    let initial_json =
        serde_json::to_string(&initial_path.to_string_lossy().to_string()).unwrap_or_default();
    PICKER_HTML.replace("__INITIAL_PATH__", &initial_json)
}

const PICKER_HTML: &str = include_str!("picker.html");
