# [ENGINE-018] Resume does not restart execution after pause

## Domain
engine

## Status
done

## Priority
P2

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section on pause/resume behavior

## Summary
Found during E2E testing (suite: controls).

After pausing a running flow via the UI Pause button, clicking Resume does not restart execution. The flow remains in `paused` status indefinitely. The pause itself works correctly — the UI shows "PAUSED" and the running task completes, but no new tasks are scheduled after resume.

**Expected**: Resume → executor resumes → pending tasks start executing → flow completes
**Actual**: Resume → nothing happens → flow stays paused

## Acceptance Criteria
- [ ] Resuming a paused flow restarts the executor loop
- [ ] Pending tasks begin executing after resume
- [ ] The flow eventually reaches a terminal state (completed/failed)
- [ ] Verified by re-running `/e2e controls`

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — Resume handler must restart the execution loop
- `src/flowstate/server/routes.py` — Resume API endpoint may need to signal the executor

### Key Implementation Details
The executor's resume path likely only updates the DB status to `running` but doesn't actually restart the execution loop. The executor needs to:
1. Update run status to `running`
2. Find pending tasks whose predecessors are all complete
3. Start executing the next task(s)
4. Re-enter the main execution loop

### Edge Cases
- Resume after all tasks are complete (should just mark as completed)
- Resume when the next task is a fork (should start both branches)
- Multiple pause/resume cycles

## Testing Strategy
Re-run `/e2e controls` and verify the suite passes.

## Evidence
- Screenshot: /tmp/flowstate-e2e-controls-paused.png (shows PAUSED state with Resume button)
- Screenshot: /tmp/flowstate-e2e-controls-final.png (still PAUSED after resume)
- Suite: controls
- Timed out after 10 minutes waiting for resume
