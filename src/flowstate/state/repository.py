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
    EdgeTransitionRow,
    FlowDefinitionRow,
    FlowRunRow,
    FlowScheduleRow,
    ForkGroupRow,
    TaskExecutionRow,
    TaskLogRow,
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
        run_id: str | None = None,
    ) -> str:
        """Create a new flow run with status 'created' and return its UUID.

        If *run_id* is provided it is used as the primary key; otherwise a new
        UUID is generated.
        """
        id = run_id or str(uuid.uuid4())
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

    def update_flow_run_worktree(self, run_id: str, worktree_path: str) -> None:
        """Store the git worktree path for a flow run."""
        self._execute(
            "UPDATE flow_runs SET worktree_path = ? WHERE id = ?",
            (worktree_path, run_id),
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

    def get_latest_task_execution(self, flow_run_id: str) -> TaskExecutionRow | None:
        """Get the most recently created task execution for a flow run."""
        row = self._fetchone(
            "SELECT * FROM task_executions WHERE flow_run_id = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (flow_run_id,),
        )
        return TaskExecutionRow(**dict(row)) if row else None

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

    def list_edge_transitions(self, flow_run_id: str) -> list[EdgeTransitionRow]:
        """List all edge transitions for a given flow run, ordered by creation time."""
        rows = self._fetchall(
            "SELECT * FROM edge_transitions WHERE flow_run_id = ? ORDER BY created_at",
            (flow_run_id,),
        )
        return [EdgeTransitionRow(**dict(r)) for r in rows]

    # ================================================================== #
    # Fork Groups
    # ================================================================== #

    def create_fork_group(
        self,
        flow_run_id: str,
        source_task_id: str,
        join_node_name: str,
        generation: int,
        member_task_ids: list[str],
    ) -> str:
        """Create a fork group and all its members atomically.

        The fork group row and all fork_group_members rows are inserted in a
        single transaction. If any member insert fails (e.g., invalid task ID),
        the entire group is rolled back.

        Args:
            flow_run_id: The flow run this fork group belongs to.
            source_task_id: The task that triggered the fork.
            join_node_name: The node where forked branches rejoin.
            generation: The generation number.
            member_task_ids: Task execution IDs that are members of this fork group.

        Returns:
            The UUID of the newly created fork group.
        """
        id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self._transaction():
            self._execute(
                """INSERT INTO fork_groups
                   (id, flow_run_id, source_task_id, join_node_name, generation, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?)""",
                (id, flow_run_id, source_task_id, join_node_name, generation, now),
            )
            for task_id in member_task_ids:
                self._execute(
                    "INSERT INTO fork_group_members (fork_group_id, task_execution_id)"
                    " VALUES (?, ?)",
                    (id, task_id),
                )
        return id

    def get_fork_group(self, id: str) -> ForkGroupRow | None:
        """Retrieve a fork group by ID, or None if not found."""
        row = self._fetchone("SELECT * FROM fork_groups WHERE id = ?", (id,))
        return ForkGroupRow(**dict(row)) if row else None

    def get_active_fork_groups(self, flow_run_id: str) -> list[ForkGroupRow]:
        """Return fork groups with status 'active' for a given flow run."""
        rows = self._fetchall(
            "SELECT * FROM fork_groups WHERE flow_run_id = ? AND status = 'active'"
            " ORDER BY created_at",
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
        """Update the status of a fork group (e.g., 'active' -> 'joined')."""
        self._execute(
            "UPDATE fork_groups SET status = ? WHERE id = ?",
            (status, id),
        )
        self._commit()

    # ================================================================== #
    # Task Logs
    # ================================================================== #

    def insert_task_log(self, task_execution_id: str, log_type: str, content: str) -> None:
        """Insert a log entry. Uses individual transaction (high frequency, loss acceptable).

        The timestamp column uses DEFAULT CURRENT_TIMESTAMP, so SQLite sets it
        automatically. This avoids clock skew between Python and SQLite.
        """
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

        Logs are ordered by (timestamp ASC, id ASC). The id tiebreaker is
        important because multiple entries can share the same timestamp
        (CURRENT_TIMESTAMP has second-level precision).

        Args:
            task_execution_id: The task to get logs for.
            after_timestamp: If provided, only return logs with timestamp > this value.
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

    # ================================================================== #
    # Flow Schedules
    # ================================================================== #

    def create_flow_schedule(
        self,
        flow_definition_id: str,
        cron_expression: str,
        on_overlap: str = "skip",
        next_trigger_at: str | None = None,
    ) -> str:
        """Create a new flow schedule and return its UUID.

        Args:
            flow_definition_id: The flow definition to schedule.
            cron_expression: Cron expression for recurring execution.
            on_overlap: Overlap policy ('skip', 'queue', or 'parallel'). Defaults to 'skip'.
            next_trigger_at: ISO 8601 timestamp for the next trigger. Defaults to None.

        Returns:
            The UUID of the newly created schedule.
        """
        id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self._execute(
            """INSERT INTO flow_schedules
               (id, flow_definition_id, cron_expression, on_overlap, enabled, next_trigger_at,
                created_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (id, flow_definition_id, cron_expression, on_overlap, next_trigger_at, now),
        )
        self._commit()
        return id

    def get_flow_schedule(self, id: str) -> FlowScheduleRow | None:
        """Retrieve a flow schedule by ID, or None if not found."""
        row = self._fetchone("SELECT * FROM flow_schedules WHERE id = ?", (id,))
        return FlowScheduleRow(**dict(row)) if row else None

    def list_flow_schedules(self, flow_definition_id: str | None = None) -> list[FlowScheduleRow]:
        """List all flow schedules, optionally filtered by flow_definition_id."""
        if flow_definition_id:
            rows = self._fetchall(
                "SELECT * FROM flow_schedules WHERE flow_definition_id = ? ORDER BY created_at",
                (flow_definition_id,),
            )
        else:
            rows = self._fetchall("SELECT * FROM flow_schedules ORDER BY created_at")
        return [FlowScheduleRow(**dict(r)) for r in rows]

    def update_flow_schedule(self, id: str, **kwargs: object) -> None:
        """Update mutable schedule fields.

        Accepted kwargs: cron_expression, on_overlap, enabled,
        last_triggered_at, next_trigger_at

        Raises:
            ValueError: If an unknown kwarg is passed.
        """
        allowed = {
            "cron_expression",
            "on_overlap",
            "enabled",
            "last_triggered_at",
            "next_trigger_at",
        }
        updates: dict[str, object] = {}
        for key, value in kwargs.items():
            if key not in allowed:
                raise ValueError(f"Unknown schedule field: {key}")
            updates[key] = value
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = [*list(updates.values()), id]
        self._execute(
            f"UPDATE flow_schedules SET {set_clause} WHERE id = ?",
            tuple(values),
        )
        self._commit()

    def delete_flow_schedule(self, id: str) -> None:
        """Delete a flow schedule by ID. No-op if the ID does not exist."""
        self._execute("DELETE FROM flow_schedules WHERE id = ?", (id,))
        self._commit()

    def get_due_schedules(self, now: str | None = None) -> list[FlowScheduleRow]:
        """Get enabled schedules whose next_trigger_at is at or before the given time.

        Args:
            now: ISO 8601 timestamp. Defaults to current UTC time.
        """
        if now is None:
            now = datetime.now(UTC).isoformat()
        rows = self._fetchall(
            """SELECT * FROM flow_schedules
               WHERE enabled = 1 AND next_trigger_at IS NOT NULL AND next_trigger_at <= ?
               ORDER BY next_trigger_at ASC""",
            (now,),
        )
        return [FlowScheduleRow(**dict(r)) for r in rows]

    # ================================================================== #
    # Recovery
    # ================================================================== #

    def get_running_flow_runs(self) -> list[FlowRunRow]:
        """Find flow runs with status 'running'. Used for crash recovery."""
        rows = self._fetchall(
            "SELECT * FROM flow_runs WHERE status = 'running' ORDER BY created_at"
        )
        return [FlowRunRow(**dict(r)) for r in rows]

    def get_running_tasks(self, flow_run_id: str) -> list[TaskExecutionRow]:
        """Find task executions with status 'running' for a given flow run.

        Used for crash recovery to detect orphaned tasks.
        """
        rows = self._fetchall(
            "SELECT * FROM task_executions WHERE flow_run_id = ? AND status = 'running'"
            " ORDER BY created_at",
            (flow_run_id,),
        )
        return [TaskExecutionRow(**dict(r)) for r in rows]

    # ================================================================== #
    # Waiting Tasks
    # ================================================================== #

    def get_waiting_tasks(self, flow_run_id: str, now: str | None = None) -> list[TaskExecutionRow]:
        """Find tasks with status 'waiting' whose wait_until has passed.

        Delayed edges set wait_until on a task and status to 'waiting'. The
        engine periodically checks for tasks ready to execute.

        Args:
            flow_run_id: The flow run to check.
            now: ISO 8601 timestamp. Defaults to current UTC time.
        """
        if now is None:
            now = datetime.now(UTC).isoformat()
        rows = self._fetchall(
            """SELECT * FROM task_executions
               WHERE flow_run_id = ? AND status = 'waiting' AND wait_until <= ?
               ORDER BY wait_until ASC""",
            (flow_run_id, now),
        )
        return [TaskExecutionRow(**dict(r)) for r in rows]
