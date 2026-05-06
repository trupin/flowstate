# [UI-076] Tauri auto-updater + GitHub Releases manifest

## Domain
ui

## Status
todo

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
- [ ] `tauri-plugin-updater` added to `desktop/src-tauri/Cargo.toml` and registered in the Tauri builder.
- [ ] Tauri keypair generated (`cargo tauri signer generate`); private key stored in 1Password / a maintainer secret store and **not** committed; public key embedded in `tauri.conf.json` under `plugins.updater.pubkey`.
- [ ] `tauri.conf.json` `plugins.updater.endpoints` points at `https://github.com/<org>/flowstate/releases/latest/download/latest.json` (or equivalent GitHub Releases asset).
- [ ] On launch, the app checks the manifest in the background. If a newer version is available, a tray menu item ("Update available — restart to install") becomes enabled. Clicking it triggers `Update::download_and_install` and quits.
- [ ] `latest.json` schema documented in `RELEASING.md` and a sample committed at `desktop/updater/latest.json.example`.
- [ ] Build script (UI-077) signs the `.dmg` using `cargo tauri signer sign` and writes the resulting signature into `latest.json`.

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
_Filled in by the implementing agent._

## Completion Checklist
- [ ] Updater plugin wired
- [ ] Pubkey embedded in tauri.conf.json
- [ ] GitHub Releases manifest live and signed
- [ ] Tray "Update available" UX works
- [ ] RELEASING.md updated with the bump step
