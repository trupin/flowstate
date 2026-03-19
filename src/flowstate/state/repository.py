"""Repository for Flowstate database operations.

Provides FlowstateDB, the main class for all CRUD operations on flow definitions,
flow runs, task executions, and edge transitions. Delegates connection setup to
database.py and wraps all queries with Pydantic model conversion.
"""

import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from flowstate.state.database import FlowstateDB as _DatabaseBase
from flowstate.state.models import (
    FlowDefinitionRow,
    FlowRunRow,
    TaskExecutionRow,
)


class FlowstateDB:
    """SQLite repository for all Flowstate CRUD operations.

    Wraps a SQLite connection (set up by database.py) and provides methods for
    flow definitions, flow runs, task executions, and edge transitions. Not
    thread-safe by design -- the execution engine ensures single-writer access.
    """

    def __init__(self, db_path: str = "~/.flowstate/flowstate.db") -> None:
        """Open or create the database and initialize the schema.

        Args:
            db_path: Path to the SQLite database file. Use ":memory:" for
                in-memory databases (tests). Defaults to ~/.flowstate/flowstate.db.
        """
        self._db = _DatabaseBase(db_path)
        self._conn = self._db.connection
        self._in_transaction = False

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        """Context manager for explicit transactions.

        Usage:
            with self._transaction():
                self._execute(...)
                self._execute(...)

        If an exception occurs, the transaction is rolled back.
        Individual method commits are suppressed inside the transaction.
        """
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

    def _execute(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def _fetchone(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()  # type: ignore[return-value]

    def _fetchall(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the underlying sqlite3 connection."""
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    def __enter__(self) -> "FlowstateDB":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.close()

    # ================================================================== #
    # Flow Definitions
    # ================================================================== #

    def create_flow_definition(self, name: str, source_dsl: str, ast_json: str) -> str:
        """Insert a new flow definition and return its UUID."""
        id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self._execute(
            "INSERT INTO flow_definitions (id, name, source_dsl, ast_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (id, name, source_dsl, ast_json, now, now),
        )
        self._commit()
        return id

    def get_flow_definition(self, id: str) -> FlowDefinitionRow | None:
        """Retrieve a flow definition by ID, or None if not found."""
        row = self._fetchone("SELECT * FROM flow_definitions WHERE id = ?", (id,))
        return FlowDefinitionRow(**dict(row)) if row else None

    def get_flow_definition_by_name(self, name: str) -> FlowDefinitionRow | None:
        """Retrieve a flow definition by its unique name, or None if not found."""
        row = self._fetchone("SELECT * FROM flow_definitions WHERE name = ?", (name,))
        return FlowDefinitionRow(**dict(row)) if row else None

    def list_flow_definitions(self) -> list[FlowDefinitionRow]:
        """List all flow definitions, ordered by creation date descending."""
        rows = self._fetchall("SELECT * FROM flow_definitions ORDER BY created_at DESC")
        return [FlowDefinitionRow(**dict(r)) for r in rows]

    def update_flow_definition(self, id: str, source_dsl: str, ast_json: str) -> None:
        """Update the source DSL and AST JSON of an existing flow definition."""
        now = datetime.now(UTC).isoformat()
        self._execute(
            "UPDATE flow_definitions SET source_dsl = ?, ast_json = ?, updated_at = ? WHERE id = ?",
            (source_dsl, ast_json, now, id),
        )
        self._commit()

    def delete_flow_definition(self, id: str) -> None:
        """Delete a flow definition by ID. No-op if the ID does not exist."""
        self._execute("DELETE FROM flow_definitions WHERE id = ?", (id,))
        self._commit()

    # ================================================================== #
    # Flow Runs
    # ================================================================== #

    def create_flow_run(
        self,
        flow_definition_id: str,
        data_dir: str,
        budget_seconds: int,
        on_error: str,
        default_workspace: str | None = None,
        params_json: str | None = None,
    ) -> str:
        """Create a new flow run with status 'created' and return its UUID."""
        id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self._execute(
            """INSERT INTO flow_runs
               (id, flow_definition_id, status, default_workspace, data_dir, params_json,
                budget_seconds, elapsed_seconds, on_error, created_at)
               VALUES (?, ?, 'created', ?, ?, ?, ?, 0, ?, ?)""",
            (
                id,
                flow_definition_id,
                default_workspace,
                data_dir,
                params_json,
                budget_seconds,
                on_error,
                now,
            ),
        )
        self._commit()
        return id

    def get_flow_run(self, id: str) -> FlowRunRow | None:
        """Retrieve a flow run by ID, or None if not found."""
        row = self._fetchone("SELECT * FROM flow_runs WHERE id = ?", (id,))
        return FlowRunRow(**dict(row)) if row else None

    def list_flow_runs(self, status: str | None = None) -> list[FlowRunRow]:
        """List flow runs, optionally filtered by status."""
        if status:
            rows = self._fetchall(
                "SELECT * FROM flow_runs WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            rows = self._fetchall("SELECT * FROM flow_runs ORDER BY created_at DESC")
        return [FlowRunRow(**dict(r)) for r in rows]

    def update_flow_run_status(
        self, id: str, status: str, error_message: str | None = None
    ) -> None:
        """Update flow run status, setting timestamps for terminal/running states."""
        now = datetime.now(UTC).isoformat()
        terminal = {"completed", "failed", "cancelled", "budget_exceeded"}
        if status in terminal:
            self._execute(
                "UPDATE flow_runs SET status = ?, completed_at = ?, error_message = ? WHERE id = ?",
                (status, now, error_message, id),
            )
        elif status == "running":
            self._execute(
                "UPDATE flow_runs SET status = ?, started_at = COALESCE(started_at, ?),"
                " error_message = ? WHERE id = ?",
                (status, now, error_message, id),
            )
        else:
            self._execute(
                "UPDATE flow_runs SET status = ?, error_message = ? WHERE id = ?",
                (status, error_message, id),
            )
        self._commit()

    def update_flow_run_elapsed(self, id: str, elapsed_seconds: float) -> None:
        """Update only the elapsed_seconds column of a flow run."""
        self._execute(
            "UPDATE flow_runs SET elapsed_seconds = ? WHERE id = ?",
            (elapsed_seconds, id),
        )
        self._commit()

    # ================================================================== #
    # Task Executions
    # ================================================================== #

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
        """Create a new task execution with status 'pending' and return its UUID."""
        id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self._execute(
            """INSERT INTO task_executions
               (id, flow_run_id, node_name, node_type, status, generation,
                context_mode, cwd, task_dir, prompt_text, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
            (
                id,
                flow_run_id,
                node_name,
                node_type,
                generation,
                context_mode,
                cwd,
                task_dir,
                prompt_text,
                now,
            ),
        )
        self._commit()
        return id

    def get_task_execution(self, id: str) -> TaskExecutionRow | None:
        """Retrieve a task execution by ID, or None if not found."""
        row = self._fetchone("SELECT * FROM task_executions WHERE id = ?", (id,))
        return TaskExecutionRow(**dict(row)) if row else None

    def list_task_executions(self, flow_run_id: str) -> list[TaskExecutionRow]:
        """List all task executions for a given flow run, ordered by creation time."""
        rows = self._fetchall(
            "SELECT * FROM task_executions WHERE flow_run_id = ? ORDER BY created_at",
            (flow_run_id,),
        )
        return [TaskExecutionRow(**dict(r)) for r in rows]

    def get_pending_tasks(self, flow_run_id: str) -> list[TaskExecutionRow]:
        """Return tasks with status 'pending' for a given flow run."""
        rows = self._fetchall(
            "SELECT * FROM task_executions WHERE flow_run_id = ? AND status = 'pending'"
            " ORDER BY created_at",
            (flow_run_id,),
        )
        return [TaskExecutionRow(**dict(r)) for r in rows]

    def update_task_status(self, id: str, status: str, **kwargs: object) -> None:
        """Update task status and any additional fields.

        Accepted kwargs: claude_session_id, started_at, completed_at,
        elapsed_seconds, exit_code, summary_path, error_message, wait_until

        Raises:
            ValueError: If an unknown kwarg is passed.
        """
        allowed = {
            "claude_session_id",
            "started_at",
            "completed_at",
            "elapsed_seconds",
            "exit_code",
            "summary_path",
            "error_message",
            "wait_until",
        }
        updates: dict[str, object] = {"status": status}
        for key, value in kwargs.items():
            if key not in allowed:
                raise ValueError(f"Unknown task field: {key}")
            updates[key] = value

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = [*list(updates.values()), id]
        self._execute(
            f"UPDATE task_executions SET {set_clause} WHERE id = ?",
            tuple(values),
        )
        self._commit()

    # ================================================================== #
    # Edge Transitions
    # ================================================================== #

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
        """Create an edge transition record and return its UUID."""
        id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self._execute(
            """INSERT INTO edge_transitions
               (id, flow_run_id, from_task_id, to_task_id, edge_type,
                condition_text, judge_session_id, judge_decision,
                judge_reasoning, judge_confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                id,
                flow_run_id,
                from_task_id,
                to_task_id,
                edge_type,
                condition_text,
                judge_session_id,
                judge_decision,
                judge_reasoning,
                judge_confidence,
                now,
            ),
        )
        self._commit()
        return id
