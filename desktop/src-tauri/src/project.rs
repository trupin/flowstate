//! Project-state persistence and selection helpers.
//!
//! The desktop app stores a tiny piece of state at
//! `~/.flowstate/desktop_state.json` so that re-launching the menubar app
//! restores the most-recently-used project. The file is created on first
//! write and is safe to delete (the next launch will start with no project
//! selected).

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};

/// Path under `~/.flowstate/` where desktop state lives. We deliberately
/// stay inside the existing Flowstate data dir so users only have one
/// place to clean up.
pub const STATE_FILENAME: &str = "desktop_state.json";

/// The project anchor file we look for to validate a directory.
pub const PROJECT_ANCHOR: &str = "flowstate.toml";

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DesktopState {
    /// Most recently opened project root, if any. Persisted across
    /// launches so the user doesn't have to re-pick on every start.
    #[serde(default)]
    pub last_project_root: Option<PathBuf>,
}

/// Resolve `~/.flowstate/`, creating it if it doesn't exist.
pub fn flowstate_dir() -> Result<PathBuf> {
    let home = dirs::home_dir().ok_or_else(|| anyhow!("could not resolve home directory"))?;
    let dir = home.join(".flowstate");
    fs::create_dir_all(&dir).with_context(|| format!("failed to create {}", dir.display()))?;
    Ok(dir)
}

pub fn state_path() -> Result<PathBuf> {
    Ok(flowstate_dir()?.join(STATE_FILENAME))
}

/// Load the persisted desktop state. Missing or corrupt files yield a
/// fresh default (logged) — never a hard error, since this state is
/// strictly a UX nicety.
pub fn load_state() -> DesktopState {
    let path = match state_path() {
        Ok(p) => p,
        Err(e) => {
            log::warn!("could not resolve desktop state path: {e}");
            return DesktopState::default();
        }
    };
    if !path.exists() {
        return DesktopState::default();
    }
    match fs::read_to_string(&path) {
        Ok(s) => match serde_json::from_str::<DesktopState>(&s) {
            Ok(state) => state,
            Err(e) => {
                log::warn!("desktop_state.json is corrupt ({e}); ignoring.");
                DesktopState::default()
            }
        },
        Err(e) => {
            log::warn!("failed to read {}: {e}", path.display());
            DesktopState::default()
        }
    }
}

/// Persist the desktop state. Errors are logged and swallowed — same
/// rationale as [`load_state`].
pub fn save_state(state: &DesktopState) {
    let path = match state_path() {
        Ok(p) => p,
        Err(e) => {
            log::warn!("could not resolve desktop state path: {e}");
            return;
        }
    };
    let json = match serde_json::to_string_pretty(state) {
        Ok(s) => s,
        Err(e) => {
            log::warn!("failed to serialize desktop state: {e}");
            return;
        }
    };
    if let Err(e) = fs::write(&path, json) {
        log::warn!("failed to write {}: {e}", path.display());
    }
}

/// Validate that a candidate directory looks like a Flowstate project.
/// v0: just checks for `flowstate.toml`. Future: parse it and surface the
/// project slug in the error message.
pub fn validate_project_root(path: &Path) -> Result<()> {
    if !path.is_dir() {
        return Err(anyhow!("{} is not a directory", path.display()));
    }
    let anchor = path.join(PROJECT_ANCHOR);
    if !anchor.exists() {
        return Err(anyhow!(
            "{} has no {PROJECT_ANCHOR} — pick a Flowstate project directory or run `flowstate init` there first",
            path.display()
        ));
    }
    Ok(())
}
