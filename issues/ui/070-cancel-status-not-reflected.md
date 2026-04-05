# [UI-070] Cancel button succeeds on backend but UI does not reflect cancelled status

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md Section 10 — WebSocket protocol / flow events

## Summary
When the user taps the Cancel button on a running flow, the backend successfully cancels the flow (DB status becomes `cancelled`, subprocesses are killed), but the UI does not update to reflect the cancellation. The flow graph, control panel status badge, and node statuses all remain in their pre-cancel visual state.

## Acceptance Criteria
- [ ] After clicking Cancel on a running flow, the control panel status displays `cancelled` within 2 seconds
- [ ] The graph nodes for tasks that were running/pending show `failed` status after cancellation
- [ ] The "View Results" button appears after cancellation (already coded for `cancelled` status)
- [ ] The Cancel/Pause buttons disappear (since `isActive` becomes false when status is `cancelled`)

## Technical Design

### Root Cause Investigation

The cancel flow traverses: UI button → WebSocket `{action: 'cancel'}` → server `_handle_control` → `executor.cancel()` → `self._emit(FlowEvent(FLOW_STATUS_CHANGED))` → `on_flow_event` → `broadcast_event` → UI `applyEvent` handler.

The code path looks correct on paper. The most likely root causes, in order of probability:

1. **Exception before event emission**: `executor.cancel()` (`src/flowstate/engine/executor.py:1628-1697`) does significant work (kill subprocesses, await gather, update DB rows, cleanup worktrees) before emitting the status changed event at line 1686. If any intermediate step raises an unhandled exception, the emit is never reached. The `_handle_control` method (`websocket.py:248-264`) has no try/except around the `await executor.cancel()` call, so the exception would propagate silently.

2. **Race condition with execute() completion**: When `cancel()` sets `self._cancelled = True`, the `execute()` main loop breaks and the asyncio Task completes, triggering `_on_run_complete` which removes the executor from the RunManager. If this happens while `cancel()` is still in its `await asyncio.gather(...)` (line 1660), subsequent operations in `cancel()` that depend on executor state might behave unexpectedly.

3. **Event `flow_run_id` mismatch**: `_handle_control` uses `actual_run_id = getattr(executor, "_flow_run_id", None) or flow_run_id`. The emitted event uses this `actual_run_id`, but the client is subscribed using the ID from the URL. If these differ, `broadcast_event` sends to the wrong subscription channel.

4. **Task status events missing**: `executor.cancel()` marks tasks as `failed` in the DB directly but does NOT emit `task.failed` events for each task. The UI relies on `fetchRunDetail()` (triggered by the `flow.status_changed` event) to sync task statuses. If the flow event never arrives, task nodes stay visually stuck.

### Files to Modify

- `src/flowstate/server/websocket.py` — Add error handling around `_handle_control` cancel call; send error response to client on failure
- `src/flowstate/engine/executor.py` — Ensure `_emit` is reached even if intermediate cleanup steps fail (move emit before cleanup, or wrap cleanup in try/finally)
- `ui/src/hooks/useFlowRun.ts` — Possibly add a fallback: if cancel was requested but no status event arrives within N seconds, re-fetch run detail from REST API

### Key Implementation Details

**Step 1: Reproduce.** Start a flow, cancel it, observe WebSocket traffic in browser devtools to determine exactly where the event chain breaks.

**Step 2: Server-side hardening.** In `executor.cancel()`, restructure so the `flow.status_changed` event emission is guaranteed even if worktree cleanup or fork group updates fail. The DB status update (line 1685) and event emission (line 1686) are the critical operations; auxiliary cleanup can be best-effort.

**Step 3: Error feedback.** In `_handle_control`, wrap the cancel call in try/except and send an error message back to the client WebSocket if it fails.

**Step 4 (if needed): UI fallback.** After the UI sends the cancel action, start a short timeout. If no `flow.status_changed` event arrives within 3 seconds, call `fetchRunDetail()` to sync from the REST API.

### Edge Cases
- Cancel while flow is paused (different initial status)
- Cancel while no tasks are running (all pending)
- Cancel with multiple concurrent tasks
- Cancel during edge evaluation / judge invocation
- WebSocket disconnect during cancel

## Testing Strategy
- Unit test: verify `executor.cancel()` emits `FLOW_STATUS_CHANGED` event even when worktree cleanup fails
- Unit test: verify `_handle_control` sends error response on cancel failure
- Integration test: mock executor to raise during cancel, verify UI gets error feedback
- E2E: start a real flow, cancel it, verify the UI updates within 2 seconds

## E2E Verification Plan

### Reproduction Steps
1. Start server: `uv run flowstate serve`
2. Open browser to the UI
3. Start a flow run with at least one long-running task
4. Once a task is running, click the Cancel button and confirm
5. Expected: status badge shows `cancelled`, graph nodes update, Cancel button disappears
6. Actual: UI stays showing `running` status and active controls

### Verification Steps
1. Start server and run a flow
2. Cancel the flow via the UI
3. Verify: status badge shows `cancelled` within 2 seconds
4. Verify: running task nodes show `failed` status
5. Verify: "View Results" button appears
6. Verify: Cancel/Pause buttons are gone
7. Open browser devtools Network/WS tab, repeat — confirm `flow.status_changed` event arrives

## E2E Verification Log

### Reproduction
_[Agent fills this in]_

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
