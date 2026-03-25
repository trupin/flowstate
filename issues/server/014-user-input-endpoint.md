# [SERVER-014] Message + interrupt API endpoints

## Domain
server

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-036, STATE-009
- Blocks: UI-037

## Spec References
- specs.md Section 10.2 — "REST API"
- specs.md Section 10.3 — "WebSocket Protocol"

## Summary
Add two REST endpoints: one for sending user messages to a task's agent, and one for interrupting a running agent. Messages are always accepted while a task is running or interrupted (queued for processing). Interrupt stops the agent's current turn so the user can interact. Both endpoints store state changes and emit WebSocket events.

## Acceptance Criteria
- [ ] `POST /api/runs/{run_id}/tasks/{task_execution_id}/message` accepts `{ "message": "..." }` and enqueues it
- [ ] Returns 200 on success with `{ "status": "queued" }` (running task) or `{ "status": "resumed" }` (interrupted task)
- [ ] Returns 404 if run/task not found
- [ ] Returns 409 if task is completed/failed/skipped (not running or interrupted)
- [ ] Returns 400 for empty message
- [ ] `POST /api/runs/{run_id}/tasks/{task_execution_id}/interrupt` stops the running agent
- [ ] Returns 200 on success with `{ "status": "interrupted" }`
- [ ] Returns 409 if task is not in `running` status
- [ ] Both endpoints store the user message as a `task.log` with `log_type: "user_input"` for log history
- [ ] Both endpoints emit WebSocket events so all clients see the update in real time
- [ ] `task.interrupted` WebSocket event emitted on interrupt

## Technical Design

### Files to Modify

- `src/flowstate/server/routes.py` — Add two new routes:

```python
@router.post("/runs/{run_id}/tasks/{task_execution_id}/message")
async def send_task_message(request, run_id, task_execution_id, body: UserMessageRequest):
    executor = _get_executor_or_error(request, run_id)
    db = _get_db(request)
    task = db.get_task_execution(task_execution_id)
    if not task or task.flow_run_id != run_id:
        raise FlowstateError(404, "Task not found")
    if task.status not in ("running", "interrupted"):
        raise FlowstateError(409, f"Task is {task.status}")

    await executor.send_message(task_execution_id, body.message)
    # Log the user input for history
    db.insert_task_log(task_execution_id, "user_input", json.dumps({"message": body.message}))
    # Broadcast to UI
    hub = request.app.state.ws_hub
    hub.broadcast_event({...task.log with log_type user_input...})

    status = "resumed" if task.status == "interrupted" else "queued"
    return {"status": status}

@router.post("/runs/{run_id}/tasks/{task_execution_id}/interrupt")
async def interrupt_task(request, run_id, task_execution_id):
    executor = _get_executor_or_error(request, run_id)
    db = _get_db(request)
    task = db.get_task_execution(task_execution_id)
    if not task or task.flow_run_id != run_id:
        raise FlowstateError(404, "Task not found")
    if task.status != "running":
        raise FlowstateError(409, f"Task is {task.status}, not running")

    await executor.interrupt(task_execution_id)
    return {"status": "interrupted"}
```

- `src/flowstate/server/models.py` — Add `UserMessageRequest` model: `message: str` with `min_length=1`.

### Edge Cases
- Task completes between request validation and executor call → executor raises RuntimeError → return 500
- Executor not in memory (server restarted) → 404
- Rapid interrupt + message → interrupt completes, message resumes
- WebSocket replay: user input messages stored as task.log, so they replay on reconnect

## Regression Risks
- New endpoints are additive — no existing routes modified
- `insert_task_log` with `user_input` requires STATE-009
- `task.interrupted` WebSocket event requires ENGINE-036 event type

## Testing Strategy
- Unit test with TestClient: mock executor, verify `send_message` / `interrupt` called
- Unit test: 404 for missing run/task
- Unit test: 409 for completed task (message) and non-running task (interrupt)
- Unit test: 400 for empty message
- Unit test: correct response status ("queued" vs "resumed")
- `uv run pytest tests/server/ && uv run ruff check . && uv run pyright`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
