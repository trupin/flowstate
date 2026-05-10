# [UI-079] Trim vendored Python size — claude-agent-sdk's bundled `claude` binary dominates

## Domain
ui (with light shared/server touch)

## Status
todo

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: UI-075 (vendored Python in place)
- Blocks: —

## Spec References
- specs.md §13.5 Desktop App (Menubar)

## Summary
UI-075 landed a portable Python at `desktop/python/`, but the resulting tree is **~330 MB** versus the original target of < 120 MB. The dominant cost is `claude_agent_sdk/_bundled/claude` — a 196 MB Mach-O binary that the SDK vendors for its own subprocess management. Strip it (or replace the Python wheel install with `--no-deps` + a vetted minimum dep set) so the `.app` lands closer to ~100 MB and downloads/updates feel reasonable.

## Acceptance Criteria
- [ ] Vendored `desktop/python/` is under 150 MB after `vendor_python.sh` runs.
- [ ] Flowstate still functions end-to-end when launched from the menubar app — flows execute, ACP harness works, lumon plugin loads when configured.
- [ ] Decision documented: either (a) we strip `claude_agent_sdk/_bundled/claude` and rely on a `claude`/`claude-agent-acp` from PATH (with a clear error if missing), or (b) we replace `claude_agent_sdk` with a thinner runtime client.
- [ ] If (a), the menubar app shows a clear "Claude not found on PATH" state in the tray menu when the bundled binary is absent and no PATH fallback is found.

## Technical Design

### Sketch — Option A (strip bundled binary)
- Add a post-install pruning step in `desktop/scripts/vendor_python.sh`:
  ```bash
  rm -f "$PYTHON_DIR/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude"
  ```
- Verify that `claude_agent_sdk` falls back to a `claude` on PATH when the bundled binary is missing. If it doesn't, patch the SDK's resolver via a small monkey-patch in `flowstate.cli` startup, or open an upstream PR.
- Add a startup probe in the menubar app: if the bundled python's `claude_agent_sdk` can't find `claude` and PATH has none either, surface "Install claude-code first" in the tray.

### Sketch — Option B (drop claude-agent-sdk)
- Audit which `claude_agent_sdk` symbols flowstate actually uses (likely a thin wrapper around ACP — the SDK's own ACP module + types).
- Replace with direct ACP protocol calls from the existing `acp` package or roll a minimal client.
- More work but cleaner long-term — the SDK is a heavyweight dep for what we use.

Pick (A) for quick wins, (B) if (A) reveals deeper coupling.

### Edge Cases
- A user who has never installed `claude` on the host and downloads the `.dmg` should still get a working app (path A requires guarded UX, path B requires no extra install).
- macOS Gatekeeper / hardened runtime — stripping a binary inside an unsigned bundle is fine; signing (a future P3) would need to be reapplied to the trimmed tree.

## Testing Strategy
- Manual: `bash desktop/scripts/vendor_python.sh aarch64-apple-darwin && du -sh desktop/python` — under 150 MB.
- Manual: launch the menubar app, run a flow with the ACP harness against a real `claude`/`claude-agent-acp`, confirm it completes without errors.

## E2E Verification Plan

### Verification Steps
1. Re-vendor: `bash desktop/scripts/vendor_python.sh aarch64-apple-darwin`.
2. Size check: `du -sh desktop/python` returns < 150 MB.
3. Smoke run: from the menubar app, pick a project with an ACP-harness flow, run it end-to-end. Confirm no errors related to missing `claude` binary.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] Vendored Python under 150 MB
- [ ] Menubar app still runs ACP-harness flows end-to-end
- [ ] Decision documented in commit / issue closeout
- [ ] If option A: tray UX surfaces missing-PATH state
