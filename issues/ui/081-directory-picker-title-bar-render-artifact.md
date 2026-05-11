# [UI-081] Directory picker title bar has rendering artifacts (menubar app)

## Domain
ui

## Status
in_progress

## Priority
P2 (cosmetic — picker is functional)

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section 12 (UI) — Tauri menubar app behavior
- `issues/ui/074-tauri-menubar-app.md` — original picker spec
- `desktop/src-tauri/src/main.rs:351-360` — existing related `ActivationPolicy` workaround

## Summary
When the menubar app shows its native folder picker via the tray's "Switch Project..." / "New Project Here..." actions, the picker's title bar renders with visual artifacts: animated sparkle / asterisk glyphs overlap the title text "Select a Flowstate project directory" letter-by-letter, and one glyph is centered on the green traffic-light button. The dialog itself is functional — Cancel and Open both work, folder selection completes correctly. Pure rendering bug. Reproduces **deterministically on every picker open** against the unsigned `.dmg` built via `desktop/scripts/build.sh` from current `main`.

The existing code already carries a workaround for an adjacent NSOpenPanel quirk under `ActivationPolicy::Accessory` (anchor-frame coordinate offset — see `main.rs:351-360`). The activation-policy promote/demote cycle resolves click registration but does not resolve the title-bar overlay artifact reported here.

## Reproduction (deterministic)
1. From `main` (commit `e96b964` or later), run `desktop/scripts/build.sh` to produce the unsigned `.dmg`.
2. Install / launch the resulting Flowstate.app.
3. Click the menubar tray icon → "Switch Project..." (or "New Project Here...").
4. The native folder picker opens. Observe: title bar shows "Select a Flowstate project directory" overlaid with animated sparkle glyphs on each letter; one glyph is centered over the green traffic-light button.

Expected: clean title bar with the text and unobstructed traffic-light controls.
Actual: artifact overlays as described. Picker remains functional.

## Investigation hypotheses (in priority order)

1. **Menubar tray icon leaking through to the dialog's window layer.** The tray icon is itself a glyph that resembles the artifact (asterisk/sparkle shape). If Tauri's tray plugin keeps its `NSStatusItem` window at a fixed top-of-screen position and the dialog's title bar happens to be drawn directly below the menu bar, GPU compositing may surface the tray icon's frame inside the dialog's title-bar area. Inspect: take a screen recording, see whether the artifacts animate in lockstep with the tray icon's animation (if it has one) or with a server-status spinner.
2. **`ActivationPolicy` promote race**. The promote-to-Regular call at `main.rs:360` and the `pick_folder` call at `main.rs:366` happen on the same tick — the dialog may render its first frame before the activation policy switch has fully propagated through NSApp, leading to a transient ghosted frame. The drop-back-to-Accessory at `main.rs:370` doesn't apply during render, but the initial frame may already be polluted. Try: insert a small `Task::sleep`/dispatch-after before `pick_folder` to let the activation policy settle, see if artifact disappears.
3. **macOS-version-specific `NSOpenPanel` regression**. The artifact resembles known macOS 14/15 title-bar rendering bugs around `NSWindow` style masks with `.titled` + `.utilityWindow` flags. Tauri's dialog plugin may be picking a window style that triggers it. Check Tauri's `tauri-plugin-dialog` issues for similar reports.
4. **Animated tray icon's frame buffer not invalidated**. The `main.rs:678` `tray.set_icon` call swaps icons (likely for status indicators). If a frame is still being composited when the dialog opens, stale tray-icon pixels could appear over the dialog. Investigate by disabling tray icon updates and re-testing.

## Acceptance Criteria
- [ ] Title bar of the "Select a Flowstate project directory" picker renders cleanly: title text legible without overlay, traffic-light buttons unobstructed
- [ ] Behavior preserved: existing positioning workaround (Accessory ↔ Regular dance) continues to fix click registration
- [ ] Verified on at least one macOS 14 or 15 build (matching the user's environment)

## Technical Design
**Investigation-first.** Don't ship a speculative fix. Start by reproducing under `cargo tauri dev` (where tooling/logs are richer than in the bundled `.dmg`), then narrow which hypothesis above applies before choosing an approach.

Likely interventions, in order from least to most invasive:
1. Add a single dispatched-after-zero or 50ms delay between `set_activation_policy(Regular)` and `pick_folder` to allow the NSApp main run-loop to absorb the activation change.
2. Disable tray icon animation/status updates while a modal native dialog is open (suppress via a flag in `tray.rs` / wherever icon updates are scheduled).
3. Force a window-server compositing flush via `NSApp.activate(ignoringOtherApps: true)` before the dialog call.
4. Switch from `tauri-plugin-dialog`'s `pick_folder` to a direct `rfd` invocation with explicit parent-window anchoring.

## Edge Cases
- The artifact may differ across macOS versions (Sonoma 14 / Sequoia 15 / Tahoe 16). Capture the exact `sw_vers -productVersion` when reproducing.
- The picker can also be opened via "New Project Here..." — verify the fix covers both paths (same underlying `pick_project` call site).
- Multi-monitor setup: artifact may be specific to displays where the dialog spawns at a specific y-coordinate relative to the menu bar.

## Testing Strategy
This is a visual bug in a native dialog — neither pytest nor Playwright can reach it. Verification is by:
- Reproducing in a fresh local build (`desktop/scripts/build.sh` or `cargo tauri dev`).
- Manual screen capture before/after the fix attempt.
- Confirming the existing `ActivationPolicy` workaround (click registration) still works post-fix.

## E2E Verification Plan
1. Build the menubar app via `desktop/scripts/build.sh`.
2. Launch the resulting `Flowstate.app`.
3. Open "Switch Project..." from the tray. Screenshot title bar. Title text should render cleanly, traffic-light buttons unobstructed.
4. Cancel. Re-open. Re-screenshot. Confirm artifact does not reappear on subsequent opens either.
5. Capture `sw_vers -productVersion` and include in the verification log so future regressions can be matched against macOS version.

## E2E Verification Log

### Reproduction (current behavior)
- macOS version: 15.6.1 (Sequoia) — confirmed via `sw_vers -productVersion`
- Build: built from `main` at commit `e96b964` via `desktop/scripts/build.sh`. Screenshot attached in the originating chat shows the artifact.

### Investigation Notes
Read `desktop/src-tauri/src/main.rs:351-399` (the `pick_project` function) and `desktop/src-tauri/src/menu.rs` (the tray menu that dispatches to it).

Considered the four hypotheses in priority order and converged on **H2 (ActivationPolicy promote race)**:

- The artifact reproduces *deterministically on every picker open* — that excludes intermittent compositor glitches (H1) or stale tray-icon frame buffer (H4), which would be flaky.
- The overlay covers the title-bar chrome *including the traffic-light area* — exactly the layer that gets re-decorated when an app's activation policy flips from Accessory (no chrome owner) to Regular (chrome owner). Before NSApp processes the activation change, NSOpenPanel can't pick the right title-bar style → it draws transient utility-window chrome, and a frame or two later AppKit re-decorates with the Regular style on top, leaving glyph ghosts on the title text and traffic-light buttons.
- The existing 351-360 workaround for click-registration confirms the activation dance is needed; it just doesn't give the run-loop time to settle before the panel renders its first frame.

### Fix
File: `desktop/src-tauri/src/main.rs`, function `pick_project` (lines ~349-418).

Wrapped the `dialog().file().pick_folder(...)` call in a `tauri::async_runtime::spawn(async move { ... })` task that performs a `tokio::time::sleep(Duration::from_millis(50))` between `set_activation_policy(Regular)` and the panel-open call. The Regular ↔ Accessory dance and the entire callback body (validate_project_root, stop_server, start_server_for, refresh_sdk_claude_warning) are preserved exactly — only the timing of the panel open changes.

50 ms is invisibly fast to a user but well beyond a single AppKit run-loop tick, so NSApp's activation switch fully propagates before NSOpenPanel performs its first chrome layout.

### Post-Implementation Verification
- macOS version: 15.6.1 (Sequoia)
- Base commit: `f7e7029` (current `main`)
- `cargo check` (in `desktop/src-tauri/`): passes, no errors, no warnings on the edited file.
- `cargo clippy` (in `desktop/src-tauri/`): passes, no new warnings. (One pre-existing `match_like_matches_macro` warning in `server.rs:68` is unrelated to this change.)
- `cargo tauri build --target aarch64-apple-darwin` via `desktop/scripts/build.sh`: succeeded. Output: `desktop/dist/Flowstate-0.0.1-aarch64.dmg` (102.9 MB). The unrelated `TAURI_SIGNING_PRIVATE_KEY` warning at the end of the run is expected for local unsigned builds (UI-076 release-pipeline only).
- Visual confirmation: **requires user-side verification** — open the built `desktop/dist/Flowstate-0.0.1-aarch64.dmg`, install/launch `Flowstate.app`, click the tray icon → "Switch Project…", and confirm the title bar of "Select a Flowstate project directory" renders cleanly: text is legible without overlay glyphs, the green traffic-light button is unobstructed. Repeat the open/cancel cycle 2-3 times to confirm the artifact does not return on subsequent opens. Behavior parity: clicks on Open / Cancel / folder rows should still register at their visual position (existing click-coordinate workaround preserved). The artifact is purely visual — neither pytest nor Playwright can reach a native NSOpenPanel, so this final step has to be eyeballed.

## Completion Checklist
- [ ] Root cause narrowed to one of the listed hypotheses
- [ ] Fix applied with the minimum-blast-radius option
- [ ] Built `.dmg` shows clean title bar on the user's macOS version
- [ ] Existing click-registration workaround still functional
- [ ] `/lint` passes (`cargo clippy` for Rust portion)
