# [ENGINE-038] Fix retry_task not waking paused executor loop

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section 6.2 — "Task Execution Lifecycle"

## Summary
`retry_task()` adds the new task execution to `_pending_tasks` but does not signal `_resume_event`. When `on_error=pause` is set (common default), a task failure pauses the flow and the main executor loop blocks on `await _resume_event.wait()`. Calling `retry_task` creates the new task but the loop never wakes up to execute it — the button appears to "do nothing."

Additionally, the WebSocket handler `_handle_task_control` has no try/except around the executor call. If `retry_task` raises a `ValueError` (e.g., task not failed), the exception propagates up and kills the WebSocket connection silently.

## Acceptance Criteria
- [ ] `retry_task()` calls `self._resume_event.set()` after adding to `_pending_tasks`, so the executor loop wakes up
- [ ] `retry_task()` sets `self._paused = False` and updates flow status to `running` (same as `resume()` does)
- [ ] `retry_task()` emits a `FLOW_STATUS_CHANGED` event (paused → running) so the UI updates
- [ ] WebSocket `_handle_task_control` wraps executor calls in try/except and sends error responses to the client
- [ ] After clicking Retry on a failed task in a paused flow, the task actually re-executes

## Technical Design

### Files to Modify

- `src/flowstate/engine/executor.py` — In `retry_task()`, after `self._pending_tasks.add(new_task_id)`, add:
  ```python
  if self._paused:
      self._paused = False
      self._db.update_flow_run_status(flow_run_id, "running")
      self._emit(FlowEvent(
          type=EventType.FLOW_STATUS_CHANGED,
          flow_run_id=flow_run_id,
          timestamp=_now_iso(),
          payload={"old_status": "paused", "new_status": "running", "reason": "Task retried"},
      ))
      self._resume_event.set()
  ```

- `src/flowstate/server/websocket.py` — In `_handle_task_control`, wrap the executor call:
  ```python
  try:
      if action == "retry_task":
          await executor.retry_task(flow_run_id, task_id)
      elif action == "skip_task":
          await executor.skip_task(flow_run_id, task_id)
  except (ValueError, RuntimeError) as e:
      logger.warning("Task control failed: %s", e)
      # Optionally send error back to client
  ```

### Edge Cases
- Retry on a non-paused flow (flow still running) → should still work, just adds to pending
- Retry when flow already completed → `retry_task` raises ValueError (task not failed), need error handling
- Skip also has the same resume issue — `skip_task` should also resume if paused

## Regression Risks
- Changing the paused state in `retry_task` must not conflict with explicit `resume()` calls
- The `skip_task` method likely has the same problem — check and fix simultaneously

## Testing Strategy
- Unit test: retry on paused flow resumes execution
- Unit test: retry emits FLOW_STATUS_CHANGED event
- Unit test: WS handler catches ValueError from retry_task
- `uv run pytest tests/engine/ tests/server/ && uv run ruff check . && uv run pyright`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
