# [STATE-001] SQLite Schema + Database Setup

## Domain
state

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-001
- Blocks: STATE-002

## Spec References
- specs.md Section 8.1 — "SQLite Schema"
- specs.md Section 8.4 — "Database Configuration"
- agents/02-state.md — "SQLite Schema" and "Key Constraints"

## Summary
Create the SQLite schema file containing all 9 CREATE TABLE statements and 8 indexes from the spec, plus the database connection management module that opens/creates the database, enables WAL mode, sets pragmas, and initializes the schema on first connection. This is the foundation upon which all other state-layer issues build.

## Acceptance Criteria
- [ ] `src/flowstate/state/__init__.py` exists (can be empty or re-export `FlowstateDB`)
- [ ] `src/flowstate/state/schema.sql` contains all 9 CREATE TABLE statements verbatim from specs.md Section 8.1
- [ ] `src/flowstate/state/schema.sql` contains all 8 CREATE INDEX statements from specs.md Section 8.1
- [ ] `src/flowstate/state/database.py` provides a `FlowstateDB` class (or factory) that opens/creates a SQLite database
- [ ] WAL mode is enabled on every connection (`PRAGMA journal_mode=WAL`)
- [ ] Busy timeout is set to 5000ms (`PRAGMA busy_timeout=5000`)
- [ ] Foreign keys are enforced (`PRAGMA foreign_keys=ON`)
- [ ] Journal size limit is set to 64MB (`PRAGMA journal_size_limit=67108864`)
- [ ] Schema is created automatically on first connection (reads and executes `schema.sql`)
- [ ] Schema creation is idempotent (use `CREATE TABLE IF NOT EXISTS` or check before creating)
- [ ] `uv run pytest tests/state/test_database.py` passes
- [ ] `uv run ruff check src/flowstate/state/` passes
- [ ] `uv run pyright src/flowstate/state/` passes

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/__init__.py` — package init; may re-export key symbols
- `src/flowstate/state/schema.sql` — all CREATE TABLE and CREATE INDEX statements
- `src/flowstate/state/database.py` — connection management class
- `tests/state/__init__.py` — test package init (empty)
- `tests/state/test_database.py` — tests for database setup

### Key Implementation Details

#### `schema.sql`

Copy the exact schema from specs.md Section 8.1. Use `IF NOT EXISTS` on all CREATE TABLE and CREATE INDEX statements for idempotency. The file should contain:

**Tables (9):**
1. `flow_definitions` — parsed DSL stored alongside source. Columns: id (TEXT PK), name (TEXT NOT NULL UNIQUE), source_dsl (TEXT NOT NULL), ast_json (TEXT NOT NULL), created_at (TIMESTAMP DEFAULT CURRENT_TIMESTAMP), updated_at (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
2. `flow_runs` — execution instances. Columns: id (TEXT PK), flow_definition_id (TEXT NOT NULL FK → flow_definitions), status (TEXT NOT NULL CHECK IN created/running/paused/completed/failed/cancelled/budget_exceeded), default_workspace (TEXT), data_dir (TEXT NOT NULL), params_json (TEXT), budget_seconds (INTEGER NOT NULL), elapsed_seconds (REAL DEFAULT 0), on_error (TEXT NOT NULL CHECK IN pause/abort/skip), started_at (TIMESTAMP), completed_at (TIMESTAMP), created_at (TIMESTAMP DEFAULT CURRENT_TIMESTAMP), error_message (TEXT)
3. `task_executions` — individual node runs. Columns: id (TEXT PK), flow_run_id (TEXT NOT NULL FK → flow_runs), node_name (TEXT NOT NULL), node_type (TEXT NOT NULL CHECK IN entry/task/exit), status (TEXT NOT NULL CHECK IN pending/waiting/running/completed/failed/skipped), wait_until (TIMESTAMP), generation (INTEGER NOT NULL DEFAULT 1), context_mode (TEXT NOT NULL CHECK IN handoff/session/none), cwd (TEXT NOT NULL), claude_session_id (TEXT), task_dir (TEXT NOT NULL), prompt_text (TEXT NOT NULL), started_at (TIMESTAMP), completed_at (TIMESTAMP), elapsed_seconds (REAL), exit_code (INTEGER), summary_path (TEXT), error_message (TEXT), created_at (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
4. `edge_transitions` — log of every edge traversal. Columns: id (TEXT PK), flow_run_id (TEXT NOT NULL FK → flow_runs), from_task_id (TEXT NOT NULL FK → task_executions), to_task_id (TEXT FK → task_executions), edge_type (TEXT NOT NULL CHECK IN unconditional/conditional/fork/join), condition_text (TEXT), judge_session_id (TEXT), judge_decision (TEXT), judge_reasoning (TEXT), judge_confidence (REAL), created_at (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
5. `fork_groups` — parallel execution groups. Columns: id (TEXT PK), flow_run_id (TEXT NOT NULL FK → flow_runs), source_task_id (TEXT NOT NULL FK → task_executions), join_node_name (TEXT NOT NULL), generation (INTEGER NOT NULL DEFAULT 1), status (TEXT NOT NULL CHECK IN active/joined/cancelled), created_at (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
6. `fork_group_members` — junction table. Columns: fork_group_id (TEXT NOT NULL FK → fork_groups), task_execution_id (TEXT NOT NULL FK → task_executions), PRIMARY KEY (fork_group_id, task_execution_id)
7. `task_logs` — streaming logs. Columns: id (INTEGER PRIMARY KEY AUTOINCREMENT), task_execution_id (TEXT NOT NULL FK → task_executions), timestamp (TIMESTAMP DEFAULT CURRENT_TIMESTAMP), log_type (TEXT NOT NULL CHECK IN stdout/stderr/tool_use/assistant_message/system), content (TEXT NOT NULL)
8. `flow_schedules` — recurring flows. Columns: id (TEXT PK), flow_definition_id (TEXT NOT NULL FK → flow_definitions), cron_expression (TEXT NOT NULL), on_overlap (TEXT NOT NULL DEFAULT 'skip' CHECK IN skip/queue/parallel), enabled (INTEGER NOT NULL DEFAULT 1), last_triggered_at (TIMESTAMP), next_trigger_at (TIMESTAMP), created_at (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)

**Indexes (8):**
1. `idx_flow_runs_status` ON flow_runs(status)
2. `idx_task_executions_flow_run` ON task_executions(flow_run_id)
3. `idx_task_executions_status` ON task_executions(flow_run_id, status)
4. `idx_task_executions_waiting` ON task_executions(status, wait_until) WHERE status = 'waiting'
5. `idx_edge_transitions_flow_run` ON edge_transitions(flow_run_id)
6. `idx_task_logs_execution` ON task_logs(task_execution_id)
7. `idx_task_logs_timestamp` ON task_logs(task_execution_id, timestamp)
8. `idx_fork_groups_flow_run` ON fork_groups(flow_run_id)
9. `idx_flow_schedules_next` ON flow_schedules(next_trigger_at) WHERE enabled = 1

#### `database.py`

```python
import sqlite3
from pathlib import Path

class FlowstateDB:
    def __init__(self, db_path: str = "~/.flowstate/flowstate.db"):
        """Open/create database, enable WAL mode, create tables if needed."""
        resolved = Path(db_path).expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(resolved))
        self._conn.row_factory = sqlite3.Row
        self._configure_pragmas()
        self._initialize_schema()

    def _configure_pragmas(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_size_limit=67108864")

    def _initialize_schema(self) -> None:
        schema_path = Path(__file__).parent / "schema.sql"
        schema_sql = schema_path.read_text()
        self._conn.executescript(schema_sql)

    def close(self) -> None:
        self._conn.close()
```

Key points:
- Use standard library `sqlite3` only — no SQLAlchemy, no aiosqlite.
- `row_factory = sqlite3.Row` enables dict-like access to rows.
- The class is NOT thread-safe (by design — engine ensures single-writer).
- For `:memory:` databases (used in tests), skip the `mkdir` parent logic. Handle this by checking if `db_path == ":memory:"`.

### Edge Cases
- Database file doesn't exist yet → should be created automatically
- Database file already exists with schema → `IF NOT EXISTS` prevents errors
- `:memory:` path used in tests → skip parent directory creation
- `schema.sql` file not found → raise a clear error (not a silent failure)
- WAL mode verification: after setting, query `PRAGMA journal_mode` and confirm it returns `'wal'`

## Testing Strategy

File: `tests/state/test_database.py`

Tests use in-memory SQLite (`:memory:`) for speed and isolation:

```python
def test_database_opens_in_memory():
    """FlowstateDB(":memory:") opens without error."""

def test_wal_mode_enabled():
    """After init, PRAGMA journal_mode returns 'wal'."""
    # Note: in-memory databases may not support WAL — test with a temp file if needed.

def test_foreign_keys_enabled():
    """PRAGMA foreign_keys returns 1."""

def test_busy_timeout_set():
    """PRAGMA busy_timeout returns 5000."""

def test_all_tables_exist():
    """Query sqlite_master for all 8 table names + fork_group_members."""

def test_all_indexes_exist():
    """Query sqlite_master for all 9 index names."""

def test_schema_idempotent():
    """Calling _initialize_schema() twice doesn't raise."""

def test_foreign_key_constraint_enforced():
    """Inserting a flow_run with a non-existent flow_definition_id raises IntegrityError."""
```

Use `tmp_path` fixture from pytest for file-based database tests (WAL mode).
