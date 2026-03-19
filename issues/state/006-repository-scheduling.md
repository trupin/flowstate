# [STATE-006] Repository (Scheduling + Recovery)

## Domain
state

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: STATE-002
- Blocks: ENGINE-010, ENGINE-011

## Spec References
- specs.md Section 8.1 — "SQLite Schema" (flow_schedules table)
- specs.md Section 8.3 — "Recovery Protocol"
- agents/02-state.md — "FlowstateDB" interface (recovery methods)

## Summary
Add flow scheduling CRUD, recovery queries, and waiting-task queries to the FlowstateDB class. Flow schedules support recurring execution via cron expressions. Recovery methods detect orphaned flow runs and tasks after a process restart. Waiting-task queries find tasks whose `wait_until` timestamp has passed, enabling delayed edge execution. These are P1 features needed by the engine's scheduling and recovery subsystems.

## Acceptance Criteria
- [ ] `create_flow_schedule` generates UUID, returns ID
- [ ] `get_flow_schedule` returns `FlowScheduleRow | None`
- [ ] `list_flow_schedules` returns all schedules, optionally filtered by flow_definition_id
- [ ] `update_flow_schedule` updates mutable fields (cron_expression, on_overlap, enabled, last_triggered_at, next_trigger_at)
- [ ] `delete_flow_schedule` removes a schedule by ID
- [ ] `get_due_schedules` returns enabled schedules where `next_trigger_at <= now`
- [ ] `get_running_flow_runs` returns flow runs with status `'running'` (for recovery)
- [ ] `get_running_tasks` returns task executions with status `'running'` for a given flow_run_id (for recovery)
- [ ] `get_waiting_tasks` returns task executions with status `'waiting'` and `wait_until <= now` for a given flow_run_id
- [ ] Foreign key constraints enforced: schedule must reference valid flow_definition
- [ ] `uv run pytest tests/state/test_repository.py` passes (scheduling + recovery tests)
- [ ] `uv run ruff check src/flowstate/state/repository.py` passes
- [ ] `uv run pyright src/flowstate/state/repository.py` passes

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/repository.py` — add scheduling + recovery methods to FlowstateDB
- `tests/state/test_repository.py` — add scheduling and recovery tests

### Key Implementation Details

#### Flow Schedule Methods

```python
def create_flow_schedule(
    self,
    flow_definition_id: str,
    cron_expression: str,
    on_overlap: str = "skip",
    next_trigger_at: str | None = None,
) -> str:
    id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    self._execute(
        """INSERT INTO flow_schedules
           (id, flow_definition_id, cron_expression, on_overlap, enabled, next_trigger_at, created_at)
           VALUES (?, ?, ?, ?, 1, ?, ?)""",
        (id, flow_definition_id, cron_expression, on_overlap, next_trigger_at, now),
    )
    self._commit()
    return id

def get_flow_schedule(self, id: str) -> FlowScheduleRow | None:
    row = self._fetchone("SELECT * FROM flow_schedules WHERE id = ?", (id,))
    return FlowScheduleRow(**dict(row)) if row else None

def list_flow_schedules(
    self, flow_definition_id: str | None = None
) -> list[FlowScheduleRow]:
    if flow_definition_id:
        rows = self._fetchall(
            "SELECT * FROM flow_schedules WHERE flow_definition_id = ? ORDER BY created_at",
            (flow_definition_id,),
        )
    else:
        rows = self._fetchall(
            "SELECT * FROM flow_schedules ORDER BY created_at"
        )
    return [FlowScheduleRow(**dict(r)) for r in rows]

def update_flow_schedule(self, id: str, **kwargs) -> None:
    """Update mutable schedule fields.

    Accepted kwargs: cron_expression, on_overlap, enabled,
    last_triggered_at, next_trigger_at
    """
    allowed = {"cron_expression", "on_overlap", "enabled", "last_triggered_at", "next_trigger_at"}
    updates = {}
    for key, value in kwargs.items():
        if key not in allowed:
            raise ValueError(f"Unknown schedule field: {key}")
        updates[key] = value
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    self._execute(
        f"UPDATE flow_schedules SET {set_clause} WHERE id = ?",
        tuple(values),
    )
    self._commit()

def delete_flow_schedule(self, id: str) -> None:
    self._execute("DELETE FROM flow_schedules WHERE id = ?", (id,))
    self._commit()

def get_due_schedules(self, now: str | None = None) -> list[FlowScheduleRow]:
    """Get enabled schedules whose next_trigger_at is at or before the given time.

    Args:
        now: ISO 8601 timestamp. Defaults to current UTC time.
    """
    if now is None:
        now = datetime.now(timezone.utc).isoformat()
    rows = self._fetchall(
        """SELECT * FROM flow_schedules
           WHERE enabled = 1 AND next_trigger_at IS NOT NULL AND next_trigger_at <= ?
           ORDER BY next_trigger_at ASC""",
        (now,),
    )
    return [FlowScheduleRow(**dict(r)) for r in rows]
```

#### Recovery Methods

These methods support the recovery protocol described in specs.md Section 8.3. On process restart, the engine:
1. Calls `get_running_flow_runs()` to find flows that were running when the process died
2. For each, calls `get_running_tasks(flow_run_id)` to find orphaned tasks
3. Marks orphaned tasks as `'failed'`
4. Sets the flow to `'paused'` with an error message

```python
def get_running_flow_runs(self) -> list[FlowRunRow]:
    """Find flow runs with status 'running'. Used for crash recovery."""
    rows = self._fetchall(
        "SELECT * FROM flow_runs WHERE status = 'running' ORDER BY created_at"
    )
    return [FlowRunRow(**dict(r)) for r in rows]

def get_running_tasks(self, flow_run_id: str) -> list[TaskExecutionRow]:
    """Find task executions with status 'running' for a given flow run. Used for crash recovery."""
    rows = self._fetchall(
        "SELECT * FROM task_executions WHERE flow_run_id = ? AND status = 'running' ORDER BY created_at",
        (flow_run_id,),
    )
    return [TaskExecutionRow(**dict(r)) for r in rows]
```

#### Waiting Task Methods

Delayed edges set `wait_until` on a task and status to `'waiting'`. The engine periodically checks for tasks ready to execute:

```python
def get_waiting_tasks(self, flow_run_id: str, now: str | None = None) -> list[TaskExecutionRow]:
    """Find tasks with status 'waiting' whose wait_until has passed.

    Args:
        flow_run_id: The flow run to check.
        now: ISO 8601 timestamp. Defaults to current UTC time.
    """
    if now is None:
        now = datetime.now(timezone.utc).isoformat()
    rows = self._fetchall(
        """SELECT * FROM task_executions
           WHERE flow_run_id = ? AND status = 'waiting' AND wait_until <= ?
           ORDER BY wait_until ASC""",
        (flow_run_id, now),
    )
    return [TaskExecutionRow(**dict(r)) for r in rows]
```

**Note on timestamp comparison:** SQLite compares TEXT timestamps lexicographically. ISO 8601 format (`YYYY-MM-DDTHH:MM:SS.ffffff`) is lexicographically sortable, so `<=` comparisons work correctly. Ensure all timestamps use the same format (no timezone offsets — always UTC, use `Z` suffix or no suffix consistently). Using `datetime.now(timezone.utc).isoformat()` produces format like `2025-01-15T10:30:00+00:00`. For consistent comparison, consider stripping the `+00:00` or using `datetime.utcnow().isoformat()` (deprecated but produces bare timestamps). The recommended approach is to use `datetime.now(timezone.utc).isoformat()` consistently across all methods (established in STATE-003).

### Edge Cases
- `get_due_schedules` with no schedules returns empty list
- `get_due_schedules` with `now` in the distant past returns empty list (no schedules are due yet)
- Schedule with `next_trigger_at = None` is never returned by `get_due_schedules` (filtered by IS NOT NULL)
- Schedule with `enabled = 0` is never returned by `get_due_schedules`
- `update_flow_schedule` with no kwargs is a no-op (returns immediately)
- `update_flow_schedule` with unknown kwarg raises `ValueError`
- `get_running_flow_runs` returns empty list when no flows are running (normal case)
- `get_running_tasks` returns empty list for a flow with no running tasks
- `get_waiting_tasks` with no waiting tasks returns empty list
- `get_waiting_tasks` with tasks whose `wait_until` is in the future returns empty list
- `create_flow_schedule` with invalid `flow_definition_id` raises `IntegrityError`
- `delete_flow_schedule` with non-existent ID is a no-op

## Testing Strategy

File: `tests/state/test_repository.py` (add to existing file from STATE-003/004/005)

Reuse the `db`, `flow_def_id`, `flow_run_id` fixtures.

```python
# --- Flow Schedule Tests ---

def test_create_and_get_flow_schedule(db, flow_def_id):
    """Create a schedule, get by ID, verify fields."""
    schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *")
    schedule = db.get_flow_schedule(schedule_id)
    assert schedule is not None
    assert schedule.cron_expression == "0 * * * *"
    assert schedule.on_overlap == "skip"
    assert schedule.enabled == 1

def test_list_flow_schedules(db, flow_def_id):
    """Create 2 schedules, list returns both."""
    db.create_flow_schedule(flow_def_id, "0 * * * *")
    db.create_flow_schedule(flow_def_id, "0 0 * * *")
    schedules = db.list_flow_schedules()
    assert len(schedules) == 2

def test_list_flow_schedules_by_definition(db, flow_def_id):
    """Filter schedules by flow_definition_id."""
    other_def_id = db.create_flow_definition("other-flow", "source2", "{}")
    db.create_flow_schedule(flow_def_id, "0 * * * *")
    db.create_flow_schedule(other_def_id, "0 0 * * *")
    schedules = db.list_flow_schedules(flow_definition_id=flow_def_id)
    assert len(schedules) == 1

def test_update_flow_schedule(db, flow_def_id):
    """Update cron_expression and enabled status."""
    schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *")
    db.update_flow_schedule(schedule_id, cron_expression="0 0 * * *", enabled=0)
    schedule = db.get_flow_schedule(schedule_id)
    assert schedule is not None
    assert schedule.cron_expression == "0 0 * * *"
    assert schedule.enabled == 0

def test_update_flow_schedule_invalid_kwarg(db, flow_def_id):
    """Unknown kwarg raises ValueError."""
    schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *")
    with pytest.raises(ValueError):
        db.update_flow_schedule(schedule_id, nonexistent_field="value")

def test_delete_flow_schedule(db, flow_def_id):
    """Delete a schedule, verify it's gone."""
    schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *")
    db.delete_flow_schedule(schedule_id)
    assert db.get_flow_schedule(schedule_id) is None

def test_get_due_schedules(db, flow_def_id):
    """Create schedules with different next_trigger_at, verify due filtering."""
    past = "2020-01-01T00:00:00"
    future = "2099-01-01T00:00:00"
    s1 = db.create_flow_schedule(flow_def_id, "0 * * * *", next_trigger_at=past)
    s2 = db.create_flow_schedule(flow_def_id, "0 0 * * *", next_trigger_at=future)
    now = datetime.now(timezone.utc).isoformat()
    due = db.get_due_schedules(now=now)
    assert len(due) == 1
    assert due[0].id == s1

def test_get_due_schedules_excludes_disabled(db, flow_def_id):
    """Disabled schedules are not returned even if due."""
    past = "2020-01-01T00:00:00"
    schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *", next_trigger_at=past)
    db.update_flow_schedule(schedule_id, enabled=0)
    due = db.get_due_schedules()
    assert len(due) == 0

def test_get_due_schedules_excludes_null_trigger(db, flow_def_id):
    """Schedules with NULL next_trigger_at are not returned."""
    db.create_flow_schedule(flow_def_id, "0 * * * *")  # next_trigger_at defaults to None
    due = db.get_due_schedules()
    assert len(due) == 0

def test_create_schedule_invalid_definition_id(db):
    """Creating schedule with non-existent flow_definition_id raises IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        db.create_flow_schedule("nonexistent-id", "0 * * * *")

# --- Recovery Tests ---

def test_get_running_flow_runs(db, flow_def_id):
    """Create a 'running' flow run, verify get_running_flow_runs finds it."""
    run_id = db.create_flow_run(
        flow_definition_id=flow_def_id,
        data_dir="/tmp/test-run",
        budget_seconds=3600,
        on_error="pause",
    )
    db.update_flow_run_status(run_id, "running")
    running = db.get_running_flow_runs()
    assert len(running) == 1
    assert running[0].id == run_id

def test_get_running_flow_runs_excludes_completed(db, flow_def_id):
    """Completed flow runs are not returned by get_running_flow_runs."""
    run_id = db.create_flow_run(
        flow_definition_id=flow_def_id,
        data_dir="/tmp/test-run",
        budget_seconds=3600,
        on_error="pause",
    )
    db.update_flow_run_status(run_id, "running")
    db.update_flow_run_status(run_id, "completed")
    running = db.get_running_flow_runs()
    assert len(running) == 0

def test_get_running_tasks(db, flow_run_id):
    """Create a 'running' task, verify get_running_tasks finds it."""
    task_id = db.create_task_execution(
        flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a"
    )
    db.update_task_status(task_id, "running", started_at=datetime.now(timezone.utc).isoformat())
    running = db.get_running_tasks(flow_run_id)
    assert len(running) == 1
    assert running[0].id == task_id

def test_get_running_tasks_excludes_completed(db, flow_run_id):
    """Completed tasks are not returned by get_running_tasks."""
    task_id = db.create_task_execution(
        flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a"
    )
    db.update_task_status(task_id, "running", started_at=datetime.now(timezone.utc).isoformat())
    db.update_task_status(task_id, "completed", completed_at=datetime.now(timezone.utc).isoformat())
    running = db.get_running_tasks(flow_run_id)
    assert len(running) == 0

# --- Waiting Task Tests ---

def test_get_waiting_tasks(db, flow_run_id):
    """Create a waiting task with past wait_until, verify it's found."""
    task_id = db.create_task_execution(
        flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a"
    )
    past = "2020-01-01T00:00:00"
    db.update_task_status(task_id, "waiting", wait_until=past)
    waiting = db.get_waiting_tasks(flow_run_id)
    assert len(waiting) == 1
    assert waiting[0].id == task_id

def test_get_waiting_tasks_excludes_future(db, flow_run_id):
    """Waiting tasks with future wait_until are not returned."""
    task_id = db.create_task_execution(
        flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a"
    )
    future = "2099-01-01T00:00:00"
    db.update_task_status(task_id, "waiting", wait_until=future)
    waiting = db.get_waiting_tasks(flow_run_id)
    assert len(waiting) == 0

def test_get_waiting_tasks_excludes_non_waiting(db, flow_run_id):
    """Only tasks with status 'waiting' are returned."""
    task_id = db.create_task_execution(
        flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a"
    )
    # Task is 'pending', not 'waiting'
    waiting = db.get_waiting_tasks(flow_run_id)
    assert len(waiting) == 0
```
