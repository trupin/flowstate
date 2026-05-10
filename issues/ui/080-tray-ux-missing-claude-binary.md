# [UI-080] Tray UX when `harness="sdk"` flows can't find `claude` on PATH

## Domain
ui (Rust + light engine touch)

## Status
todo

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: UI-079 (strip removes the bundled fallback so PATH lookup matters)
- Blocks: —

## Spec References
- specs.md §13.5 Desktop App (Menubar)

## Summary
After UI-079 stripped `claude_agent_sdk/_bundled/claude` from the menubar app's bundled Python (saving ~195 MB), flows that explicitly set `harness="sdk"` now require a `claude` binary on PATH. If the user's `claude-code` isn't installed, the SDK harness fails at task-execution time with a `ProcessError` from the SDK's own resolver — visible in the run-detail log viewer but not surfaced anywhere ahead of time. This issue adds a clear "Claude not found" indicator in the tray menu so users see the problem before triggering a flow that depends on it.

## Acceptance Criteria
- [ ] On menubar app startup, the desktop binary probes for `claude` on PATH (`which claude` equivalent).
- [ ] If missing, the tray dropdown shows a yellow/orange "⚠ `claude` not on PATH" line above the project label, with a tooltip linking to install instructions (`https://docs.anthropic.com/en/docs/claude-code/quickstart`).
- [ ] If present, no extra menu line appears.
- [ ] The probe re-runs whenever the `Switch Project…` flow completes, so users who install `claude` mid-session see the warning clear without quitting the app.
- [ ] AcpHarness flows (the default) are unaffected — the warning only shows when SDK harness might be invoked. Heuristic: probe the project's `flows/*.flow` for `harness = "sdk"`; only show the warning if at least one flow uses it.

## Technical Design

### Files to Modify
- `desktop/src-tauri/src/main.rs` — add a `probe_claude_on_path()` helper; call it from `setup` and after `pick_project` succeeds.
- `desktop/src-tauri/src/menu.rs` — add an optional warning row above `project_label`.

### Key Implementation Details
```rust
// main.rs
fn probe_claude_on_path() -> bool {
    std::env::var_os("PATH")
        .map(|path| {
            std::env::split_paths(&path)
                .any(|dir| dir.join("claude").is_file())
        })
        .unwrap_or(false)
}
```

For the per-project check, parse `flowstate.toml` + scan the project's
`flows/*.flow` files for `harness = "sdk"` declarations. Skip the warning
if no flow uses SDK harness — most projects only use AcpHarness.

### Edge Cases
- macOS shell PATH vs the GUI launchd PATH differ. Apps launched from
  Spotlight inherit the launchd PATH, which often excludes shell-set
  paths like `~/.npm-global/bin`. Document this in `desktop/README.md`
  if the warning fires unexpectedly.

## Testing Strategy
- Manual: with `claude` on PATH, launch the menubar app — no warning.
- Manual: rename `~/.local/bin/claude` (or whatever's on PATH) to
  something else, relaunch — warning row appears.

## E2E Verification Plan

### Verification Steps
1. Confirm `claude` is on PATH (`which claude`).
2. Launch the menubar app — open the dropdown, no warning row.
3. Move the binary aside: `mv "$(which claude)" "$(which claude).bak"`.
4. Quit + relaunch the menubar app — warning row visible.
5. Restore: `mv "$(which claude).bak" "$(which claude)"`.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `probe_claude_on_path()` runs at startup + after project switch
- [ ] Warning row only shows when at least one project flow uses `harness="sdk"`
- [ ] Tooltip links to claude-code install docs
- [ ] `desktop/README.md` notes the launchd-PATH-vs-shell-PATH gotcha
