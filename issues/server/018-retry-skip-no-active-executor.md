# [SERVER-018] Handle retry/skip when no active executor exists

## Domain
server

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-053
- Blocks: —

## Spec References
- specs.md Section 10.1 — REST API
- specs.md Section 10.2 — WebSocket protocol

## Summary
When a flow is in a terminal state (cancelled, failed, completed), there is no active executor in the RunManager. The REST retry/skip endpoints return a 409 error, and the WebSocket handler silently returns with no feedback to the UI. The server needs to handle this case by reconstructing an executor and using `restart_from_task()` (from ENGINE-053). The WebSocket handler should also send error responses instead of silently failing.

## Acceptance Criteria
- [ ] REST retry/skip endpoints reconstruct an executor for terminal flows and start execution
- [ ] WebSocket retry_task/skip_task actions reconstruct an executor for terminal flows
- [ ] WebSocket handler sends error responses back to the client when actions fail (no more silent failures)
- [ ] The new executor is registered with RunManager so subsequent operations work
- [ ] Flow status transitions from terminal → running when retry/skip succeeds

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/routes.py` — update retry/skip REST endpoints to handle missing executor
- `src/flowstate/server/websocket.py` — update `_handle_task_control()` to handle missing executor + send error responses
- `src/flowstate/server/run_manager.py` — possibly add a helper to re-create and register an executor

### Key Implementation Details

**REST endpoints** (`routes.py`):

In `retry_task()` and `skip_task()`, when `_get_executor_or_error()` would raise 409:

```python
@router.post("/runs/{run_id}/tasks/{task_id}/retry")
async def retry_task(request: Request, run_id: str, task_id: str) -> dict[str, str]:
    run_manager = _get_run_manager(request)
    executor = run_manager.get_executor(run_id)

    if executor is None:
        # No active executor — try to reconstruct for terminal flows
        db = _get_db(request)
        run = db.get_flow_run(run_id)
        if not run:
            raise FlowstateError(f"Run '{run_id}' not found", status_code=404)
        if run.status not in ("cancelled", "failed"):
            raise FlowstateError(
                f"Run '{run_id}' is not active (status: {run.status})",
                status_code=409,
            )
        # Re-create executor and restart from task
        executor, coro = _create_executor_for_restart(request, run, task_id, "retry")
        await run_manager.start_run(run_id, executor, coro)
        return {"status": "running"}

    # Existing flow: use active executor
    ...
```

A helper `_create_executor_for_restart()` would:
1. Parse the flow from `run.flow_name` using the file watcher / DSL parser
2. Create a `FlowExecutor` with the same DB and harness manager
3. Call `executor.restart_from_task(flow, run_id, task_id, "retry")`
4. Return the executor and its execute coroutine

**WebSocket handler** (`websocket.py`):

In `_handle_task_control()`, send error responses instead of silently returning:

```python
async def _handle_task_control(self, action, flow_run_id, task_id):
    if not self._run_manager or not task_id:
        return
    executor = self._run_manager.get_executor(flow_run_id)
    if not executor:
        # Try to reconstruct for terminal flows (similar to REST)
        # OR send error back to client:
        await self._broadcast({
            "type": "error",
            "payload": {"message": f"No active executor for run {flow_run_id}"},
        })
        return
```

### Edge Cases
- Race condition: two retry requests arrive simultaneously for the same cancelled run — only one should create an executor
- The flow file may have been deleted since the run — handle parse errors gracefully
- The flow file may have changed — the re-parsed AST might not match the original run's node structure

## Testing Strategy
- Unit test: mock RunManager with no executor, call retry endpoint, verify executor is created and registered
- Unit test: verify WebSocket handler sends error response when no executor and reconstruction fails
- Integration test: cancel a flow via API, retry a task, verify the flow resumes

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
