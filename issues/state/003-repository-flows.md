# [STATE-003] Repository (Flow Definitions + Runs)

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
- specs.md Section 8.1 — "SQLite Schema" (flow_definitions, flow_runs tables)
- specs.md Section 8.2 — "Transaction Boundaries" (flow status change = single transaction with elapsed_seconds update)
- agents/02-state.md — "FlowstateDB" interface (flow definitions and flow runs methods)

## Summary
Implement the FlowstateDB repository class with full CRUD operations for flow definitions and flow runs. This is the first repository issue and establishes the class structure, the `_transaction()` context manager, and the pattern for converting `sqlite3.Row` objects into Pydantic models. All subsequent repository issues (STATE-004 through STATE-006) add methods to this same class.

## Acceptance Criteria
- [ ] `src/flowstate/state/repository.py` contains a `FlowstateDB` class
- [ ] `FlowstateDB.__init__` accepts `db_path: str` and delegates to `database.py` for connection setup (or integrates the database setup directly)
- [ ] Flow definition methods: `create_flow_definition`, `get_flow_definition`, `get_flow_definition_by_name`, `list_flow_definitions`, `update_flow_definition`, `delete_flow_definition`
- [ ] Flow run methods: `create_flow_run`, `get_flow_run`, `list_flow_runs`, `update_flow_run_status`, `update_flow_run_elapsed`
- [ ] `create_flow_definition` generates a UUID via `uuid.uuid4()` and returns it
- [ ] `create_flow_run` generates a UUID, sets initial status to `'created'`, and returns the ID
- [ ] `list_flow_runs` accepts optional `status` filter parameter
- [ ] `update_flow_run_status` updates status, sets `completed_at` timestamp when status is terminal, and updates `error_message` if provided
- [ ] `update_flow_run_elapsed` updates only the `elapsed_seconds` column
- [ ] All timestamps are ISO 8601 strings generated via `datetime.utcnow().isoformat()`
- [ ] `uv run pytest tests/state/test_repository.py` passes (at least the flow-related tests)
- [ ] `uv run ruff check src/flowstate/state/repository.py` passes
- [ ] `uv run pyright src/flowstate/state/repository.py` passes

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/repository.py` — FlowstateDB class with flow definition + flow run methods
- `tests/state/test_repository.py` — comprehensive tests for all flow CRUD operations

### Key Implementation Details

#### Class Structure

```python
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from flowstate.state.models import FlowDefinitionRow, FlowRunRow

class FlowstateDB:
    def __init__(self, db_path: str = "~/.flowstate/flowstate.db"):
        # Reuse or incorporate logic from database.py
        # Set up connection, pragmas, schema
        ...

    @contextmanager
    def _transaction(self):
        """Context manager for explicit transactions.

        Usage:
            with self._transaction():
                self._execute(...)
                self._execute(...)
        If an exception occurs, the transaction is rolled back.
        """
        self._conn.execute("BEGIN")
        try:
            yield
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def _fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()
```

#### Flow Definition Methods

```python
def create_flow_definition(self, name: str, source_dsl: str, ast_json: str) -> str:
    id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    self._execute(
        "INSERT INTO flow_definitions (id, name, source_dsl, ast_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (id, name, source_dsl, ast_json, now, now),
    )
    self._conn.commit()
    return id

def get_flow_definition(self, id: str) -> FlowDefinitionRow | None:
    row = self._fetchone("SELECT * FROM flow_definitions WHERE id = ?", (id,))
    return FlowDefinitionRow(**dict(row)) if row else None

def get_flow_definition_by_name(self, name: str) -> FlowDefinitionRow | None:
    row = self._fetchone("SELECT * FROM flow_definitions WHERE name = ?", (name,))
    return FlowDefinitionRow(**dict(row)) if row else None

def list_flow_definitions(self) -> list[FlowDefinitionRow]:
    rows = self._fetchall("SELECT * FROM flow_definitions ORDER BY created_at DESC")
    return [FlowDefinitionRow(**dict(r)) for r in rows]

def update_flow_definition(self, id: str, source_dsl: str, ast_json: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    self._execute(
        "UPDATE flow_definitions SET source_dsl = ?, ast_json = ?, updated_at = ? WHERE id = ?",
        (source_dsl, ast_json, now, id),
    )
    self._conn.commit()

def delete_flow_definition(self, id: str) -> None:
    self._execute("DELETE FROM flow_definitions WHERE id = ?", (id,))
    self._conn.commit()
```

#### Flow Run Methods

```python
def create_flow_run(
    self,
    flow_definition_id: str,
    data_dir: str,
    budget_seconds: int,
    on_error: str,
    default_workspace: str | None = None,
    params_json: str | None = None,
) -> str:
    id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    self._execute(
        """INSERT INTO flow_runs
           (id, flow_definition_id, status, default_workspace, data_dir, params_json,
            budget_seconds, elapsed_seconds, on_error, created_at)
           VALUES (?, ?, 'created', ?, ?, ?, ?, 0, ?, ?)""",
        (id, flow_definition_id, default_workspace, data_dir, params_json,
         budget_seconds, on_error, now),
    )
    self._conn.commit()
    return id

def get_flow_run(self, id: str) -> FlowRunRow | None:
    row = self._fetchone("SELECT * FROM flow_runs WHERE id = ?", (id,))
    return FlowRunRow(**dict(row)) if row else None

def list_flow_runs(self, status: str | None = None) -> list[FlowRunRow]:
    if status:
        rows = self._fetchall(
            "SELECT * FROM flow_runs WHERE status = ? ORDER BY created_at DESC", (status,)
        )
    else:
        rows = self._fetchall("SELECT * FROM flow_runs ORDER BY created_at DESC")
    return [FlowRunRow(**dict(r)) for r in rows]

def update_flow_run_status(self, id: str, status: str, error_message: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    terminal = {"completed", "failed", "cancelled", "budget_exceeded"}
    if status in terminal:
        self._execute(
            "UPDATE flow_runs SET status = ?, completed_at = ?, error_message = ? WHERE id = ?",
            (status, now, error_message, id),
        )
    elif status == "running":
        self._execute(
            "UPDATE flow_runs SET status = ?, started_at = COALESCE(started_at, ?), error_message = ? WHERE id = ?",
            (status, now, error_message, id),
        )
    else:
        self._execute(
            "UPDATE flow_runs SET status = ?, error_message = ? WHERE id = ?",
            (status, error_message, id),
        )
    self._conn.commit()

def update_flow_run_elapsed(self, id: str, elapsed_seconds: float) -> None:
    self._execute(
        "UPDATE flow_runs SET elapsed_seconds = ? WHERE id = ?",
        (elapsed_seconds, id),
    )
    self._conn.commit()
```

**Note on `create_flow_run` signature:** The spec in agents/02-state.md shows `workspace_path` as a parameter, but the schema (Section 8.1) has `default_workspace` and `data_dir` as separate columns. Follow the **schema** as the source of truth. The `data_dir` is typically `~/.flowstate/runs/<id>/` and should be passed by the caller (engine).

#### Row-to-Model Conversion Pattern

All `get_*` and `list_*` methods follow this pattern:
1. Execute the SQL query
2. If the result is a single row, check for None, then `ModelClass(**dict(row))`
3. If the result is a list, return `[ModelClass(**dict(r)) for r in rows]`

This works because `sqlite3.Row` supports `dict()` conversion and Pydantic models accept keyword arguments matching field names.

### Edge Cases
- `get_flow_definition` with non-existent ID returns `None`, not an exception
- `delete_flow_definition` with non-existent ID is a no-op (DELETE WHERE id=? affects 0 rows)
- `update_flow_run_status` to `'running'` should set `started_at` only if it hasn't been set yet (COALESCE)
- `update_flow_run_status` to a terminal status should set `completed_at`
- `list_flow_runs(status=None)` returns all runs; `list_flow_runs(status='running')` filters
- Attempting to create a flow_run with a non-existent `flow_definition_id` raises `sqlite3.IntegrityError` due to foreign key constraint
- `name` uniqueness constraint on flow_definitions: inserting a duplicate name raises `sqlite3.IntegrityError`

## Testing Strategy

File: `tests/state/test_repository.py`

All tests use `:memory:` database. Create a `db` fixture that returns a fresh `FlowstateDB(":memory:")` for each test.

```python
@pytest.fixture
def db():
    return FlowstateDB(":memory:")

# --- Flow Definition Tests ---

def test_create_and_get_flow_definition(db):
    """Create a definition, get it by ID, verify all fields."""

def test_get_flow_definition_by_name(db):
    """Create a definition, retrieve by name."""

def test_get_flow_definition_not_found(db):
    """get_flow_definition with bogus ID returns None."""

def test_list_flow_definitions(db):
    """Create 3 definitions, list returns all 3."""

def test_update_flow_definition(db):
    """Create, update source_dsl and ast_json, verify updated_at changed."""

def test_delete_flow_definition(db):
    """Create, delete, get returns None."""

def test_duplicate_flow_definition_name(db):
    """Creating two definitions with the same name raises IntegrityError."""

# --- Flow Run Tests ---

def test_create_and_get_flow_run(db):
    """Create a run, get by ID, verify status='created' and elapsed_seconds=0."""

def test_list_flow_runs_all(db):
    """Create multiple runs, list without filter returns all."""

def test_list_flow_runs_by_status(db):
    """Create runs with different statuses, filter by 'running'."""

def test_update_flow_run_status_to_running(db):
    """Update status to 'running', verify started_at is set."""

def test_update_flow_run_status_to_completed(db):
    """Update status to 'completed', verify completed_at is set."""

def test_update_flow_run_status_with_error(db):
    """Update to 'failed' with error_message, verify both set."""

def test_update_flow_run_elapsed(db):
    """Update elapsed_seconds, verify the new value."""

def test_create_flow_run_invalid_definition_id(db):
    """Creating a flow_run with non-existent flow_definition_id raises IntegrityError."""
```
