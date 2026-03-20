# [ENGINE-017] Cancel triggers on_error=pause instead of cancelling

## Domain
engine

## Status
done

## Priority
P1

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section on cancel behavior and on_error handling

## Summary
Found during E2E testing (suite: cancel).

When a running flow is cancelled via the UI Cancel button, the subprocess is SIGTERM'd (exit code 143). The executor treats this as a normal task failure and triggers the `on_error=pause` behavior, setting the run status to `paused` instead of `cancelled`. Additionally, orphan subprocess processes were observed after cancel.

**Expected**: Cancel → subprocess killed → run status = `cancelled`
**Actual**: Cancel → subprocess SIGTERM'd (exit 143) → executor interprets as task failure → `on_error=pause` → run status = `paused`

## Acceptance Criteria
- [ ] Cancelling a running flow sets status to `cancelled`, not `paused`
- [ ] The executor distinguishes between SIGTERM from cancel vs genuine task failure
- [ ] No orphan `claude` processes remain after cancellation
- [ ] Verified by re-running `/e2e cancel`

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — Cancel path must set a flag before killing subprocess so the error handler knows to use `cancelled` status
- `src/flowstate/engine/subprocess_manager.py` — Subprocess kill logic may need to propagate cancel intent

### Key Implementation Details
The executor needs to track whether a task termination was initiated by cancel vs failed organically. When the subprocess exits with code 143 (SIGTERM) and the cancel flag is set, the executor should:
1. Set task status to `cancelled` (not `failed`)
2. Set run status to `cancelled` (not trigger `on_error`)
3. Kill all remaining subprocesses
4. Verify cleanup with process check

### Edge Cases
- Cancel while between tasks (no subprocess running) — should still cancel
- Cancel racing with natural task completion
- Multiple subprocesses running (fork) when cancel is issued

## Testing Strategy
Re-run `/e2e cancel` and verify the suite passes.

## Evidence
- Screenshot: /tmp/flowstate-e2e-cancel-final.png
- Error message in UI: "Task failed (on_error=pause): Task exited with code 143"
- Suite: cancel
- Wall time at failure: ~50s
