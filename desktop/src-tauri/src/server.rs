//! Flowstate server child-process supervision.
//!
//! Spawns `<python> -m flowstate server --port N --host 127.0.0.1` where
//! `<python>` is resolved by `main::resolve_python` (UI-075):
//!   1. `FLOWSTATE_PYTHON` env var (dev override)
//!   2. The bundled portable Python at `Resource/python/bin/python3`
//!      shipped inside the `.app` (production)
//!   3. `python3` from `PATH` (fallback for `cargo tauri dev` runs that
//!      haven't been vendored yet)
//!
//! Lifecycle guarantees:
//! - [`FlowstateServer::start`] spawns the child and stores its handle.
//! - [`FlowstateServer::stop`] sends SIGTERM, waits up to 5 seconds, then
//!   SIGKILLs if still alive.
//! - [`Drop`] calls [`FlowstateServer::kill_now`] as a last resort so we
//!   never leak a Python process if the menubar app crashes.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use anyhow::{anyhow, Context, Result};

#[cfg(unix)]
use std::os::unix::process::ExitStatusExt;

/// Port range the server scans for a free port. Mirrors the Flowstate
/// default of 9090 in `flowstate.toml`.
pub const PORT_SCAN_START: u16 = 9090;
pub const PORT_SCAN_END: u16 = 9099;

/// Grace period before SIGKILL on stop.
pub const STOP_GRACE: Duration = Duration::from_secs(5);

/// Supervises a single `flowstate server` child process.
pub struct FlowstateServer {
    child: Option<Child>,
    port: u16,
    project_root: PathBuf,
    /// Path to a `python3` interpreter that has Flowstate installed.
    /// Resolved by the caller (typically `main::resolve_python`) so the
    /// supervisor stays decoupled from Tauri's resource resolver.
    python: OsString,
}

impl FlowstateServer {
    pub fn new(project_root: PathBuf, python: OsString) -> Self {
        Self {
            child: None,
            port: 0,
            project_root,
            python,
        }
    }

    pub fn project_root(&self) -> &Path {
        &self.project_root
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn is_running(&mut self) -> bool {
        match self.child.as_mut() {
            None => false,
            Some(child) => match child.try_wait() {
                Ok(None) => true,
                _ => false,
            },
        }
    }

    /// Start the server. Picks a free port in [`PORT_SCAN_START`, `PORT_SCAN_END`].
    /// Errors if no port is free or the child cannot be spawned.
    pub fn start(&mut self) -> Result<u16> {
        if self.is_running() {
            return Ok(self.port);
        }
        let port = find_free_port(PORT_SCAN_START, PORT_SCAN_END)
            .context("no free port available in scan range")?;

        let mut cmd = Command::new(&self.python);
        cmd.arg("-m")
            .arg("flowstate")
            .arg("server")
            .arg("--port")
            .arg(port.to_string())
            .arg("--host")
            .arg("127.0.0.1")
            // The server reads `flowstate.toml` from CWD; setting CWD to the
            // project root is enough for v0. We also set FLOWSTATE_CONFIG as
            // a belt-and-suspenders signal in case the server picks it up
            // explicitly in a future version.
            .current_dir(&self.project_root)
            .env(
                "FLOWSTATE_CONFIG",
                self.project_root.join("flowstate.toml"),
            )
            // Pipe stdout/stderr so we can later surface logs in the menu
            // ("Show Logs" is a polish follow-up). For v0 we just inherit
            // them so they show up in Console.app when the .app is launched
            // from Finder.
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let child = cmd
            .spawn()
            .with_context(|| format!("failed to spawn flowstate server (python={:?})", self.python))?;
        self.child = Some(child);
        self.port = port;
        Ok(port)
    }

    /// Send SIGTERM to the child and wait up to [`STOP_GRACE`] for it to
    /// exit. If the grace period expires, SIGKILL.
    pub fn stop(&mut self) -> Result<()> {
        let Some(mut child) = self.child.take() else {
            return Ok(());
        };

        #[cfg(unix)]
        {
            let pid = child.id() as i32;
            // libc::kill is unsafe; we wrap it in a small helper so the
            // unsafe surface is contained.
            let _ = unix_send_signal(pid, libc::SIGTERM);
        }
        #[cfg(not(unix))]
        {
            // On non-unix (Windows future) fall back to a hard kill. The
            // graceful-shutdown story for Windows is a follow-up.
            let _ = child.kill();
        }

        let deadline = Instant::now() + STOP_GRACE;
        loop {
            match child.try_wait() {
                Ok(Some(_status)) => {
                    self.port = 0;
                    return Ok(());
                }
                Ok(None) => {
                    if Instant::now() >= deadline {
                        let _ = child.kill();
                        let _ = child.wait();
                        self.port = 0;
                        return Ok(());
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
                Err(e) => {
                    return Err(anyhow!("failed to poll child status: {e}"));
                }
            }
        }
    }

    /// Last-resort kill used by Drop. Best-effort, never panics.
    fn kill_now(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for FlowstateServer {
    fn drop(&mut self) {
        self.kill_now();
    }
}

/// Returns the first port in the range that we can bind on 127.0.0.1.
///
/// Race-condition note: there's a TOCTOU window between us closing the
/// listener and the child binding it. v0 ignores this — `flowstate server`
/// will fail with a clear error if the port is taken in the interim, and
/// the user will see it in the tray's error state.
pub fn find_free_port(start: u16, end: u16) -> Option<u16> {
    use std::net::{Ipv4Addr, SocketAddrV4, TcpListener};
    for port in start..=end {
        let addr = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
        if TcpListener::bind(addr).is_ok() {
            return Some(port);
        }
    }
    None
}

#[cfg(unix)]
fn unix_send_signal(pid: i32, sig: i32) -> Result<()> {
    // Safety: `kill(2)` is a well-defined libc call. We only pass an
    // already-spawned PID we own; sending SIGTERM/SIGKILL has no memory
    // safety implications.
    let rc = unsafe { libc::kill(pid, sig) };
    if rc == 0 {
        Ok(())
    } else {
        Err(anyhow!(
            "kill({pid}, {sig}) failed: errno={}",
            std::io::Error::last_os_error()
        ))
    }
}

#[cfg(unix)]
mod libc {
    // We avoid pulling the full `libc` crate into Cargo.toml for v0 to
    // keep the dep surface small. `kill(2)` is stable and trivially
    // declared. If the dep set grows, switch to the `libc` crate.
    pub const SIGTERM: i32 = 15;
    pub const SIGKILL: i32 = 9;
    extern "C" {
        pub fn kill(pid: i32, sig: i32) -> i32;
    }
    // Silence dead_code on SIGKILL — kept for future use ("force stop").
    #[allow(dead_code)]
    fn _keep_sigkill_used() {
        let _ = SIGKILL;
    }
}

// `ExitStatusExt` is imported so the `From` glue compiles cleanly on unix.
// Silence the unused-import warning since v0 doesn't actually inspect exit
// signals — that's a follow-up when "Show Logs" lands.
#[cfg(unix)]
#[allow(dead_code)]
fn _unused_exit_status_ext() {
    fn _take(s: std::process::ExitStatus) -> Option<i32> {
        s.signal()
    }
}
