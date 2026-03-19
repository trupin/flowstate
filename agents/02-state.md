# Agent 2: State Layer — SQLite + Repository

## Role

You are implementing the Flowstate persistence layer: the SQLite schema, database connection management, and a repository class that provides all CRUD operations needed by the execution engine and web server.

Read `specs.md` section **8 (State Management)** for the full schema and requirements.

## Phase

**Phase 1** — depends only on AST types from `src/flowstate/dsl/ast.py`. If that file doesn't exist yet, you can create it from Section 11.1 of specs.md, but Agent 1 owns it — prefer importing from it.

## Files to Create

```
src/flowstate/state/__init__.py
src/flowstate/state/schema.sql        ← full CREATE TABLE statements
src/flowstate/state/database.py       ← connection management, WAL setup, migrations
src/flowstate/state/models.py         ← Pydantic models for DB rows
src/flowstate/state/repository.py     ← FlowstateDB class with all CRUD operations
tests/state/__init__.py
tests/state/test_repository.py
```

## Dependencies

- **Python packages:** `pydantic` (data models), standard library `sqlite3`
- **Internal:** `flowstate.dsl.ast` (AST types for `flow_definitions.ast_json` serialization)

## Exported Interface

Other agents import from this module:

```python
from flowstate.state.database import get_database      # → FlowstateDB
from flowstate.state.repository import FlowstateDB
from flowstate.state.models import (
    FlowDefinitionRow, FlowRunRow, TaskExecutionRow,
    EdgeTransitionRow, ForkGroupRow, TaskLogRow,
)
```

### `FlowstateDB`

The main class. Wraps a SQLite connection and provides all operations:

```python
class FlowstateDB:
    def __init__(self, db_path: str = "./flowstate.db"):
        """Open/create database, enable WAL mode, create tables if needed."""

    # Flow definitions
    def create_flow_definition(self, name: str, source_dsl: str, ast_json: str) -> str:  # returns id
    def get_flow_definition(self, id: str) -> FlowDefinitionRow | None:
    def get_flow_definition_by_name(self, name: str) -> FlowDefinitionRow | None:
    def list_flow_definitions(self) -> list[FlowDefinitionRow]:
    def update_flow_definition(self, id: str, source_dsl: str, ast_json: str) -> None:
    def delete_flow_definition(self, id: str) -> None:

    # Flow runs
    def create_flow_run(self, flow_definition_id: str, workspace_path: str,
                        params_json: str, budget_seconds: int, on_error: str) -> str:
    def get_flow_run(self, id: str) -> FlowRunRow | None:
    def list_flow_runs(self, status: str | None = None) -> list[FlowRunRow]:
    def update_flow_run_status(self, id: str, status: str, error_message: str | None = None) -> None:
    def update_flow_run_elapsed(self, id: str, elapsed_seconds: float) -> None:

    # Task executions
    def create_task_execution(self, flow_run_id: str, node_name: str, node_type: str,
                              generation: int, prompt_text: str) -> str:
    def get_task_execution(self, id: str) -> TaskExecutionRow | None:
    def list_task_executions(self, flow_run_id: str) -> list[TaskExecutionRow]:
    def get_pending_tasks(self, flow_run_id: str) -> list[TaskExecutionRow]:
    def update_task_status(self, id: str, status: str, **kwargs) -> None:
        # kwargs: claude_session_id, started_at, completed_at, elapsed_seconds,
        #         exit_code, output_summary, error_message

    # Edge transitions
    def create_edge_transition(self, flow_run_id: str, from_task_id: str,
                               to_task_id: str | None, edge_type: str,
                               condition_text: str | None = None,
                               judge_session_id: str | None = None,
                               judge_decision: str | None = None,
                               judge_reasoning: str | None = None,
                               judge_confidence: float | None = None) -> str:

    # Fork groups
    def create_fork_group(self, flow_run_id: str, source_task_id: str,
                          join_node_name: str, generation: int,
                          member_task_ids: list[str]) -> str:
    def get_fork_group(self, id: str) -> ForkGroupRow | None:
    def get_active_fork_groups(self, flow_run_id: str) -> list[ForkGroupRow]:
    def get_fork_group_members(self, fork_group_id: str) -> list[TaskExecutionRow]:
    def update_fork_group_status(self, id: str, status: str) -> None:

    # Task logs
    def insert_task_log(self, task_execution_id: str, log_type: str, content: str) -> None:
    def get_task_logs(self, task_execution_id: str, after_timestamp: str | None = None,
                      limit: int = 1000) -> list[TaskLogRow]:

    # Recovery
    def get_running_flow_runs(self) -> list[FlowRunRow]:
    def get_running_tasks(self, flow_run_id: str) -> list[TaskExecutionRow]:
```

## Pydantic Models

Define in `models.py`. One model per table, matching column names:

```python
class FlowRunRow(BaseModel):
    id: str
    flow_definition_id: str
    status: str
    workspace_path: str
    params_json: str | None
    budget_seconds: int
    elapsed_seconds: float
    on_error: str
    started_at: str | None
    completed_at: str | None
    created_at: str
    error_message: str | None
```

(Similar for all other tables.)

## SQLite Schema

Implement exactly the schema from Section 8.1 of specs.md. Copy the CREATE TABLE statements into `schema.sql`. The `database.py` module reads this file and executes it on first connection.

Key configuration:
- **WAL mode**: `PRAGMA journal_mode=WAL;`
- **Busy timeout**: `PRAGMA busy_timeout=5000;`
- **Foreign keys**: `PRAGMA foreign_keys=ON;`

## Transaction Boundaries

Follow Section 8.2:
- Task status change + edge creation = single transaction
- Fork group creation + all member inserts = single transaction
- Log insertion = individual transactions (high frequency)
- Flow status change = single transaction with elapsed_seconds update

Use Python context managers for transactions:
```python
def update_task_and_create_edge(self, task_id, status, edge_data):
    with self._transaction():
        self.update_task_status(task_id, status, ...)
        self.create_edge_transition(...)
```

## Testing Requirements

### `test_repository.py`
- Use **in-memory SQLite** (`":memory:"`) for all tests — fast, no cleanup needed
- Test all CRUD operations for each table
- Test transaction atomicity: if the second operation in a compound transaction fails, the first should be rolled back
- Test WAL mode is enabled
- Test foreign key constraints (e.g., can't create task_execution with non-existent flow_run_id)
- Test the recovery methods: create a "running" flow run, then verify `get_running_flow_runs()` finds it
- Test log insertion and retrieval with timestamp filtering
- Test fork group creation with members and completion checking

## Key Constraints

1. **Use standard library `sqlite3` only.** No SQLAlchemy, no aiosqlite (the execution engine will call this synchronously from an asyncio context using `run_in_executor` if needed — that's the engine's problem, not yours).
2. **All IDs are UUIDs** generated via `uuid.uuid4()` and stored as TEXT.
3. **Timestamps** stored as ISO 8601 strings (`datetime.utcnow().isoformat()`).
4. **Thread safety**: The `FlowstateDB` class does NOT need to be thread-safe. The execution engine ensures single-writer access. WAL mode handles concurrent reads from the web server.
5. **Use `pytest` for all tests.**
