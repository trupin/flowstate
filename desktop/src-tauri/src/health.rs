//! Health polling task.
//!
//! Polls `http://127.0.0.1:{port}/health` every 5 seconds while a
//! flowstate server is supposed to be running, and emits Tauri events
//! that the tray UI listens for.
//!
//! Events:
//! - `health://running` — payload: [`HealthInfo`]. Server responded 200.
//! - `health://down`    — payload: `{ "port": u16 }`. Connection refused or
//!   non-2xx response.
//! - `health://error`   — payload: `{ "port": u16, "message": String }`.
//!   Unexpected error (DNS, TLS, JSON parse, etc.).
//!
//! v0 design notes:
//! - One poller per "active server" — when the server is restarted on a new
//!   port, the old poller is cancelled (via the [`tokio::sync::watch`] handle
//!   stored in [`crate::AppState`]) and a fresh poller is spawned.
//! - The poller runs forever until cancelled. Backoff is intentionally
//!   simple (fixed 5s) — exponential backoff is a polish follow-up.

use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};
use tokio::sync::watch;

pub const POLL_INTERVAL: Duration = Duration::from_secs(5);
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(3);

pub const EVENT_RUNNING: &str = "health://running";
pub const EVENT_DOWN: &str = "health://down";
pub const EVENT_ERROR: &str = "health://error";

/// Subset of the JSON returned by `GET /health` (SERVER-031). Extra fields
/// are ignored so the poller is forward-compatible if the server adds new
/// keys.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthResponse {
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub project: Option<HealthProject>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthProject {
    #[serde(default)]
    pub slug: Option<String>,
    #[serde(default)]
    pub root: Option<String>,
}

/// Payload emitted on `health://running`. We `Deserialize` it too so the
/// listener side in `main.rs` can decode the event body without
/// duplicating the schema.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthInfo {
    pub port: u16,
    pub slug: Option<String>,
    pub version: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
struct DownPayload {
    port: u16,
}

#[derive(Debug, Clone, Serialize)]
struct ErrorPayload {
    port: u16,
    message: String,
}

/// Spawn the health polling task on the current tokio runtime.
///
/// `cancel` is a [`watch::Receiver`] that flips to `true` when the caller
/// wants the poller to stop (e.g., the server is being restarted on a new
/// port). The poller exits on the next tick after cancellation.
pub fn spawn_poller(app: AppHandle, port: u16, mut cancel: watch::Receiver<bool>) {
    tauri::async_runtime::spawn(async move {
        let client = match reqwest::Client::builder()
            .timeout(REQUEST_TIMEOUT)
            .build()
        {
            Ok(c) => c,
            Err(e) => {
                let _ = app.emit(
                    EVENT_ERROR,
                    ErrorPayload {
                        port,
                        message: format!("failed to build http client: {e}"),
                    },
                );
                return;
            }
        };

        let url = format!("http://127.0.0.1:{port}/health");
        loop {
            // Cancellation check before doing any work.
            if *cancel.borrow() {
                break;
            }

            match poll_once(&client, &url).await {
                Ok(info) => {
                    let _ = app.emit(
                        EVENT_RUNNING,
                        HealthInfo {
                            port,
                            slug: info.project.and_then(|p| p.slug),
                            version: info.version,
                        },
                    );
                }
                Err(PollError::Down) => {
                    let _ = app.emit(EVENT_DOWN, DownPayload { port });
                }
                Err(PollError::Other(message)) => {
                    let _ = app.emit(EVENT_ERROR, ErrorPayload { port, message });
                }
            }

            // Sleep with cancellation: whichever fires first wins.
            tokio::select! {
                _ = tokio::time::sleep(POLL_INTERVAL) => {},
                _ = cancel.changed() => {
                    if *cancel.borrow() { break; }
                }
            }
        }
    });
}

#[derive(Debug)]
enum PollError {
    /// Server is not up (connection refused, non-2xx status).
    Down,
    /// Other error (timeout, JSON parse, etc.).
    Other(String),
}

async fn poll_once(client: &reqwest::Client, url: &str) -> Result<HealthResponse, PollError> {
    let resp = match client.get(url).send().await {
        Ok(r) => r,
        Err(e) if e.is_connect() || e.is_timeout() => return Err(PollError::Down),
        Err(e) => return Err(PollError::Other(e.to_string())),
    };
    if !resp.status().is_success() {
        return Err(PollError::Down);
    }
    resp.json::<HealthResponse>()
        .await
        .map_err(|e| PollError::Other(format!("invalid /health JSON: {e}")))
}
