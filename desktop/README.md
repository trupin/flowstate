# Flowstate Desktop App

Menubar app for macOS that supervises a local `flowstate server` and
surfaces project status from the system tray. Same UX category as
Postgres.app, OrbStack, Tailscale.

## For end users — install + first launch

Download `Flowstate-X.Y.Z-aarch64.dmg` (Apple Silicon) or
`Flowstate-X.Y.Z-x86_64.dmg` (Intel) from the [Releases page][releases].

> [!IMPORTANT]
> **The DMG is unsigned.** macOS Gatekeeper will refuse to launch it on
> the first try. Pick one workaround:

**Option A — right-click → Open** (recommended for non-technical users):

1. Drag `Flowstate.app` from the DMG into `/Applications`.
2. **Right-click** (or Control-click) `Flowstate.app` in `/Applications`
   → **Open**.
3. Click **Open** in the "developer cannot be verified" dialog.
4. Subsequent launches don't need this — macOS remembers the override.

**Option B — strip the quarantine attribute** (one terminal command):

```bash
xattr -d com.apple.quarantine /Applications/Flowstate.app
```

Then launch normally from Spotlight / Launchpad.

> [!NOTE]
> Apple Developer cert + notarization is intentionally deferred. See
> [`specs.md` §13.5][specs] for the rationale. Once distribution friction
> warrants it, we'll add a signed/notarized release path.

[releases]: https://github.com/trupin/flowstate/releases
[specs]: ../specs.md

## What's in the app

- **Menubar icon** — three states: idle (dimmed), running (green), error (red).
- **Project picker** — pick any directory containing `flowstate.toml`.
  Persisted across launches in `~/.flowstate/desktop_state.json`.
- **Server lifecycle** — the app spawns `python -m flowstate server` as
  a child process, polls `/health` every 5s, and stops the child cleanly
  on quit (SIGTERM + 5s grace + SIGKILL fallback).
- **Open UI** — opens the React UI in a Tauri webview window
  (`Open in Browser` opens it in your default browser instead).
- **No Dock icon** — the app uses macOS `Accessory` activation policy.

The bundled Python runtime ships inside the `.app` itself
(`Contents/Resources/python/`) — no system Python install needed.

### `harness="sdk"` flows: `claude` must be on PATH

The bundled Python tree omits `claude_agent_sdk`'s 196 MB embedded `claude`
binary (UI-079 trims it). The default `AcpHarness` spawns
`claude-agent-acp` from PATH and is unaffected. But flows that explicitly
set `harness = "sdk"` need a `claude` binary on PATH at runtime.

If your active project has any SDK-harness flow and `claude` isn't on
PATH, the tray dropdown surfaces a `⚠ claude not on PATH` warning row
above the project name.

> [!NOTE]
> **Launchd PATH ≠ shell PATH.** Apps launched from Spotlight / Finder
> inherit launchd's PATH (typically `/usr/bin:/bin:/usr/sbin:/sbin`),
> not your shell's `~/.zshrc`-augmented PATH. If `claude` is on your
> shell PATH but the warning still fires, install `claude` to a
> launchd-visible location (e.g. `/usr/local/bin/`) or add it via
> `launchctl setenv PATH "$PATH:..."`. Install instructions:
> <https://docs.anthropic.com/en/docs/claude-code/quickstart>.

## For maintainers — building from source

Prereqs:

- Rust toolchain (`cargo` 1.77+) — install via `rustup`.
- Tauri CLI: `cargo install tauri-cli --locked --version "^2.0"`.
- `jq` for the build script.
- A working Flowstate dev environment (`uv build` must succeed at the
  repo root — the build script invokes it to produce a fresh wheel).

### Quick dev loop

```bash
# From the repo root.
. "$HOME/.cargo/env"

# Optional: point the spawned server at the repo's venv interpreter
# instead of the bundled Python (cargo tauri dev doesn't bundle).
export FLOWSTATE_PYTHON="$PWD/.venv/bin/python"

cd desktop/src-tauri
cargo tauri dev          # interactive — opens the menubar app from source
```

### Producing a release `.dmg`

```bash
bash desktop/scripts/build.sh                       # auto-detect arch
bash desktop/scripts/build.sh aarch64-apple-darwin  # explicit
bash desktop/scripts/build.sh x86_64-apple-darwin
```

This:

1. Rebuilds the Flowstate wheel via `uv build --wheel`.
2. Calls `desktop/scripts/vendor_python.sh <arch>` to populate
   `desktop/python/` with a fresh `python-build-standalone` runtime
   plus the wheel installed into it.
3. Runs `cargo tauri build --target <arch>` to produce a `.app` + `.dmg`.
4. Copies the DMG to `desktop/dist/Flowstate-X.Y.Z-<arch>.dmg`.
5. Prints the final `.app` and `.dmg` sizes.

The first build takes ~10 min (Tauri pulls macOS framework deps).
Subsequent builds are ~1-2 min.

### Bumping the version

Single source of truth: `desktop/src-tauri/tauri.conf.json` `version`
field. The build script reads it via `jq` and uses it in the output
filename. Bump it before cutting a release.

### Known size

The `.app` is currently ~400 MB and the `.dmg` ~200 MB. The bulk is
`claude_agent_sdk`'s bundled `claude` Mach-O (~196 MB). Trimming this
is filed as **UI-079**.

## Architecture

See [`../specs.md` §13.5 Desktop App (Menubar)](../specs.md) for the full
v0 design. Source layout:

```
desktop/
├── README.md           ← this file
├── src-tauri/
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   ├── src/
│   │   ├── main.rs     ← entry, tray builder, dispatcher, resolve_python
│   │   ├── server.rs   ← FlowstateServer child supervisor (SIGTERM/grace/kill)
│   │   ├── health.rs   ← tokio /health poller
│   │   ├── menu.rs     ← native tray menu builder
│   │   └── project.rs  ← desktop_state.json + flowstate.toml validation
│   └── icons/          ← tray icons (idle/running/error) + app icon
├── scripts/
│   ├── vendor_python.sh        ← UI-075: fetch python-build-standalone
│   ├── build.sh                ← UI-077: produce the .dmg
│   └── generate_tray_icons.py  ← regenerate placeholder PNGs
└── dist/               ← build output (gitignored)
```
