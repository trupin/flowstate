# [ENGINE-078] Implement two-phase pause with `pausing` intermediate state

## Domain
engine

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: UI-071

## Spec References
- specs.md Section 6.1 — "Flow Run Lifecycle"
- specs.md Section 6.8 — "Concurrency Model" (paused flows release semaphore slots)

## Summary

The current `executor.pause()` method blocks until all running tasks finish before updating the DB and emitting the status change event. This means the UI gets no feedback until the current node completes, which can take minutes. Introduce a `pausing` intermediate state so that pause is acknowledged immediately, with the final `paused` state arriving once tasks actually complete.

This also enables "cancel pause" — if the user resumes while still in `pausing`, the engine clears the flag and returns to `running` without waiting for the pause to finalize.

## Acceptance Criteria
- [ ] `pausing` is a valid `flow_runs.status` value in the SQLite schema
- [ ] `executor.pause()` returns immediately after setting status to `pausing` and emitting `flow.status_changed` (running → pausing)
- [ ] When all running tasks finish while `_paused` is true, the engine transitions from `pausing` → `paused` and emits a second `flow.status_changed` event
- [ ] `executor.resume()` on a `pausing` flow: clears the pause flag, sets status back to `running`, emits event, and allows the main loop to continue launching new tasks
- [ ] `executor.resume()` on a `paused` flow: behaves as before (repopulates pending tasks, signals resume event)
- [ ] Existing tests continue to pass with updated pause semantics

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/schema.sql` — Add `'pausing'` to `flow_runs.status` CHECK constraint
- `src/flowstate/state/repository.py` — Add migration to ALTER the CHECK constraint for existing databases
- `src/flowstate/engine/executor.py` — Restructure `pause()` and `resume()`, add transition logic in main loop
- `tests/engine/test_executor.py` — Update/add tests for two-phase pause behavior

### Key Implementation Details

**Schema change** (schema.sql line 15-18):
```sql
status TEXT NOT NULL CHECK(status IN (
    'created', 'running', 'pausing', 'paused', 'completed',
    'failed', 'cancelled', 'budget_exceeded'
)),
```

Add a migration in `repository.py` that recreates the CHECK constraint for existing databases (SQLite doesn't support ALTER CHECK directly — use the existing migration pattern in the codebase).

**Executor `pause()` method** (executor.py ~line 1578):
1. Set `self._paused = True` (prevents new tasks from launching)
2. Update DB: `flow_run_id` status → `pausing`
3. Emit `flow.status_changed` event (running → pausing)
4. Return immediately — do NOT `asyncio.gather` on running tasks

**Main loop transition** (executor.py ~line 425-465):
After the task completion handling, add a check: if `self._paused` and `len(self._running_tasks) == 0` and DB status is `pausing`:
1. Update DB: status → `paused`
2. Emit `flow.status_changed` event (pausing → paused)
3. Continue to the pause-wait logic (block on `_resume_event`)

**Executor `resume()` method** (executor.py ~line 1606):
- Read current status from DB
- If `pausing`: clear `_paused`, set status → `running`, emit event, signal `_resume_event`. The main loop will continue launching tasks since `_paused` is now false.
- If `paused`: existing behavior (repopulate pending, set status → `running`, emit event, signal `_resume_event`)

**WebSocket fallback** (websocket.py ~line 248): Update the fallback target status for pause from `"paused"` to `"pausing"` since pause now returns before reaching `paused`.

### Edge Cases
- **Rapid pause/resume/pause**: Each call is idempotent. Pause sets flag + emits; resume clears + emits. The flag is the authoritative state; DB updates follow.
- **No running tasks at pause time**: If the user pauses when all tasks have completed but next ones haven't launched yet, `pause()` sets `pausing` and the main loop immediately transitions to `paused` since there are no running tasks.
- **Task fails while pausing**: The task failure handling should still work normally. If on_error=pause, the flow is already pausing, so it transitions to paused with the error.
- **Budget exceeded during pausing**: Budget guard already sets status separately. If flow is already `pausing`, the budget exceeded should take precedence and set `budget_exceeded`.
- **Cancel while pausing**: Cancel should still work — it sets `_cancelled` and the main loop breaks out regardless of pause state.

## Testing Strategy
- **Unit test**: `test_pause_returns_immediately` — verify that `pause()` sets status to `pausing` and returns without waiting for tasks
- **Unit test**: `test_pausing_transitions_to_paused` — verify that when a running task completes while `_paused=True`, the status transitions to `paused`
- **Unit test**: `test_resume_from_pausing_cancels_pause` — verify that resuming while `pausing` clears the flag and sets status to `running`
- **Unit test**: `test_resume_from_paused_starts_next_task` — verify existing resume behavior still works
- **Regression**: All existing pause/resume tests must pass (may need updated assertions for intermediate `pausing` state)

## E2E Verification Plan

### Verification Steps
1. Start server: `uv run flowstate serve`
2. Start a flow with a long-running node
3. Click Pause while node is running
4. Verify: API returns quickly, status shows `pausing`
5. Wait for node to complete → verify status transitions to `paused`
6. Start another flow, click Pause, then immediately click Resume
7. Verify: status goes running → pausing → running (pause was cancelled)

## E2E Verification Log
_Filled in by the implementing agent as proof-of-work._

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
