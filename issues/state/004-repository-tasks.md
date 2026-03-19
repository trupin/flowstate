# [STATE-004] Repository (Task Executions + Edge Transitions)

## Domain
state

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: STATE-002
- Blocks: ENGINE-005

## Spec References
- specs.md Section 8.1 — "SQLite Schema" (task_executions, edge_transitions tables)
- specs.md Section 8.2 — "Transaction Boundaries" (task status change + edge creation = single transaction)
- agents/02-state.md — "FlowstateDB" interface (task execution and edge transition methods)

## Summary
Add task execution and edge transition CRUD methods to the FlowstateDB class. Task executions track individual node runs within a flow run; edge transitions log every graph edge traversal including judge decisions. The critical design requirement is that task status changes combined with edge creation must be atomic (single transaction), ensuring the state machine never has inconsistent edge/task state.

## Acceptance Criteria
- [ ] `create_task_execution` generates UUID, sets status to `'pending'`, returns ID
- [ ] `get_task_execution` returns `TaskExecutionRow | None`
- [ ] `list_task_executions` returns all tasks for a given flow_run_id
- [ ] `get_pending_tasks` returns tasks with status `'pending'` for a given flow_run_id
- [ ] `update_task_status` updates status and any provided kwargs (claude_session_id, started_at, completed_at, elapsed_seconds, exit_code, summary_path, error_message)
- [ ] `create_edge_transition` generates UUID, returns ID, stores all judge fields
- [ ] Task status update + edge creation can be wrapped in `_transaction()` for atomicity
- [ ] Foreign key constraints are enforced: task must reference valid flow_run, edge must reference valid tasks
- [ ] `uv run pytest tests/state/test_repository.py` passes (task + edge tests)
- [ ] `uv run ruff check src/flowstate/state/repository.py` passes
- [ ] `uv run pyright src/flowstate/state/repository.py` passes

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/repository.py` — add task execution + edge transition methods to FlowstateDB
- `tests/state/test_repository.py` — add task and edge tests

### Key Implementation Details

#### Task Execution Methods

```python
def create_task_execution(
    self,
    flow_run_id: str,
    node_name: str,
    node_type: str,
    generation: int,
    context_mode: str,
    cwd: str,
    task_dir: str,
    prompt_text: str,
) -> str:
    id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    self._execute(
        """INSERT INTO task_executions
           (id, flow_run_id, node_name, node_type, status, generation,
            context_mode, cwd, task_dir, prompt_text, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
        (id, flow_run_id, node_name, node_type, generation,
         context_mode, cwd, task_dir, prompt_text, now),
    )
    self._conn.commit()
    return id

def get_task_execution(self, id: str) -> TaskExecutionRow | None:
    row = self._fetchone("SELECT * FROM task_executions WHERE id = ?", (id,))
    return TaskExecutionRow(**dict(row)) if row else None

def list_task_executions(self, flow_run_id: str) -> list[TaskExecutionRow]:
    rows = self._fetchall(
        "SELECT * FROM task_executions WHERE flow_run_id = ? ORDER BY created_at",
        (flow_run_id,),
    )
    return [TaskExecutionRow(**dict(r)) for r in rows]

def get_pending_tasks(self, flow_run_id: str) -> list[TaskExecutionRow]:
    rows = self._fetchall(
        "SELECT * FROM task_executions WHERE flow_run_id = ? AND status = 'pending' ORDER BY created_at",
        (flow_run_id,),
    )
    return [TaskExecutionRow(**dict(r)) for r in rows]

def update_task_status(self, id: str, status: str, **kwargs) -> None:
    """Update task status and any additional fields.

    Accepted kwargs: claude_session_id, started_at, completed_at,
    elapsed_seconds, exit_code, summary_path, error_message, wait_until
    """
    allowed = {
        "claude_session_id", "started_at", "completed_at", "elapsed_seconds",
        "exit_code", "summary_path", "error_message", "wait_until",
    }
    updates = {"status": status}
    for key, value in kwargs.items():
        if key not in allowed:
            raise ValueError(f"Unknown task field: {key}")
        updates[key] = value

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    self._execute(
        f"UPDATE task_executions SET {set_clause} WHERE id = ?",
        tuple(values),
    )
    self._conn.commit()
```

**Important:** `update_task_status` does NOT auto-commit when called within a `_transaction()` block. The `_transaction()` context manager handles BEGIN/COMMIT. The `self._conn.commit()` in the method is a no-op inside an explicit transaction (sqlite3 behavior). However, to be safe, the implementation should detect whether a transaction is active. A simpler approach: always commit, and let the `_transaction()` context manager use `BEGIN`/`COMMIT` explicitly — the individual `commit()` calls become no-ops inside a manual transaction. **Alternatively**, remove the `self._conn.commit()` from individual methods and always require callers to commit (or use `_transaction()`). The recommended approach is to keep `commit()` in individual methods for standalone use, and override with `_transaction()` for compound operations.

#### Edge Transition Methods

```python
def create_edge_transition(
    self,
    flow_run_id: str,
    from_task_id: str,
    to_task_id: str | None,
    edge_type: str,
    condition_text: str | None = None,
    judge_session_id: str | None = None,
    judge_decision: str | None = None,
    judge_reasoning: str | None = None,
    judge_confidence: float | None = None,
) -> str:
    id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    self._execute(
        """INSERT INTO edge_transitions
           (id, flow_run_id, from_task_id, to_task_id, edge_type,
            condition_text, judge_session_id, judge_decision,
            judge_reasoning, judge_confidence, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (id, flow_run_id, from_task_id, to_task_id, edge_type,
         condition_text, judge_session_id, judge_decision,
         judge_reasoning, judge_confidence, now),
    )
    self._conn.commit()
    return id
```

#### Compound Transaction Example

The engine will use the `_transaction()` context manager for atomic operations:

```python
# In the engine (not in repository.py — but the repository must support this pattern):
with db._transaction():
    db.update_task_status(task_id, "completed", completed_at=now, exit_code=0, summary_path=path)
    db.create_edge_transition(flow_run_id, task_id, next_task_id, "unconditional")
```

For this to work, individual methods must NOT call `self._conn.commit()` when inside a `_transaction()`. The recommended implementation:

- Set `self._conn.isolation_level = None` (autocommit mode for sqlite3)
- Use explicit `BEGIN`/`COMMIT`/`ROLLBACK` in `_transaction()`
- Individual methods call `self._conn.commit()` which is a no-op in autocommit mode outside transactions but works correctly inside `BEGIN`/`COMMIT`

**Alternative (simpler):** Use `self._conn.isolation_level = "DEFERRED"` (default) and rely on sqlite3's implicit transaction behavior. Individual writes auto-commit. The `_transaction()` context manager uses `self._conn.execute("BEGIN")` for compound ops. Inside a BEGIN block, individual `commit()` calls are harmless because they commit the ongoing transaction — so instead, the `_transaction()` should suppress intermediate commits. The cleanest approach:

```python
def __init__(self, ...):
    self._in_transaction = False

@contextmanager
def _transaction(self):
    self._conn.execute("BEGIN")
    self._in_transaction = True
    try:
        yield
        self._conn.execute("COMMIT")
    except Exception:
        self._conn.execute("ROLLBACK")
        raise
    finally:
        self._in_transaction = False

def _commit(self) -> None:
    """Commit unless inside an explicit transaction."""
    if not self._in_transaction:
        self._conn.commit()
```

Then all methods call `self._commit()` instead of `self._conn.commit()`.

### Edge Cases
- `update_task_status` with unknown kwargs raises `ValueError`
- `update_task_status` with empty kwargs (just status change) is valid
- `get_pending_tasks` on a flow_run with no pending tasks returns empty list
- `create_edge_transition` with `to_task_id=None` is valid (e.g., when a conditional edge leads to no target)
- Foreign key enforcement: `create_task_execution` with invalid `flow_run_id` raises `IntegrityError`
- Foreign key enforcement: `create_edge_transition` with invalid `from_task_id` raises `IntegrityError`
- Edge `judge_confidence` is a float (REAL in SQLite) — ensure proper type handling

## Testing Strategy

File: `tests/state/test_repository.py` (add to existing file from STATE-003)

Helper fixtures to reduce boilerplate:

```python
@pytest.fixture
def db():
    return FlowstateDB(":memory:")

@pytest.fixture
def flow_def_id(db):
    """Create a flow definition and return its ID."""
    return db.create_flow_definition("test-flow", "source", "{}")

@pytest.fixture
def flow_run_id(db, flow_def_id):
    """Create a flow run and return its ID."""
    return db.create_flow_run(
        flow_definition_id=flow_def_id,
        data_dir="/tmp/test-run",
        budget_seconds=3600,
        on_error="pause",
    )
```

Tests:

```python
# --- Task Execution Tests ---

def test_create_and_get_task_execution(db, flow_run_id):
    """Create a task, get by ID, verify status='pending'."""

def test_list_task_executions(db, flow_run_id):
    """Create 3 tasks, list returns all 3 ordered by created_at."""

def test_get_pending_tasks(db, flow_run_id):
    """Create 2 pending + 1 running task, get_pending returns only 2."""

def test_update_task_status_to_running(db, flow_run_id):
    """Update to 'running' with claude_session_id and started_at."""

def test_update_task_status_to_completed(db, flow_run_id):
    """Update to 'completed' with completed_at, elapsed_seconds, exit_code, summary_path."""

def test_update_task_status_to_failed(db, flow_run_id):
    """Update to 'failed' with error_message."""

def test_update_task_status_invalid_kwarg(db, flow_run_id):
    """Passing unknown kwarg raises ValueError."""

def test_create_task_invalid_flow_run_id(db):
    """Creating task with non-existent flow_run_id raises IntegrityError."""

# --- Edge Transition Tests ---

def test_create_edge_transition_unconditional(db, flow_run_id):
    """Create an unconditional edge, verify all fields."""

def test_create_edge_transition_with_judge(db, flow_run_id):
    """Create a conditional edge with all judge fields populated."""

def test_create_edge_transition_null_to_task(db, flow_run_id):
    """Edge with to_task_id=None is valid."""

def test_edge_transition_invalid_from_task(db, flow_run_id):
    """Creating edge with non-existent from_task_id raises IntegrityError."""

# --- Compound Transaction Tests ---

def test_task_status_and_edge_atomic_success(db, flow_run_id):
    """Within _transaction(), both task update and edge creation succeed."""

def test_task_status_and_edge_atomic_rollback(db, flow_run_id):
    """If edge creation fails inside _transaction(), task status is rolled back."""
    # Create task, start transaction, update status, try to create edge with invalid FK → rollback
    # Verify task status is unchanged.
```
