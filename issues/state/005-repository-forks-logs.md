# [STATE-005] Repository (Fork Groups + Task Logs)

## Domain
state

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: STATE-002
- Blocks: ENGINE-006

## Spec References
- specs.md Section 8.1 — "SQLite Schema" (fork_groups, fork_group_members, task_logs tables)
- specs.md Section 8.2 — "Transaction Boundaries" (fork group creation + all member inserts = single transaction; log insertion = individual transactions)
- agents/02-state.md — "FlowstateDB" interface (fork group and task log methods)

## Summary
Add fork group and task log methods to the FlowstateDB class. Fork groups track parallel execution branches created by fork edges, with a junction table for member tasks. The critical invariant is that fork group creation and all member inserts happen in a single transaction — a partially-created fork group would corrupt the execution state. Task logs are high-frequency inserts (streaming stdout/stderr from Claude subprocesses) and use individual transactions since losing a few log lines on crash is acceptable.

## Acceptance Criteria
- [ ] `create_fork_group` accepts member_task_ids list, creates the fork_group row AND all fork_group_members rows in a single transaction, returns the group ID
- [ ] `get_fork_group` returns `ForkGroupRow | None`
- [ ] `get_active_fork_groups` returns fork groups with status `'active'` for a given flow_run_id
- [ ] `get_fork_group_members` returns `list[TaskExecutionRow]` (joins fork_group_members with task_executions)
- [ ] `update_fork_group_status` updates the status column
- [ ] `insert_task_log` inserts a log entry with auto-generated timestamp
- [ ] `get_task_logs` returns logs for a task, supports `after_timestamp` filter and `limit` parameter
- [ ] Fork group creation is atomic: if any member insert fails, the group row is also rolled back
- [ ] `uv run pytest tests/state/test_repository.py` passes (fork + log tests)
- [ ] `uv run ruff check src/flowstate/state/repository.py` passes
- [ ] `uv run pyright src/flowstate/state/repository.py` passes

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/repository.py` — add fork group + task log methods to FlowstateDB
- `tests/state/test_repository.py` — add fork and log tests

### Key Implementation Details

#### Fork Group Methods

```python
def create_fork_group(
    self,
    flow_run_id: str,
    source_task_id: str,
    join_node_name: str,
    generation: int,
    member_task_ids: list[str],
) -> str:
    """Create a fork group and all its members atomically."""
    id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with self._transaction():
        self._execute(
            """INSERT INTO fork_groups
               (id, flow_run_id, source_task_id, join_node_name, generation, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?)""",
            (id, flow_run_id, source_task_id, join_node_name, generation, now),
        )
        for task_id in member_task_ids:
            self._execute(
                "INSERT INTO fork_group_members (fork_group_id, task_execution_id) VALUES (?, ?)",
                (id, task_id),
            )
    return id

def get_fork_group(self, id: str) -> ForkGroupRow | None:
    row = self._fetchone("SELECT * FROM fork_groups WHERE id = ?", (id,))
    return ForkGroupRow(**dict(row)) if row else None

def get_active_fork_groups(self, flow_run_id: str) -> list[ForkGroupRow]:
    rows = self._fetchall(
        "SELECT * FROM fork_groups WHERE flow_run_id = ? AND status = 'active' ORDER BY created_at",
        (flow_run_id,),
    )
    return [ForkGroupRow(**dict(r)) for r in rows]

def get_fork_group_members(self, fork_group_id: str) -> list[TaskExecutionRow]:
    """Get task executions that are members of a fork group.

    Joins fork_group_members with task_executions to return full task rows.
    """
    rows = self._fetchall(
        """SELECT te.* FROM task_executions te
           JOIN fork_group_members fgm ON te.id = fgm.task_execution_id
           WHERE fgm.fork_group_id = ?
           ORDER BY te.created_at""",
        (fork_group_id,),
    )
    return [TaskExecutionRow(**dict(r)) for r in rows]

def update_fork_group_status(self, id: str, status: str) -> None:
    self._execute(
        "UPDATE fork_groups SET status = ? WHERE id = ?",
        (status, id),
    )
    self._commit()
```

#### Task Log Methods

```python
def insert_task_log(self, task_execution_id: str, log_type: str, content: str) -> None:
    """Insert a log entry. Uses individual transaction (high frequency, loss acceptable)."""
    self._execute(
        """INSERT INTO task_logs (task_execution_id, log_type, content)
           VALUES (?, ?, ?)""",
        (task_execution_id, log_type, content),
    )
    self._commit()

def get_task_logs(
    self,
    task_execution_id: str,
    after_timestamp: str | None = None,
    limit: int = 1000,
) -> list[TaskLogRow]:
    """Get logs for a task, optionally filtering by timestamp.

    Args:
        task_execution_id: The task to get logs for.
        after_timestamp: If provided, only return logs with timestamp > this value (ISO 8601).
        limit: Maximum number of log entries to return (default 1000).
    """
    if after_timestamp:
        rows = self._fetchall(
            """SELECT * FROM task_logs
               WHERE task_execution_id = ? AND timestamp > ?
               ORDER BY timestamp ASC, id ASC
               LIMIT ?""",
            (task_execution_id, after_timestamp, limit),
        )
    else:
        rows = self._fetchall(
            """SELECT * FROM task_logs
               WHERE task_execution_id = ?
               ORDER BY timestamp ASC, id ASC
               LIMIT ?""",
            (task_execution_id, limit),
        )
    return [TaskLogRow(**dict(r)) for r in rows]
```

**Log ordering:** Logs are ordered by `(timestamp ASC, id ASC)`. The `id` tiebreaker is important because multiple log entries can share the same timestamp (SQLite CURRENT_TIMESTAMP has second-level precision, and many logs arrive within the same second). The AUTOINCREMENT `id` guarantees insertion order.

**No explicit timestamp in insert_task_log:** The `timestamp` column uses `DEFAULT CURRENT_TIMESTAMP`, so we let SQLite set it. This avoids clock skew issues between Python's `datetime` and SQLite's internal clock.

### Edge Cases
- `create_fork_group` with empty `member_task_ids` list: technically valid (creates a group with no members), but unlikely in practice. Allow it — the engine can validate upstream.
- `create_fork_group` with an invalid `member_task_ids` entry (non-existent task_execution_id): the foreign key constraint on fork_group_members will cause the transaction to roll back, removing the fork_group row too. This is the desired behavior.
- `get_fork_group_members` returns empty list if the group has no members.
- `get_task_logs` with `limit=0` should return empty list.
- `get_task_logs` with `after_timestamp` set to a future timestamp returns empty list.
- `insert_task_log` with very large `content` (e.g., full tool output): SQLite handles TEXT of any size, but be aware of database bloat.
- Concurrent log inserts from multiple tasks: WAL mode handles this. Each insert is an independent transaction.

## Testing Strategy

File: `tests/state/test_repository.py` (add to existing file from STATE-003/004)

Reuse the `db`, `flow_def_id`, `flow_run_id` fixtures from STATE-003/004 tests.

```python
# --- Fork Group Tests ---

def test_create_fork_group_with_members(db, flow_run_id):
    """Create 3 tasks, create a fork group with them, verify group and members."""
    t1 = db.create_task_execution(flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a")
    t2 = db.create_task_execution(flow_run_id, "b", "task", 1, "handoff", "/tmp", "/tmp/b-1", "do b")
    t3 = db.create_task_execution(flow_run_id, "c", "task", 1, "handoff", "/tmp", "/tmp/c-1", "do c")
    group_id = db.create_fork_group(flow_run_id, t1, "join_node", 1, [t2, t3])
    group = db.get_fork_group(group_id)
    assert group is not None
    assert group.status == "active"
    members = db.get_fork_group_members(group_id)
    assert len(members) == 2

def test_fork_group_creation_atomicity(db, flow_run_id):
    """If a member insert fails, the group row is also rolled back."""
    t1 = db.create_task_execution(flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_fork_group(flow_run_id, t1, "join_node", 1, [t1, "nonexistent-id"])
    # Verify no fork group was created
    groups = db.get_active_fork_groups(flow_run_id)
    assert len(groups) == 0

def test_get_active_fork_groups(db, flow_run_id):
    """Create 2 groups, mark one 'joined', get_active returns only the active one."""
    t1 = db.create_task_execution(flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a")
    t2 = db.create_task_execution(flow_run_id, "b", "task", 1, "handoff", "/tmp", "/tmp/b-1", "do b")
    g1 = db.create_fork_group(flow_run_id, t1, "join1", 1, [t2])
    g2 = db.create_fork_group(flow_run_id, t1, "join2", 1, [t2])
    db.update_fork_group_status(g1, "joined")
    active = db.get_active_fork_groups(flow_run_id)
    assert len(active) == 1
    assert active[0].id == g2

def test_update_fork_group_status(db, flow_run_id):
    """Update fork group status from 'active' to 'joined'."""
    t1 = db.create_task_execution(flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a")
    group_id = db.create_fork_group(flow_run_id, t1, "join_node", 1, [])
    db.update_fork_group_status(group_id, "joined")
    group = db.get_fork_group(group_id)
    assert group is not None
    assert group.status == "joined"

# --- Task Log Tests ---

def test_insert_and_get_task_logs(db, flow_run_id):
    """Insert 3 logs, get_task_logs returns all 3 in order."""
    task_id = db.create_task_execution(flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a")
    db.insert_task_log(task_id, "stdout", "line 1")
    db.insert_task_log(task_id, "stderr", "error 1")
    db.insert_task_log(task_id, "assistant_message", "thinking...")
    logs = db.get_task_logs(task_id)
    assert len(logs) == 3
    assert logs[0].log_type == "stdout"
    assert logs[0].content == "line 1"

def test_get_task_logs_with_limit(db, flow_run_id):
    """Insert 5 logs, get with limit=2 returns first 2."""
    task_id = db.create_task_execution(flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a")
    for i in range(5):
        db.insert_task_log(task_id, "stdout", f"line {i}")
    logs = db.get_task_logs(task_id, limit=2)
    assert len(logs) == 2

def test_get_task_logs_after_timestamp(db, flow_run_id):
    """Insert logs, filter by after_timestamp to get only newer entries."""
    task_id = db.create_task_execution(flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a")
    db.insert_task_log(task_id, "stdout", "old line")
    logs_before = db.get_task_logs(task_id)
    cutoff = logs_before[0].timestamp  # timestamp of the first log
    db.insert_task_log(task_id, "stdout", "new line")
    logs_after = db.get_task_logs(task_id, after_timestamp=cutoff)
    # Should contain only the second log (timestamp strictly > cutoff)
    assert all(log.content != "old line" for log in logs_after)

def test_task_log_ordering_by_id(db, flow_run_id):
    """Logs inserted in rapid succession maintain insertion order via id tiebreaker."""
    task_id = db.create_task_execution(flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a")
    for i in range(10):
        db.insert_task_log(task_id, "stdout", f"line {i}")
    logs = db.get_task_logs(task_id)
    assert [l.content for l in logs] == [f"line {i}" for i in range(10)]
```
