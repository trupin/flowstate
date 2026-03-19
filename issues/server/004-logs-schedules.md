# [SERVER-004] REST API — Task Logs + Schedules

## Domain
server

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-003, ENGINE-010
- Blocks: none

## Spec References
- specs.md Section 10.2 — "REST API" (GET /api/runs/:id/tasks/:tid/logs, GET /api/schedules, POST schedule pause/resume/trigger)
- specs.md Section 10.7 — "Sidebar" (schedules section)
- agents/04-server.md — "REST API" (task logs, schedule management)

## Summary
Implement the remaining REST API endpoints: paginated task log retrieval and schedule management (list, pause, resume, trigger). Task logs are streamed output lines from Claude Code subprocesses, stored in the `task_logs` database table. Schedules are recurring flow executions defined by cron expressions in the DSL. These are lower priority (P1) because the core run management (SERVER-003) and WebSocket (SERVER-005) paths work without them, but they are needed for the full UI experience (sidebar schedule display, log viewer pagination).

## Acceptance Criteria
- [ ] `GET /api/runs/:id/tasks/:tid/logs` returns paginated task logs:
  - Each log entry: `{timestamp, log_type, content}`
  - Supports `?after=<iso8601_timestamp>` query param to fetch logs after a given time
  - Supports `?limit=<int>` query param (default 1000, max 10000)
  - Returns logs sorted by timestamp ascending
  - Returns 404 if run or task not found
- [ ] `GET /api/schedules` returns all flow schedules:
  - Each schedule: `{id, flow_name, cron_expression, status, next_run_at, last_run_at, overlap_policy}`
  - Status is "active" or "paused"
- [ ] `POST /api/schedules/:id/pause` pauses a schedule:
  - Returns 200 with `{"status": "paused"}`
  - Returns 404 if schedule not found
  - Returns 409 if already paused
- [ ] `POST /api/schedules/:id/resume` resumes a paused schedule:
  - Returns 200 with `{"status": "active"}`
  - Returns 404 if schedule not found
  - Returns 409 if already active
- [ ] `POST /api/schedules/:id/trigger` manually triggers a scheduled flow:
  - Creates a new run as if the cron triggered it
  - Returns 202 with `{"flow_run_id": "<uuid>"}`
  - Returns 404 if schedule not found
  - Respects the overlap policy (skip if another run is active and policy is "skip")
- [ ] All route handlers are `async def`
- [ ] All tests pass: `uv run pytest tests/server/test_logs_schedules.py`

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/routes.py` — add log and schedule routes to existing router
- `src/flowstate/server/models.py` — add Pydantic response models (or extend if already created in SERVER-003)
- `tests/server/test_logs_schedules.py` — all tests

### Key Implementation Details

#### Pydantic Models

```python
class TaskLogEntry(BaseModel):
    timestamp: str  # ISO 8601
    log_type: str   # "assistant", "tool_use", "tool_result", "error", "result"
    content: str


class TaskLogsResponse(BaseModel):
    task_execution_id: str
    logs: list[TaskLogEntry]
    has_more: bool  # True if there are more logs after the last returned entry


class ScheduleResponse(BaseModel):
    id: str
    flow_name: str
    cron_expression: str
    status: str  # "active" or "paused"
    next_run_at: str | None  # ISO 8601, None if paused
    last_run_at: str | None  # ISO 8601, None if never run
    overlap_policy: str  # "skip", "queue", "parallel"
```

#### Task Log Route

```python
@router.get("/runs/{run_id}/tasks/{task_id}/logs")
async def get_task_logs(
    request: Request,
    run_id: str,
    task_id: str,
    after: str | None = None,
    limit: int = 1000,
) -> TaskLogsResponse:
    db = request.app.state.db

    # Validate run exists
    run = db.get_run(run_id)
    if not run:
        raise FlowstateError(f"Run '{run_id}' not found", status_code=404)

    # Validate task exists within this run
    task = db.get_task_execution(task_id)
    if not task or task.flow_run_id != run_id:
        raise FlowstateError(
            f"Task '{task_id}' not found in run '{run_id}'",
            status_code=404,
        )

    # Clamp limit
    limit = min(limit, 10000)

    # Fetch logs with pagination
    logs = db.get_task_logs(
        task_execution_id=task_id,
        after_timestamp=after,
        limit=limit + 1,  # fetch one extra to determine has_more
    )

    has_more = len(logs) > limit
    if has_more:
        logs = logs[:limit]

    return TaskLogsResponse(
        task_execution_id=task_id,
        logs=[
            TaskLogEntry(
                timestamp=log.timestamp.isoformat(),
                log_type=log.log_type,
                content=log.content,
            )
            for log in logs
        ],
        has_more=has_more,
    )
```

The `after` parameter enables cursor-based pagination: the client passes the timestamp of the last log entry it received, and the server returns all entries after that timestamp. This is efficient for both REST polling and WebSocket reconnection replay.

#### Schedule Routes

```python
@router.get("/schedules")
async def list_schedules(request: Request) -> list[ScheduleResponse]:
    db = request.app.state.db
    schedules = db.list_schedules()
    return [
        ScheduleResponse(
            id=s.id,
            flow_name=s.flow_name,
            cron_expression=s.cron_expression,
            status=s.status,
            next_run_at=s.next_run_at.isoformat() if s.next_run_at else None,
            last_run_at=s.last_run_at.isoformat() if s.last_run_at else None,
            overlap_policy=s.overlap_policy,
        )
        for s in schedules
    ]


@router.post("/schedules/{schedule_id}/pause")
async def pause_schedule(request: Request, schedule_id: str) -> dict:
    db = request.app.state.db
    schedule = db.get_schedule(schedule_id)
    if not schedule:
        raise FlowstateError(f"Schedule '{schedule_id}' not found", status_code=404)
    if schedule.status == "paused":
        raise FlowstateError("Schedule is already paused", status_code=409)
    db.update_schedule_status(schedule_id, "paused")
    return {"status": "paused"}


@router.post("/schedules/{schedule_id}/resume")
async def resume_schedule(request: Request, schedule_id: str) -> dict:
    db = request.app.state.db
    schedule = db.get_schedule(schedule_id)
    if not schedule:
        raise FlowstateError(f"Schedule '{schedule_id}' not found", status_code=404)
    if schedule.status == "active":
        raise FlowstateError("Schedule is already active", status_code=409)
    db.update_schedule_status(schedule_id, "active")
    return {"status": "active"}


@router.post("/schedules/{schedule_id}/trigger", status_code=202)
async def trigger_schedule(request: Request, schedule_id: str) -> dict:
    db = request.app.state.db
    schedule = db.get_schedule(schedule_id)
    if not schedule:
        raise FlowstateError(f"Schedule '{schedule_id}' not found", status_code=404)

    # Check overlap policy
    if schedule.overlap_policy == "skip":
        active_runs = db.list_runs(flow_name=schedule.flow_name, status="running")
        if active_runs:
            raise FlowstateError(
                "Cannot trigger: another run is active and overlap policy is 'skip'",
                status_code=409,
            )

    # Start a new run (reuse logic from start_run endpoint)
    run_manager: RunManager = request.app.state.run_manager
    registry: FlowRegistry = request.app.state.flow_registry
    flow = registry.get_flow(schedule.flow_id)
    if not flow or flow.status == "error":
        raise FlowstateError("Scheduled flow is not valid", status_code=400)

    run_id = str(uuid.uuid4())
    # ... create executor and start run (same as POST /api/flows/:id/runs) ...

    return {"flow_run_id": run_id}
```

### Edge Cases
- `limit=0`: treat as the default (1000), do not return empty results.
- `limit` exceeding 10000: clamp to 10000 to prevent memory issues.
- `after` timestamp with no matching logs: return empty list with `has_more=false`.
- `after` timestamp in the future: return empty list.
- Task with no logs yet (just started): return empty list.
- Schedule trigger while flow file has errors: return 400. The schedule exists in the DB but the flow source may have been broken since it was defined.
- Schedule trigger with `overlap_policy="queue"`: this is handled by the engine's scheduler (ENGINE-011). The REST endpoint just starts the run; the engine decides whether to queue it.
- Schedule trigger with `overlap_policy="parallel"`: always allow, create a new run.

## Testing Strategy

Create `tests/server/test_logs_schedules.py`. Mock the DB layer for all tests.

1. **test_get_task_logs** — Mock DB to return 5 log entries. Send `GET /api/runs/:id/tasks/:tid/logs`. Verify 200 with 5 entries, `has_more=false`.

2. **test_get_task_logs_pagination** — Mock DB to return 1001 entries (limit+1). Verify response has 1000 entries and `has_more=true`.

3. **test_get_task_logs_with_after** — Send `?after=2024-01-01T00:00:00Z`. Verify the DB query receives the after parameter.

4. **test_get_task_logs_custom_limit** — Send `?limit=50`. Verify only 50 entries returned.

5. **test_get_task_logs_limit_clamped** — Send `?limit=99999`. Verify the effective limit is 10000.

6. **test_get_task_logs_run_not_found** — Verify 404 when run doesn't exist.

7. **test_get_task_logs_task_not_found** — Verify 404 when task doesn't exist or belongs to a different run.

8. **test_list_schedules** — Mock DB with 2 schedules. Verify 200 with correct structure.

9. **test_list_schedules_empty** — No schedules. Verify 200 with empty list.

10. **test_pause_schedule** — Active schedule. Verify 200 and DB update called.

11. **test_pause_already_paused** — Verify 409.

12. **test_resume_schedule** — Paused schedule. Verify 200 and DB update called.

13. **test_resume_already_active** — Verify 409.

14. **test_trigger_schedule** — Valid schedule and flow. Verify 202 with `flow_run_id`.

15. **test_trigger_schedule_skip_overlap** — Schedule has `overlap_policy="skip"` and a run is active. Verify 409.

16. **test_trigger_schedule_not_found** — Verify 404.
