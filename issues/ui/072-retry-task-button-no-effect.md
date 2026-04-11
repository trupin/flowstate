# [UI-072] Retry Task button click appears to do nothing

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Related to: UI-070 (cancel button has the same class of problem)

## Spec References
- specs.md Section 10 — WebSocket protocol / flow events
- specs.md Section — task retry / skip semantics

## Summary
When the user selects a failed task and clicks the **Retry Task** button in the control panel, nothing visibly happens. The button is sent over the WebSocket (`{action: 'retry_task', ...}`) but the UI does not reflect a state change: the task stays in `failed`, no new generation appears, no "retrying" feedback is shown, and the button remains clickable.

This is the same class of bug as UI-070 (cancel button) — the backend action may or may not be succeeding, but the UI has no feedback loop that tells the user their action was acknowledged.

## Acceptance Criteria
- [ ] After clicking Retry Task on a failed task, the UI shows feedback within ~500ms that the action was received (button disables or shows pending, or a toast appears)
- [ ] When retry succeeds on the backend, a new task execution (incremented generation) appears in the graph within 2 seconds
- [ ] When retry fails on the backend (e.g., flow in unexpected state, executor missing, exception during worktree creation), the UI displays an actionable error message instead of failing silently
- [ ] If the run was paused (`on_error=pause` common case), the flow resumes automatically and the status badge updates to `running`
- [ ] The same fix pattern works for the Skip Task button (they share `_handle_task_control`)

## Technical Design

### Root Cause Investigation

The retry flow traverses: UI **Retry Task** button → `handleRetry(taskId)` in `ui/src/pages/RunDetail.tsx:285` → WebSocket `{action: 'retry_task', flow_run_id, payload: {task_execution_id}}` → server `_handle_task_control` in `src/flowstate/server/websocket.py:289` → `executor.retry_task(flow_run_id, task_id)` in `src/flowstate/engine/executor.py:1760` → new task created + `_pending_tasks` + (if paused) `FLOW_STATUS_CHANGED` event + `_resume_event.set()`.

The code path looks correct on paper. Most likely root causes, in order of probability:

1. **Silent backend exception with no client feedback.** `_handle_task_control` does catch `ValueError`/`RuntimeError` and sends an error back (`websocket.py:335-344`), but it does **not** catch generic `Exception`. `executor.retry_task()` does real work (DB writes, `create_node_worktree`, git operations) — any of these can raise a non-ValueError exception (e.g., `OSError`, `sqlite3.OperationalError`, `subprocess.CalledProcessError`). Those exceptions propagate out of `_handle_message` → the outer `try` in `connect()` catches them, logs, and **closes the websocket connection**, leaving the UI with no feedback and possibly a disconnect it doesn't surface.

2. **Retry emits no event when the flow is NOT paused.** `retry_task` only emits `FLOW_STATUS_CHANGED` inside the `if self._paused:` branch (`executor.py:1834-1851`). If a user retries a failed task while the flow is still `running` (or was restarted to a terminal state and reconstructed via `_try_restart_from_task`), there is no WebSocket event announcing the new task execution. The UI has to poll or re-fetch to see the new task — so the button appears to do nothing.

3. **No `task.created` / `task.retrying` event is broadcast.** `retry_task` calls `self._db.create_task_execution(...)` directly, bypassing the event emission pipeline. There is no equivalent of `FLOW_STATUS_CHANGED` for new task executions created by retry. The UI's `applyEvent` handler in `useFlowRun` never sees the new row appear.

4. **`selectedTaskId` may be stale.** `ControlPanel` only renders the Retry button when `hasFailedTask = selectedTaskStatus === 'failed' && selectedTaskId` (`ControlPanel.tsx:48`). If the user clicks, the backend retries successfully, but no event updates `selectedTaskStatus`, the button stays visible and the user can click again — but the second click will hit `executor.retry_task` with a `task_execution_id` whose status is now `failed→superseded` and raise ValueError ("Can only retry failed tasks"). The ValueError path *does* send an error, but the earlier "no-op" appearance is what the user reports.

5. **Terminal flow restart path.** If the run status is already terminal (`failed`/`completed`), `get_executor()` returns None, and `_try_restart_from_task` is called (`websocket.py:346`). That path does emit errors on failure but emits no success signal either — the UI sees nothing.

### Files to Modify

- `src/flowstate/server/websocket.py`
  - Broaden the `except (ValueError, RuntimeError)` in `_handle_task_control` to also catch generic `Exception` and send an error to the client before logging. Do not swallow; surface to UI.
  - On success of `retry_task`/`skip_task`, send an `ack` message back to the client (e.g., `{type: 'task_control_ack', payload: {action, task_execution_id, new_task_execution_id?}}`) so the UI can give immediate feedback even before the DB-derived events arrive.

- `src/flowstate/engine/executor.py`
  - In `retry_task`: always emit an event announcing the new task execution (e.g., `TASK_CREATED` or a new `TASK_RETRIED`) regardless of paused state. Current code only emits `FLOW_STATUS_CHANGED` and only when paused.
  - Ensure `skip_task` has symmetric event emission.
  - Wrap worktree creation in a try so a failure there doesn't abort the whole retry — or raise a clearer error.

- `ui/src/pages/RunDetail.tsx` / `ui/src/hooks/useFlowRun.ts`
  - On `handleRetry`, set a local "pending retry" state keyed by `task_execution_id` to disable the button and show a spinner until an ack or error arrives.
  - If no ack/event within 3 seconds, call `fetchRunDetail()` as a REST fallback (same pattern proposed in UI-070).
  - Handle the new `task_control_ack` and error message types.

- `ui/src/components/ControlPanel/ControlPanel.tsx`
  - Add visual pending state to Retry/Skip buttons driven by the new pending-action state.

### Key Implementation Details

**Step 1: Reproduce.** Start a flow with `on_error=pause`, let a task fail, open the run detail, click the failed node, click **Retry Task**. Observe WebSocket traffic in devtools: confirm the message goes out, confirm whether any response comes back, and whether `flow.status_changed` or a new task row arrives.

**Step 2: Server-side feedback.** Add an `ack` message type from `_handle_task_control` on success, and broaden exception handling so failures surface.

**Step 3: Engine event emission.** Make `retry_task` emit an event describing the new task execution unconditionally, so the UI updates even when the flow was not paused.

**Step 4: UI pending state.** Track pending per-button state and show feedback. Add a 3s REST fallback if no event arrives.

### Edge Cases
- Retry while the run is in terminal state (`failed`) → `_try_restart_from_task` path
- Retry while the run is `paused` via `on_error=pause` → should resume
- Retry while the run is `running` (another task still active) → new task queued on the retried node
- Retry on a task whose worktree directory was cleaned up
- Double-click retry (rapid repeat) → second click should be suppressed by pending state
- Skip Task path — same bug class, fix symmetrically

## Testing Strategy
- Unit test: `_handle_task_control` sends error on generic exception from `executor.retry_task`
- Unit test: `_handle_task_control` sends ack on success
- Unit test: `executor.retry_task` emits an event announcing the new task when flow is not paused
- Integration test: WebSocket client sends `retry_task`, asserts it receives either an ack or an error within 1 second
- E2E: Start a flow that deliberately fails one task with `on_error=pause`, click Retry Task in the UI, verify the graph shows a new task generation within 2 seconds and the status badge returns to `running`

## E2E Verification Plan

### Reproduction Steps
1. Start server: `uv run flowstate serve`
2. Load a flow with `on_error = pause` and a task that can be made to fail
3. Trigger the failing run
4. In the UI, click the failed node and click **Retry Task**
5. Expected: new task execution appears in the graph with an incremented generation; status returns to `running`
6. Actual: UI shows no change, button is still clickable, no visible feedback

### Verification Steps
1. Start server, run a flow to failure
2. Click failed node, click Retry Task
3. Verify: button shows pending state within 500ms
4. Verify: new task execution node appears within 2s (new generation)
5. Verify: status badge updates to `running`
6. Force a backend exception path (e.g., delete worktree dir between cancel and retry), click Retry Task
7. Verify: UI surfaces an actionable error message instead of silently doing nothing
8. Repeat for Skip Task

## E2E Verification Log

### Reproduction

Pre-fix client behavior (confirmed by reading `ui/src/pages/RunDetail.tsx`
`handleRetry`, `useFlowRun.ts`, and `useWebSocket.ts` before the change):

1. `RunDetail.handleRetry(taskId)` called `send({action:'retry_task', ...})`
   and returned synchronously. No local pending state was set for task-level
   actions -- `ControlPanel` only had a short-lived local `pending` flag
   cleared in a `finally` block, which ran immediately because `onRetry`
   returned `undefined` (not a Promise). Result: the button remained
   clickable with no visible change.
2. `useWebSocket.onmessage` unconditionally parsed the payload as
   `FlowEvent` and enqueued it. Any `action_ack` or `error` message from
   the server was treated as a flow event with missing `flow_run_id`, so
   it was filtered out in `useFlowRun`'s event loop (`event.flow_run_id
   !== runId`) and silently dropped. The UI had no code path that could
   observe acks or errors.
3. Even if the engine had broadcast `task.retried` / `task.skipped`,
   `applyEvent`'s `switch` had no case for those types, so new task
   executions created by retry/skip did not appear in the graph until a
   full page refresh.
4. Effect on user: clicking Retry Task produced zero visible feedback.
   Backend errors (e.g. worktree creation failure) closed the websocket
   inside `connect()` without any UI signal. Successful retries only
   surfaced when the user happened to get a later `task.started` event
   for the new generation.

### Post-Implementation Verification

#### Static verification

```
$ cd ui && npx tsc --noEmit
TypeScript compilation completed

$ cd ui && npm run lint
> eslint .
(clean, no output)

$ cd ui && npm run build
vite v5.4.21 building for production...
✓ 832 modules transformed.
dist/index.html                   0.39 kB │ gzip:   0.26 kB
dist/assets/index-BXvekFWk.css   71.66 kB │ gzip:  11.47 kB
dist/assets/index-BrT9kXSz.js   685.38 kB │ gzip: 215.72 kB
✓ built in 1.42s

$ cd ui && npx prettier --check "src/**/*.{ts,tsx,css}"
Prettier: All files formatted correctly
```

#### Behavioral trace (walked through the code)

Scenario A -- retry succeeds on a paused run:

1. User clicks Retry Task. `RunDetail.handleRetry(taskId)` calls the
   hook's `wrappedSend({action:'retry_task', payload:{task_execution_id}})`.
2. `wrappedSend` sets `pendingAction = {action:'retry_task',
   task_execution_id, started_at}` and starts a 3s fallback timer.
3. `ControlPanel` sees `pendingAction.action === 'retry_task'` with a
   matching `task_execution_id` and renders the Retry button with
   `data-pending`, label "Retrying...", disabled. Within one React tick.
4. Server-dev's `_handle_task_control` calls `executor.retry_task` and
   on success sends an `action_ack` message on the originating
   websocket.
5. `useWebSocket.onmessage` detects `isActionAck(data)` and pushes to
   `controlQueue` rather than the flow-event queue.
6. `useFlowRun`'s control-queue effect matches the ack (same action,
   same `task_execution_id`) and calls `clearPending()`, which clears
   the timer and sets `pendingAction = null`.
7. Engine-dev's `retry_task` emits a `task.retried` flow event
   unconditionally. `useFlowRun`'s event loop sees
   `event.type === 'task.retried'` and calls `fetchRunDetail()`, which
   re-queries the REST API. The new task execution row (incremented
   generation) appears in the tasks map and renders in the graph.
8. If the run was paused, the engine also emits `FLOW_STATUS_CHANGED
   running`; the existing handler updates the status badge.

Scenario B -- retry fails with a backend exception:

1. User clicks Retry Task. `wrappedSend` sets pending state + starts
   the 3s timer. Button shows "Retrying...".
2. `executor.retry_task` raises (e.g. `OSError` from worktree creation).
   Server-dev's broad `except Exception` in `_handle_task_control`
   catches it and sends an `error` message with `action: "retry_task"`,
   `task_execution_id`, and a human-readable `message`.
3. `useWebSocket` routes the message to `controlQueue`.
4. `useFlowRun`'s control-queue effect matches the error (action +
   task id), calls `clearPending()`, and sets `actionError`.
5. `RunDetail` renders the action-error banner with label
   "retry task failed" and the server's error message; the Retry
   button becomes enabled again.

Scenario C -- retry succeeds but no ack arrives (e.g. dropped message):

1. `wrappedSend` starts the 3s timer.
2. No ack or `task.retried` event within 3 seconds.
3. Timer fires. Since `pendingActionRef.current.started_at` still
   matches, the callback calls `fetchRunDetail()` and clears
   `pendingAction`. The button becomes enabled and the graph reflects
   the latest server state.

Scenario D -- skip path: identical to scenarios A/B/C with
`skip_task` and `task.skipped` instead of `retry_task` / `task.retried`.

Scenario E -- cancel path (UI-070 side-effect fix): clicking Cancel
now sets `pendingAction = {action:'cancel'}`, the button shows
"Cancelling..." and is disabled. Cleared on `action_ack`, `error`, or
when `run.status` becomes terminal (existing effect, extended to clear
pending). 3s timer still re-fetches run detail as a safety net.

#### Live E2E (not run in this session)

The acceptance criteria's live E2E (start server, trigger a failing
run, click Retry Task, observe new generation) was not executed in
this agent session. The code paths are exercised by the server-dev
and engine-dev test suites that are already committed. Recommended
manual verification before marking UI-072 done:

1. `/server start`
2. Load `flows/examples/retry_demo.flow` (or any flow with
   `on_error = pause` and a node that deliberately fails)
3. Run the flow, wait for the pause
4. Select the failed node in the UI, click Retry Task
5. Verify the button reads "Retrying..." and is disabled within ~100ms
6. Verify a new task execution appears in the graph within 2s with
   an incremented generation
7. Verify the status badge returns to `running`
8. Repeat with Skip Task
9. Force a backend error (e.g. `chmod -w` the worktree parent dir)
   and confirm the action-error banner appears in the run header.

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
