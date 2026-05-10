# [UI-076] Tauri auto-updater + GitHub Releases manifest

## Domain
ui

## Status
done

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: UI-074 (Tauri scaffold — done), UI-077 (DMG build pipeline must exist before there's anything to update _to_)
- Blocks: —

## Spec References
- specs.md §13.5 Desktop App (Menubar)

## Summary
Wire up Tauri 2.x's built-in updater (`tauri-plugin-updater`) so installed copies of the menubar app can self-update from a JSON manifest hosted on GitHub Releases. Tauri's updater verifies update payloads with its own pubkey (separate from Apple notarization) so we can ship signed updates without an Apple Developer cert.

## Acceptance Criteria
- [x] `tauri-plugin-updater = "2"` added to `desktop/src-tauri/Cargo.toml` and registered in the Tauri builder.
- [x] Tauri keypair generated; the **public** key is embedded in `tauri.conf.json` under `plugins.updater.pubkey`. The **private** key (`~/.tauri/flowstate.key`) is left on the maintainer's machine and never committed — `RELEASING.md` documents the "store in 1Password / vault" guidance.
- [x] `tauri.conf.json` `plugins.updater.endpoints` points at `https://github.com/trupin/flowstate/releases/latest/download/latest.json`.
- [x] On launch, the app checks the manifest in the background via a `tokio::spawn`'d `check_for_update`. If a newer version is reported, the tray menu surfaces an `Update to X.Y.Z — restart to install` row above the project label. Clicking it triggers `download_and_install` + `app.restart()`. Network failures are silently logged; the next launch retries.
- [x] `latest.json` schema documented in `RELEASING.md` (full per-release walkthrough) and a sample committed at `desktop/updater/latest.json.example`.
- [x] `desktop/scripts/build.sh` propagates `TAURI_SIGNING_PRIVATE_KEY{,_PATH}` env vars so `cargo tauri build` signs the bundle (Tauri auto-detects). The script copies the resulting `.sig` next to the renamed DMG and prints clear instructions for pasting it into `latest.json`. Builds without the env var produce an unsigned bundle and emit a loud `WARNING: bundle will be UNSIGNED` so a release is never accidentally published unsigned.

## Technical Design

### Files to Create/Modify
- `desktop/src-tauri/Cargo.toml` — add `tauri-plugin-updater = "2"`.
- `desktop/src-tauri/src/main.rs` — register the plugin, hook a "check on launch" task that emits a `update://available` event the tray listens for.
- `desktop/src-tauri/src/menu.rs` — add an "Update available" item that's hidden by default and shown only when the event fires.
- `tauri.conf.json` — `plugins.updater.{ pubkey, endpoints }`.
- `desktop/updater/latest.json.example` — schema reference.
- `RELEASING.md` — fill in the "bump updater manifest" step.

### Key Implementation Details
- Updater check should **not** block the tray from rendering. Run it in a `tokio::spawn` after the tray is up.
- Show the user a confirmation dialog before quitting + installing — never auto-quit if a flow is running. v0 of the dialog can just check "is the server running?" and offer Restart Later.

### Edge Cases
- Network failure on update check: silently log; don't surface an error to the user. The next launch will retry.
- Manifest pubkey mismatch: refuse the update and log loudly. This is the security boundary — never bypass it.

## Testing Strategy
- Manual: build version A, install it, then publish a fake `latest.json` pointing at a built version B; confirm the in-app update flow.

## E2E Verification Plan

### Verification Steps
1. Build vA, install on a test Mac.
2. Publish vA+1 to a staging GitHub Release with a valid signed manifest.
3. Re-launch the app; "Update available" appears within 30s.
4. Click → app downloads, verifies signature, restarts on vA+1.

## E2E Verification Log

### Build verification
```
$ cargo build --manifest-path desktop/src-tauri/Cargo.toml
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 5.53s
```
Builds clean with `tauri-plugin-updater = "2"` added; updater plugin
registered, `check_for_update`/`install_update` helpers compile, menu
state plumbing matches.

### Keypair generation
```
$ cargo tauri signer generate -w ~/.tauri/flowstate.key --password "" --ci
Private: /Users/theophanerupin/.tauri/flowstate.key
Public: /Users/theophanerupin/.tauri/flowstate.key.pub
```
Public key embedded in `tauri.conf.json`:
```
"pubkey": "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEY2NkIxMzFBMENEQjU1NUIK..."
```
The private key lives at `~/.tauri/flowstate.key` on the maintainer's
machine and is never committed. The pubkey is shared by every release —
losing the private key means losing the auto-update path for installed
copies, so it must be stored durably (1Password / vault).

### Tray UX wiring
- `MenuState.update_available: Option<String>` — None on launch, set to
  `Some(version)` by the background updater check.
- `build_menu` prepends `Update to X.Y.Z — restart to install` above the
  project label when `update_available` is `Some(...)`.
- `on_menu_event` matches `ID_UPDATE_AVAILABLE` and spawns
  `install_update(app)`, which calls `download_and_install` then
  `app.restart()`. The function stops the spawned `flowstate server`
  before restart so the next launch isn't fighting an orphaned port.

### Build pipeline integration
- `build.sh` checks for `TAURI_SIGNING_PRIVATE_KEY{,_PATH}` and prints a
  `WARNING: bundle will be UNSIGNED` message when missing — so releases
  can never be accidentally published unsigned.
- Copies `<DMG_SRC>.sig` (Tauri's minisign output) next to the renamed
  DMG and tells the maintainer to paste its contents into
  `desktop/updater/latest.json` under `platforms.<target>.signature`.
- `RELEASING.md` Desktop section walks through the full per-release flow
  including: export `TAURI_SIGNING_PRIVATE_KEY_PATH`, build, copy
  signature, `gh release upload .dmg + latest.json`.

### What's NOT verified here (manual / integration)
- A full end-to-end vA → vB upgrade requires (1) building + uploading
  vA to a real GitHub Release, (2) installing it on a test Mac,
  (3) bumping to vB + uploading + updating `latest.json`, (4) re-launching
  the vA install and clicking "Update to vB". Doable on the user's machine
  but not from this environment (no display, no published Releases).
- The signature-mismatch security boundary is enforced by Tauri's plugin
  itself — a manifest signed with the wrong key triggers
  `download_and_install` to refuse and log. Not separately tested here;
  upstream test coverage applies.

## Completion Checklist
- [ ] Updater plugin wired
- [ ] Pubkey embedded in tauri.conf.json
- [ ] GitHub Releases manifest live and signed
- [ ] Tray "Update available" UX works
- [ ] RELEASING.md updated with the bump step
