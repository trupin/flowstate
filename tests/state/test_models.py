"""Tests for Flowstate Pydantic row models.

Validates that all models can be constructed with required fields, that optional
fields default correctly, and that models can be built from sqlite3.Row dicts.
"""

import sqlite3
import uuid

import pytest
from pydantic import ValidationError

from flowstate.state.models import (
    EdgeTransitionRow,
    FlowDefinitionRow,
    FlowRunRow,
    FlowScheduleRow,
    ForkGroupMemberRow,
    ForkGroupRow,
    TaskExecutionRow,
    TaskLogRow,
)


def test_flow_definition_row_construction() -> None:
    """FlowDefinitionRow can be built with all required fields."""
    row = FlowDefinitionRow(
        id="def-1",
        name="my_flow",
        source_dsl="flow my_flow { }",
        ast_json='{"name": "my_flow"}',
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
    )
    assert row.id == "def-1"
    assert row.name == "my_flow"
    assert row.source_dsl == "flow my_flow { }"
    assert row.ast_json == '{"name": "my_flow"}'
    assert row.created_at == "2024-01-01T00:00:00"
    assert row.updated_at == "2024-01-01T00:00:00"


def test_flow_definition_row_missing_field() -> None:
    """FlowDefinitionRow raises ValidationError when a required field is missing."""
    with pytest.raises(ValidationError):
        FlowDefinitionRow(
            id="def-1",
            name="my_flow",
            # missing source_dsl, ast_json, created_at, updated_at
        )  # type: ignore[call-arg]


def test_flow_run_row_defaults() -> None:
    """FlowRunRow optional fields default to None, elapsed_seconds defaults to 0.0."""
    row = FlowRunRow(
        id="run-1",
        flow_definition_id="def-1",
        status="created",
        data_dir="/tmp/flowstate/run-1",
        budget_seconds=300,
        on_error="pause",
        created_at="2024-01-01T00:00:00",
    )
    assert row.default_workspace is None
    assert row.params_json is None
    assert row.elapsed_seconds == 0.0
    assert row.started_at is None
    assert row.completed_at is None
    assert row.error_message is None


def test_flow_run_row_all_fields() -> None:
    """FlowRunRow accepts all fields including optional ones."""
    row = FlowRunRow(
        id="run-1",
        flow_definition_id="def-1",
        status="running",
        default_workspace="/home/user/project",
        data_dir="/tmp/flowstate/run-1",
        params_json='{"key": "value"}',
        budget_seconds=600,
        elapsed_seconds=42.5,
        on_error="abort",
        started_at="2024-01-01T00:01:00",
        completed_at="2024-01-01T00:10:00",
        created_at="2024-01-01T00:00:00",
        error_message="something went wrong",
    )
    assert row.default_workspace == "/home/user/project"
    assert row.params_json == '{"key": "value"}'
    assert row.elapsed_seconds == 42.5
    assert row.started_at == "2024-01-01T00:01:00"
    assert row.completed_at == "2024-01-01T00:10:00"
    assert row.error_message == "something went wrong"


def test_task_execution_row_all_fields() -> None:
    """TaskExecutionRow accepts all fields including optional ones."""
    row = TaskExecutionRow(
        id="task-1",
        flow_run_id="run-1",
        node_name="build",
        node_type="task",
        status="completed",
        wait_until="2024-01-01T00:05:00",
        generation=2,
        context_mode="handoff",
        cwd="/home/user/project",
        claude_session_id="sess-123",
        task_dir="/tmp/flowstate/run-1/task-1",
        prompt_text="Build the project",
        started_at="2024-01-01T00:01:00",
        completed_at="2024-01-01T00:03:00",
        elapsed_seconds=120.5,
        exit_code=0,
        summary_path="/tmp/flowstate/run-1/task-1/summary.md",
        error_message=None,
        created_at="2024-01-01T00:00:00",
    )
    assert row.node_name == "build"
    assert row.generation == 2
    assert row.elapsed_seconds == 120.5
    assert row.exit_code == 0
    assert row.summary_path == "/tmp/flowstate/run-1/task-1/summary.md"


def test_task_execution_row_defaults() -> None:
    """TaskExecutionRow optional fields default correctly."""
    row = TaskExecutionRow(
        id="task-1",
        flow_run_id="run-1",
        node_name="start",
        node_type="entry",
        status="pending",
        context_mode="none",
        cwd="/home/user",
        task_dir="/tmp/flowstate/run-1/task-1",
        prompt_text="Initialize",
        created_at="2024-01-01T00:00:00",
    )
    assert row.wait_until is None
    assert row.generation == 1
    assert row.claude_session_id is None
    assert row.started_at is None
    assert row.completed_at is None
    assert row.elapsed_seconds is None
    assert row.exit_code is None
    assert row.summary_path is None
    assert row.error_message is None


def test_edge_transition_row_nullable_fields() -> None:
    """EdgeTransitionRow allows None for to_task_id, condition_text, judge_* fields."""
    row = EdgeTransitionRow(
        id="edge-1",
        flow_run_id="run-1",
        from_task_id="task-1",
        edge_type="unconditional",
        created_at="2024-01-01T00:00:00",
    )
    assert row.to_task_id is None
    assert row.condition_text is None
    assert row.judge_session_id is None
    assert row.judge_decision is None
    assert row.judge_reasoning is None
    assert row.judge_confidence is None


def test_edge_transition_row_with_judge() -> None:
    """EdgeTransitionRow accepts all judge-related fields."""
    row = EdgeTransitionRow(
        id="edge-2",
        flow_run_id="run-1",
        from_task_id="task-1",
        to_task_id="task-2",
        edge_type="conditional",
        condition_text="tests pass",
        judge_session_id="judge-sess-1",
        judge_decision="pass",
        judge_reasoning="All tests passed successfully",
        judge_confidence=0.95,
        created_at="2024-01-01T00:00:00",
    )
    assert row.judge_confidence == 0.95
    assert row.judge_reasoning == "All tests passed successfully"


def test_fork_group_row_defaults() -> None:
    """ForkGroupRow.generation defaults to 1."""
    row = ForkGroupRow(
        id="fg-1",
        flow_run_id="run-1",
        source_task_id="task-1",
        join_node_name="merge",
        status="active",
        created_at="2024-01-01T00:00:00",
    )
    assert row.generation == 1


def test_fork_group_member_row_no_defaults() -> None:
    """ForkGroupMemberRow requires both fields (no optional fields)."""
    row = ForkGroupMemberRow(
        fork_group_id="fg-1",
        task_execution_id="task-2",
    )
    assert row.fork_group_id == "fg-1"
    assert row.task_execution_id == "task-2"

    # Verify that omitting either field raises a ValidationError
    with pytest.raises(ValidationError):
        ForkGroupMemberRow(fork_group_id="fg-1")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ForkGroupMemberRow(task_execution_id="task-2")  # type: ignore[call-arg]


def test_task_log_row_integer_id() -> None:
    """TaskLogRow.id is int, not str."""
    row = TaskLogRow(
        id=42,
        task_execution_id="task-1",
        timestamp="2024-01-01T00:00:00",
        log_type="stdout",
        content="Hello world",
    )
    assert row.id == 42
    assert isinstance(row.id, int)


def test_task_log_row_no_optional_fields() -> None:
    """TaskLogRow has no optional fields -- all must be provided."""
    with pytest.raises(ValidationError):
        TaskLogRow(
            task_execution_id="task-1",
            log_type="stdout",
            content="Hello",
        )  # type: ignore[call-arg]


def test_flow_schedule_row_defaults() -> None:
    """FlowScheduleRow.on_overlap defaults to 'skip', enabled defaults to 1."""
    row = FlowScheduleRow(
        id="sched-1",
        flow_definition_id="def-1",
        cron_expression="0 * * * *",
        created_at="2024-01-01T00:00:00",
    )
    assert row.on_overlap == "skip"
    assert row.enabled == 1
    assert row.last_triggered_at is None
    assert row.next_trigger_at is None


def test_flow_schedule_row_all_fields() -> None:
    """FlowScheduleRow accepts all fields including optional ones."""
    row = FlowScheduleRow(
        id="sched-1",
        flow_definition_id="def-1",
        cron_expression="*/5 * * * *",
        on_overlap="queue",
        enabled=0,
        last_triggered_at="2024-01-01T00:00:00",
        next_trigger_at="2024-01-01T00:05:00",
        created_at="2024-01-01T00:00:00",
    )
    assert row.on_overlap == "queue"
    assert row.enabled == 0
    assert row.last_triggered_at == "2024-01-01T00:00:00"
    assert row.next_trigger_at == "2024-01-01T00:05:00"


def test_model_from_sqlite_row() -> None:
    """Construct a model from a dict mimicking sqlite3.Row output."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Create the flow_definitions table matching schema.sql
    conn.execute("""
        CREATE TABLE flow_definitions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            source_dsl TEXT NOT NULL,
            ast_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    def_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO flow_definitions (id, name, source_dsl, ast_json) VALUES (?, ?, ?, ?)",
        (def_id, "test_flow", "flow test_flow { }", '{"name": "test_flow"}'),
    )

    cursor = conn.execute("SELECT * FROM flow_definitions WHERE id = ?", (def_id,))
    sqlite_row = cursor.fetchone()
    assert sqlite_row is not None

    # Convert sqlite3.Row to model via dict unpacking
    model = FlowDefinitionRow(**dict(sqlite_row))
    assert model.id == def_id
    assert model.name == "test_flow"
    assert model.source_dsl == "flow test_flow { }"
    assert model.ast_json == '{"name": "test_flow"}'
    # Timestamps are set by SQLite DEFAULT
    assert model.created_at is not None
    assert model.updated_at is not None

    conn.close()


def test_model_from_sqlite_row_with_nulls() -> None:
    """Construct a model with NULL columns from sqlite3.Row."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE flow_runs (
            id TEXT PRIMARY KEY,
            flow_definition_id TEXT NOT NULL,
            status TEXT NOT NULL,
            default_workspace TEXT,
            data_dir TEXT NOT NULL,
            params_json TEXT,
            budget_seconds INTEGER NOT NULL,
            elapsed_seconds REAL DEFAULT 0,
            on_error TEXT NOT NULL,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            error_message TEXT
        )
    """)

    run_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO flow_runs
           (id, flow_definition_id, status, data_dir, budget_seconds, on_error)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (run_id, "def-1", "created", "/tmp/data", 300, "pause"),
    )

    cursor = conn.execute("SELECT * FROM flow_runs WHERE id = ?", (run_id,))
    sqlite_row = cursor.fetchone()
    assert sqlite_row is not None

    model = FlowRunRow(**dict(sqlite_row))
    assert model.id == run_id
    assert model.status == "created"
    assert model.default_workspace is None
    assert model.params_json is None
    assert model.started_at is None
    assert model.completed_at is None
    assert model.error_message is None
    assert model.elapsed_seconds == 0.0

    conn.close()


def test_model_from_full_schema_sqlite_row() -> None:
    """Construct a TaskExecutionRow from a real schema table with all columns."""
    from flowstate.state.database import FlowstateDB

    db = FlowstateDB(db_path=":memory:")

    # Insert a flow definition first (FK constraint)
    def_id = str(uuid.uuid4())
    db.connection.execute(
        "INSERT INTO flow_definitions (id, name, source_dsl, ast_json) VALUES (?, ?, ?, ?)",
        (def_id, "test_flow", "flow test { }", "{}"),
    )

    # Insert a flow run (FK constraint)
    run_id = str(uuid.uuid4())
    db.connection.execute(
        """INSERT INTO flow_runs
           (id, flow_definition_id, status, data_dir, budget_seconds, on_error)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (run_id, def_id, "running", "/tmp/data", 300, "pause"),
    )

    # Insert a task execution
    task_id = str(uuid.uuid4())
    db.connection.execute(
        """INSERT INTO task_executions
           (id, flow_run_id, node_name, node_type, status, context_mode, cwd, task_dir, prompt_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, run_id, "build", "task", "pending", "handoff", "/cwd", "/task_dir", "Build it"),
    )
    db.connection.commit()

    cursor = db.connection.execute("SELECT * FROM task_executions WHERE id = ?", (task_id,))
    sqlite_row = cursor.fetchone()
    assert sqlite_row is not None

    model = TaskExecutionRow(**dict(sqlite_row))
    assert model.id == task_id
    assert model.flow_run_id == run_id
    assert model.node_name == "build"
    assert model.node_type == "task"
    assert model.status == "pending"
    assert model.context_mode == "handoff"
    assert model.generation == 1
    assert model.wait_until is None
    assert model.claude_session_id is None

    db.close()
