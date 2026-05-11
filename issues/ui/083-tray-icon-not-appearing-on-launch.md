# [UI-083] Tray icon not appearing on launch (LSUIElement missing)

## Domain
ui

## Status
done

## Priority
P1

## Dependencies
- Depends on: UI-074
- Blocks: —

## Spec References
- specs.md — menubar app lifecycle (Phase 35)

## Summary
On macOS Sequoia 15.6, launching `/Applications/Flowstate.app` sometimes
leaves the process alive with no tray icon in the menubar. The bundled
`Info.plist` does not declare `LSUIElement=true`, so the app starts as a
regular Foreground app and only transitions to menubar-only at runtime
via `app.set_activation_policy(Accessory)` inside `setup()`. That
transition races with `NSStatusItem` registration — when it loses, the
status item never appears but the run loop keeps going (the
`prevent_exit` guard in `RunEvent::ExitRequested` is doing its job).

Fix: declare the app as menubar-only in `Info.plist` from launch so the
runtime transition is a no-op rather than a race.

## Acceptance Criteria
- [x] `LSUIElement=true` lands in the bundled `.app/Contents/Info.plist`
      after `bash desktop/scripts/build.sh`.
- [x] Launching a freshly-built `.app` reliably shows the tray icon on
      every launch — no Dock icon, no race.
- [x] Webview window open/close still toggles `Regular ↔ Accessory`
      correctly (existing `set_activation_policy` calls keep working).

## Technical Design

### Files to Create/Modify
- `desktop/src-tauri/Info.plist` — new file. Tauri 2's CLI auto-detects
  `Info.plist` next to `tauri.conf.json` and merges it into the generated
  one (`tauri-cli-2.11.1/src/interface/rust.rs` — `tauri_dir.join("Info.plist")`).

### Key Implementation Details
Minimal plist with one key:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
```

`main.rs:setup()` keeps calling `set_activation_policy(Accessory)` — now
a no-op on initial launch, but still required for the on_window_event
handler that restores Accessory after the webview window is destroyed.

### Edge Cases
- `pick_project` and `install_cli_shim` temporarily promote to Regular
  so the AppleScript dialog can surface; unchanged.
- `open_ui_window` promotes to Regular; on Destroyed it drops back.
  Unchanged.

## Testing Strategy
Manual GUI on the user's notched MacBook Air M2 / macOS 15.6.1.

## E2E Verification Plan

### Reproduction Steps
1. Install the pre-fix build: `/Applications/Flowstate.app` (mtime
   2026-05-11 16:02:46 in this case).
2. Launch via `open /Applications/Flowstate.app` or
   `/Applications/Flowstate.app/Contents/MacOS/flowstate-desktop`.
3. Expected: tray icon visible in menubar.
4. Actual: process runs (`pgrep -fl flowstate-desktop` matches; bundled
   Python server child spawns on 9090+), but no tray icon. System log
   shows zero `NSStatusBarWindow` activity for the PID.

### Verification Steps
1. `bash desktop/scripts/build.sh` — produces a fresh `.dmg` whose
   `Flowstate.app/Contents/Info.plist` contains `<key>LSUIElement</key><true/>`.
2. `bash desktop/scripts/install.sh` — replaces `/Applications/Flowstate.app`
   without the LaunchServices inode-cache footgun (UI-082).
3. Launch the app several times; tray icon appears on every launch.
4. Click tray → menu opens; "Open UI" still works (window promotes to
   Regular and back).

## E2E Verification Log

### Reproduction
- `log show --predicate 'process == "flowstate-desktop"' --start "2026-05-11 16:10:30"` —
  no `NSStatusBarWindow` activity for PIDs 30270, 30435, 30746, 30841,
  31956, 33089 (all post-16:10 launches of the same binary that did
  create a `NSStatusBarWindow` earlier in the day for PIDs 28169,
  28179, 28337). Same binary mtime (16:02:46), so it's a runtime race,
  not a code regression.
- `plutil -lint /Applications/Flowstate.app/Contents/Info.plist` →
  `OK`; `/usr/libexec/PlistBuddy -c "Print :LSUIElement"` →
  `Entry, ":LSUIElement", Does Not Exist` (confirms the missing key).

### Post-Implementation Verification
_Pending user-side rebuild + install via `desktop/scripts/install.sh`._

## Completion Checklist
- [x] `plutil -lint` passes on the new file.
- [x] Tauri bundler auto-merge path verified in tauri-cli source.
- [ ] User-side rebuild + install + relaunch shows tray icon reliably.
