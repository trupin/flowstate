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
    AgentSubtaskRow,
    EdgeTransitionRow,
    FlowDefinitionRow,
    FlowRunRow,
    FlowScheduleRow,
    ForkGroupRow,
    TaskArtifactRow,
    TaskExecutionRow,
    TaskLogRow,
    TaskMessageRow,
    TaskNodeHistoryRow,
    TaskRow,
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

    def reset_all(self) -> None:
        """Delete all rows from all tables. Used in tests only."""
        tables = [
            "task_messages",
            "task_logs",
            "task_artifacts",
            "agent_subtasks",
            "edge_transitions",
            "fork_group_members",
            "fork_groups",
            "task_node_history",
            "task_executions",
            "flow_runs",
            "tasks",
            "flow_schedules",
            "flow_enabled",
            "flow_definitions",
        ]
        for table in tables:
            self._conn.execute(f"DELETE FROM {table}")
        self._conn.commit()

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
            "SELECT * FROM task_executions WHERE flow_run_id = ? ORDER BY created_at DESC LIMIT 1",
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
    # Task Messages
    # ================================================================== #

    def insert_task_message(self, task_execution_id: str, message: str) -> str:
        """Insert a user message into the task message queue.

        Args:
            task_execution_id: The task execution to queue the message for.
            message: The message text.

        Returns:
            The UUID of the newly created message.
        """
        msg_id = str(uuid.uuid4())
        self._execute(
            """INSERT INTO task_messages (id, task_execution_id, message)
               VALUES (?, ?, ?)""",
            (msg_id, task_execution_id, message),
        )
        self._commit()
        return msg_id

    def get_unprocessed_messages(self, task_execution_id: str) -> list[TaskMessageRow]:
        """Get unprocessed messages for a task execution, ordered by creation time.

        Args:
            task_execution_id: The task execution to get messages for.

        Returns:
            List of unprocessed messages ordered by created_at ascending.
        """
        rows = self._fetchall(
            """SELECT * FROM task_messages
               WHERE task_execution_id = ? AND processed = 0
               ORDER BY created_at ASC""",
            (task_execution_id,),
        )
        return [TaskMessageRow(**dict(r)) for r in rows]

    def mark_messages_processed(self, task_execution_id: str) -> int:
        """Mark all unprocessed messages for a task execution as processed.

        Args:
            task_execution_id: The task execution whose messages to mark.

        Returns:
            The number of messages that were marked as processed.
        """
        cursor = self._execute(
            """UPDATE task_messages SET processed = 1
               WHERE task_execution_id = ? AND processed = 0""",
            (task_execution_id,),
        )
        self._commit()
        return cursor.rowcount

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

    # ================================================================== #
    # Tasks (Queue Work Items)
    # ================================================================== #

    def create_task(
        self,
        flow_name: str,
        title: str,
        description: str | None = None,
        params_json: str | None = None,
        parent_task_id: str | None = None,
        created_by: str | None = None,
        priority: int = 0,
        scheduled_at: str | None = None,
        cron_expression: str | None = None,
    ) -> str:
        """Create a new task and return its UUID.

        When ``scheduled_at`` is provided the task starts in ``'scheduled'``
        status; otherwise it starts as ``'queued'`` for immediate processing.

        Args:
            flow_name: The flow this task should be processed by.
            title: Human-readable task title.
            description: Optional detailed description.
            params_json: Optional JSON string of task-specific parameters.
            parent_task_id: Optional parent task ID for cross-flow lineage.
            created_by: Who created this task (e.g. "user" or "flow:X/node:Y").
            priority: Priority level (higher = processed first). Defaults to 0.
            scheduled_at: ISO-8601 timestamp for deferred execution (optional).
                When set the task status is ``'scheduled'`` instead of ``'queued'``.
            cron_expression: Cron expression for recurring tasks (optional).

        Returns:
            The UUID of the newly created task.
        """
        task_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        status = "scheduled" if scheduled_at else "queued"

        # Compute depth from parent chain (0 for root tasks)
        depth = 0
        if parent_task_id:
            parent = self.get_task(parent_task_id)
            if parent:
                depth = parent.depth + 1

        self._execute(
            """INSERT INTO tasks
               (id, flow_name, title, description, status, params_json,
                parent_task_id, created_by, priority, depth,
                scheduled_at, cron_expression, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                flow_name,
                title,
                description,
                status,
                params_json,
                parent_task_id,
                created_by,
                priority,
                depth,
                scheduled_at,
                cron_expression,
                now,
            ),
        )
        self._commit()
        return task_id

    def get_task(self, task_id: str) -> TaskRow | None:
        """Retrieve a task by ID, or None if not found."""
        row = self._fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return TaskRow(**dict(row)) if row else None

    def list_tasks(
        self,
        flow_name: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[TaskRow]:
        """List tasks with optional filters.

        Args:
            flow_name: Filter by flow name (optional).
            status: Filter by status (optional).
            limit: Maximum number of tasks to return (optional).

        Returns:
            List of tasks ordered by created_at descending.
        """
        conditions: list[str] = []
        params: list[object] = []
        if flow_name is not None:
            conditions.append("flow_name = ?")
            params.append(flow_name)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM tasks{where} ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        rows = self._fetchall(sql, tuple(params))
        return [TaskRow(**dict(r)) for r in rows]

    def update_task_queue_status(
        self,
        task_id: str,
        status: str,
        *,
        current_node: str | None = None,
        flow_run_id: str | None = None,
        output_json: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update a task's status and optional related fields.

        Named update_task_queue_status to avoid collision with the existing
        update_task_status method (which operates on the task_executions table).

        Automatically sets started_at when transitioning to 'running' and
        completed_at when transitioning to a terminal status.

        Args:
            task_id: The task to update.
            status: New status value.
            current_node: Current node the task is at (optional).
            flow_run_id: Associated flow run ID (optional).
            output_json: JSON string of task output (optional).
            error_message: Error message for failed tasks (optional).
        """
        now = datetime.now(UTC).isoformat()
        updates: dict[str, object] = {"status": status}

        if current_node is not None:
            updates["current_node"] = current_node
        if flow_run_id is not None:
            updates["flow_run_id"] = flow_run_id
        if output_json is not None:
            updates["output_json"] = output_json
        if error_message is not None:
            updates["error_message"] = error_message

        terminal = {"completed", "failed", "cancelled"}
        if status == "running":
            updates["started_at"] = now
        if status in terminal:
            updates["completed_at"] = now

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = [*list(updates.values()), task_id]
        self._execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?",
            tuple(values),
        )
        self._commit()

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        params_json: str | None = None,
        priority: int | None = None,
    ) -> None:
        """Update mutable fields of a task (typically while still queued).

        Args:
            task_id: The task to update.
            title: New title (optional).
            description: New description (optional).
            params_json: New params JSON (optional).
            priority: New priority (optional).
        """
        updates: dict[str, object] = {}
        if title is not None:
            updates["title"] = title
        if description is not None:
            updates["description"] = description
        if params_json is not None:
            updates["params_json"] = params_json
        if priority is not None:
            updates["priority"] = priority
        if not updates:
            return

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = [*list(updates.values()), task_id]
        self._execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?",
            tuple(values),
        )
        self._commit()

    def delete_task(self, task_id: str) -> None:
        """Delete a task, but only if it is still queued or scheduled.

        No-op if the task does not exist or is not in 'queued'/'scheduled' status.
        """
        self._execute(
            "DELETE FROM tasks WHERE id = ? AND status IN ('queued', 'scheduled')",
            (task_id,),
        )
        self._commit()

    # ------------------------------------------------------------------ #
    # Queue Operations
    # ------------------------------------------------------------------ #

    def list_queued_flow_names(self) -> list[str]:
        """Return distinct flow names that have at least one processable task.

        A task is processable if it is ``'queued'`` or if it is ``'scheduled'``
        with ``scheduled_at <= now`` (i.e. its deferred time has arrived).
        """
        now = datetime.now(UTC).isoformat()
        rows = self._fetchall(
            """SELECT DISTINCT flow_name FROM tasks
               WHERE status = 'queued'
                  OR (status = 'scheduled' AND scheduled_at <= ?)""",
            (now,),
        )
        return [row["flow_name"] for row in rows]

    def get_next_queued_task(self, flow_name: str) -> TaskRow | None:
        """Get the highest-priority oldest processable task for a flow.

        A task is processable if it is ``'queued'`` or ``'scheduled'`` with
        ``scheduled_at <= now``.  Returns the next task ordered by priority
        DESC (higher first) then created_at ASC (oldest first).
        """
        now = datetime.now(UTC).isoformat()
        row = self._fetchone(
            """SELECT * FROM tasks
               WHERE flow_name = ?
                 AND (status = 'queued'
                      OR (status = 'scheduled' AND scheduled_at <= ?))
               ORDER BY priority DESC, created_at ASC
               LIMIT 1""",
            (flow_name, now),
        )
        return TaskRow(**dict(row)) if row else None

    def count_running_tasks(self, flow_name: str) -> int:
        """Count tasks with status 'running' for a given flow.

        Used by the queue manager to check capacity before starting new tasks.
        """
        row = self._fetchone(
            "SELECT COUNT(*) as cnt FROM tasks WHERE flow_name = ? AND status = 'running'",
            (flow_name,),
        )
        return int(row["cnt"]) if row else 0

    def reorder_tasks(self, flow_name: str, task_ids: list[str]) -> None:
        """Reorder queued tasks by assigning priority based on list position.

        The first task in the list gets the highest priority (len - 1),
        the last gets priority 0. Only updates tasks that are still queued
        and belong to the specified flow.

        Args:
            flow_name: The flow whose tasks to reorder.
            task_ids: Task IDs in desired processing order (first = next).
        """
        with self._transaction():
            for idx, tid in enumerate(task_ids):
                priority = len(task_ids) - 1 - idx
                self._execute(
                    "UPDATE tasks SET priority = ? WHERE id = ? AND flow_name = ?"
                    " AND status = 'queued'",
                    (priority, tid, flow_name),
                )

    # ------------------------------------------------------------------ #
    # Scheduled Tasks
    # ------------------------------------------------------------------ #

    def get_due_scheduled_tasks(self) -> list[TaskRow]:
        """Return tasks with status ``'scheduled'`` whose ``scheduled_at <= now``.

        Results are ordered by ``scheduled_at ASC`` so the oldest-due tasks
        are processed first.
        """
        now = datetime.now(UTC).isoformat()
        rows = self._fetchall(
            "SELECT * FROM tasks WHERE status = 'scheduled' AND scheduled_at <= ?"
            " ORDER BY scheduled_at ASC",
            (now,),
        )
        return [TaskRow(**dict(r)) for r in rows]

    def create_next_recurring_task(self, task: TaskRow) -> str | None:
        """Create the next occurrence of a recurring task.

        Uses the task's ``cron_expression`` to compute the next ``scheduled_at``
        time relative to *now*.

        Args:
            task: The recurring task whose next occurrence should be created.

        Returns:
            The UUID of the newly created task, or ``None`` if the task has no
            ``cron_expression``.
        """
        if not task.cron_expression:
            return None

        from croniter import croniter

        now = datetime.now(UTC)
        cron = croniter(task.cron_expression, now)
        next_time: datetime = cron.get_next(datetime)

        return self.create_task(
            flow_name=task.flow_name,
            title=task.title,
            description=task.description,
            params_json=task.params_json,
            parent_task_id=task.parent_task_id,
            created_by=task.created_by or "recurring",
            priority=task.priority,
            scheduled_at=next_time.isoformat(),
            cron_expression=task.cron_expression,
        )

    # ------------------------------------------------------------------ #
    # Task Node History
    # ------------------------------------------------------------------ #

    def add_task_node_history(
        self,
        task_id: str,
        node_name: str,
        flow_run_id: str | None = None,
    ) -> int:
        """Record that a task entered a node. Returns the history entry ID.

        Args:
            task_id: The task that entered the node.
            node_name: The name of the node entered.
            flow_run_id: Optional flow run ID associated with this node execution.

        Returns:
            The integer ID of the new history row.
        """
        now = datetime.now(UTC).isoformat()
        cursor = self._execute(
            """INSERT INTO task_node_history (task_id, node_name, flow_run_id, started_at)
               VALUES (?, ?, ?, ?)""",
            (task_id, node_name, flow_run_id, now),
        )
        self._commit()
        return cursor.lastrowid or 0

    def complete_task_node_history(self, task_id: str, node_name: str) -> None:
        """Mark the most recent uncompleted history entry for this task+node as completed.

        Sets completed_at to the current UTC time on the latest entry where
        completed_at IS NULL for the given task_id and node_name.
        """
        now = datetime.now(UTC).isoformat()
        self._execute(
            """UPDATE task_node_history SET completed_at = ?
               WHERE id = (
                   SELECT id FROM task_node_history
                   WHERE task_id = ? AND node_name = ? AND completed_at IS NULL
                   ORDER BY started_at DESC LIMIT 1
               )""",
            (now, task_id, node_name),
        )
        self._commit()

    def get_task_history(self, task_id: str) -> list[TaskNodeHistoryRow]:
        """Get the full node history for a task, ordered by start time.

        Args:
            task_id: The task to get history for.

        Returns:
            List of history entries ordered by started_at ascending.
        """
        rows = self._fetchall(
            "SELECT * FROM task_node_history WHERE task_id = ? ORDER BY started_at ASC, id ASC",
            (task_id,),
        )
        return [TaskNodeHistoryRow(**dict(r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Task Lineage
    # ------------------------------------------------------------------ #

    def get_child_tasks(self, parent_task_id: str) -> list[TaskRow]:
        """Get all tasks that were filed by a given parent task.

        Args:
            parent_task_id: The parent task ID.

        Returns:
            List of child tasks ordered by created_at ascending.
        """
        rows = self._fetchall(
            "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY created_at ASC",
            (parent_task_id,),
        )
        return [TaskRow(**dict(r)) for r in rows]

    # ================================================================== #
    # Flow Enable/Disable
    # ================================================================== #

    def set_flow_enabled(self, flow_name: str, enabled: bool) -> None:
        """Enable or disable a flow's task queue processing.

        Uses upsert semantics: creates the row if it doesn't exist, updates if it does.

        Args:
            flow_name: The flow to enable or disable.
            enabled: True to enable, False to disable.
        """
        self._execute(
            "INSERT INTO flow_enabled (flow_name, enabled) VALUES (?, ?) "
            "ON CONFLICT(flow_name) DO UPDATE SET enabled = ?",
            (flow_name, int(enabled), int(enabled)),
        )
        self._commit()

    def is_flow_enabled(self, flow_name: str) -> bool:
        """Check if a flow is enabled for task processing.

        Returns True by default if no row exists (flows are enabled unless
        explicitly disabled).

        Args:
            flow_name: The flow to check.

        Returns:
            True if the flow is enabled, False if disabled.
        """
        row = self._fetchone("SELECT enabled FROM flow_enabled WHERE flow_name = ?", (flow_name,))
        return bool(row["enabled"]) if row else True

    # ================================================================== #
    # Agent Subtasks
    # ================================================================== #

    def create_agent_subtask(self, task_execution_id: str, title: str) -> AgentSubtaskRow:
        """Create a new agent subtask with status 'todo'.

        Args:
            task_execution_id: The parent task execution ID.
            title: Short description of the subtask.

        Returns:
            The newly created subtask row.
        """
        subtask_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self._execute(
            "INSERT INTO agent_subtasks (id, task_execution_id, title, status, created_at, updated_at)"
            " VALUES (?, ?, ?, 'todo', ?, ?)",
            (subtask_id, task_execution_id, title, now, now),
        )
        self._commit()
        return AgentSubtaskRow(
            id=subtask_id,
            task_execution_id=task_execution_id,
            title=title,
            status="todo",
            created_at=now,
            updated_at=now,
        )

    def get_agent_subtask(self, subtask_id: str) -> AgentSubtaskRow | None:
        """Retrieve a single agent subtask by ID, or None if not found."""
        row = self._fetchone("SELECT * FROM agent_subtasks WHERE id = ?", (subtask_id,))
        return AgentSubtaskRow(**dict(row)) if row else None

    def list_agent_subtasks(self, task_execution_id: str) -> list[AgentSubtaskRow]:
        """List all subtasks for a task execution, ordered by creation time.

        Args:
            task_execution_id: The parent task execution ID.

        Returns:
            List of subtask rows, ordered by created_at ascending.
        """
        rows = self._fetchall(
            "SELECT * FROM agent_subtasks WHERE task_execution_id = ? ORDER BY created_at ASC",
            (task_execution_id,),
        )
        return [AgentSubtaskRow(**dict(r)) for r in rows]

    def count_agent_subtasks(self, task_execution_id: str) -> int:
        """Return the number of subtasks for a task execution."""
        row = self._fetchone(
            "SELECT COUNT(*) AS cnt FROM agent_subtasks WHERE task_execution_id = ?",
            (task_execution_id,),
        )
        return int(row["cnt"]) if row else 0

    def update_agent_subtask(self, subtask_id: str, status: str) -> AgentSubtaskRow | None:
        """Update the status of an agent subtask.

        Args:
            subtask_id: The subtask ID.
            status: New status ('todo', 'in_progress', or 'done').

        Returns:
            The updated subtask row, or None if the subtask was not found.
        """
        now = datetime.now(UTC).isoformat()
        self._execute(
            "UPDATE agent_subtasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, subtask_id),
        )
        self._commit()
        return self.get_agent_subtask(subtask_id)

    def complete_remaining_subtasks(self, task_execution_id: str) -> list[AgentSubtaskRow]:
        """Mark all todo/in_progress subtasks as done and return all subtasks.

        Used by the executor to auto-complete remaining subtasks when a task
        exits successfully (exit code 0). Subtasks already marked 'done' are
        unaffected by the UPDATE but included in the returned list.

        Args:
            task_execution_id: The parent task execution ID.

        Returns:
            All subtask rows for the task execution (after the update).
        """
        now = datetime.now(UTC).isoformat()
        self._execute(
            "UPDATE agent_subtasks SET status = 'done', updated_at = ? "
            "WHERE task_execution_id = ? AND status IN ('todo', 'in_progress')",
            (now, task_execution_id),
        )
        self._commit()
        return self.list_agent_subtasks(task_execution_id)

    # ================================================================== #
    # Task Artifacts
    # ================================================================== #

    def save_artifact(
        self,
        task_execution_id: str,
        name: str,
        content: str,
        content_type: str = "application/json",
    ) -> TaskArtifactRow:
        """Save or replace an artifact for a task execution.

        Uses INSERT OR REPLACE for upsert semantics: if an artifact with the
        same (task_execution_id, name) already exists, it is replaced entirely
        (new id, new created_at).

        Args:
            task_execution_id: The task execution this artifact belongs to.
            name: Artifact name (e.g. "decision", "summary", "output").
            content: Artifact content (typically JSON or Markdown).
            content_type: MIME type of the content (default "application/json").

        Returns:
            The saved TaskArtifactRow.
        """
        artifact_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self._execute(
            """INSERT OR REPLACE INTO task_artifacts
               (id, task_execution_id, name, content, content_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (artifact_id, task_execution_id, name, content, content_type, now),
        )
        self._commit()
        return TaskArtifactRow(
            id=artifact_id,
            task_execution_id=task_execution_id,
            name=name,
            content=content,
            content_type=content_type,
            created_at=now,
        )

    def get_artifact(self, task_execution_id: str, name: str) -> TaskArtifactRow | None:
        """Retrieve a single artifact by task execution ID and name.

        Args:
            task_execution_id: The task execution ID.
            name: The artifact name.

        Returns:
            The matching TaskArtifactRow, or None if not found.
        """
        row = self._fetchone(
            "SELECT * FROM task_artifacts WHERE task_execution_id = ? AND name = ?",
            (task_execution_id, name),
        )
        return TaskArtifactRow(**dict(row)) if row else None

    def list_artifacts(self, task_execution_id: str) -> list[TaskArtifactRow]:
        """List all artifacts for a task execution, ordered by creation time.

        Args:
            task_execution_id: The task execution ID.

        Returns:
            List of TaskArtifactRow ordered by created_at ascending.
        """
        rows = self._fetchall(
            "SELECT * FROM task_artifacts WHERE task_execution_id = ? ORDER BY created_at",
            (task_execution_id,),
        )
        return [TaskArtifactRow(**dict(r)) for r in rows]
