"""Tests for FlowstateDB schema and database setup."""

import sqlite3

import pytest

from flowstate.state.database import FlowstateDB

# All 8 tables (flow_definitions, flow_runs, task_executions, edge_transitions,
# fork_groups, fork_group_members, task_logs, flow_schedules)
# plus the sqlite_sequence table created by AUTOINCREMENT.
EXPECTED_TABLES = {
    "flow_definitions",
    "flow_runs",
    "task_executions",
    "edge_transitions",
    "fork_groups",
    "fork_group_members",
    "task_logs",
    "task_messages",
    "task_artifacts",
    "flow_schedules",
    "tasks",
    "task_node_history",
    "flow_enabled",
    "agent_subtasks",
}

EXPECTED_INDEXES = {
    "idx_flow_runs_status",
    "idx_task_executions_flow_run",
    "idx_task_executions_status",
    "idx_task_executions_waiting",
    "idx_edge_transitions_flow_run",
    "idx_task_logs_execution",
    "idx_task_logs_timestamp",
    "idx_task_messages_task",
    "idx_fork_groups_flow_run",
    "idx_flow_schedules_next",
    "idx_tasks_flow_name",
    "idx_tasks_status",
    "idx_tasks_queue",
    "idx_tasks_parent",
    "idx_task_node_history_task",
    "idx_task_artifacts_task",
    "idx_agent_subtasks_task",
}


@pytest.fixture()
def db() -> FlowstateDB:
    """Create an in-memory FlowstateDB for testing."""
    database = FlowstateDB(":memory:")
    yield database  # type: ignore[misc]
    database.close()


@pytest.fixture()
def file_db(tmp_path: object) -> FlowstateDB:
    """Create a file-backed FlowstateDB for testing WAL mode."""
    from pathlib import Path

    db_path = Path(str(tmp_path)) / "test.db"
    database = FlowstateDB(str(db_path))
    yield database  # type: ignore[misc]
    database.close()


def test_database_opens_in_memory() -> None:
    """FlowstateDB(':memory:') opens without error."""
    db = FlowstateDB(":memory:")
    assert db.connection is not None
    db.close()


def test_wal_mode_enabled(file_db: FlowstateDB) -> None:
    """After init, PRAGMA journal_mode returns 'wal' for file-backed databases.

    Note: in-memory databases do not support WAL mode (they always return 'memory'),
    so we test with a file-backed database instead.
    """
    result = file_db.connection.execute("PRAGMA journal_mode").fetchone()
    assert result is not None
    assert result[0] == "wal"


def test_foreign_keys_enabled(db: FlowstateDB) -> None:
    """PRAGMA foreign_keys returns 1."""
    result = db.connection.execute("PRAGMA foreign_keys").fetchone()
    assert result is not None
    assert result[0] == 1


def test_busy_timeout_set(db: FlowstateDB) -> None:
    """PRAGMA busy_timeout returns 5000."""
    result = db.connection.execute("PRAGMA busy_timeout").fetchone()
    assert result is not None
    assert result[0] == 5000


def test_journal_size_limit_set(db: FlowstateDB) -> None:
    """PRAGMA journal_size_limit returns 67108864 (64MB)."""
    result = db.connection.execute("PRAGMA journal_size_limit").fetchone()
    assert result is not None
    assert result[0] == 67108864


def test_all_tables_exist(db: FlowstateDB) -> None:
    """All 8 tables plus fork_group_members are created (9 total)."""
    cursor = db.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    tables = {row[0] for row in cursor.fetchall()}
    assert tables == EXPECTED_TABLES


def test_all_indexes_exist(db: FlowstateDB) -> None:
    """All 9 indexes are created."""
    cursor = db.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    )
    indexes = {row[0] for row in cursor.fetchall()}
    assert EXPECTED_INDEXES.issubset(indexes), f"Missing indexes: {EXPECTED_INDEXES - indexes}"


def test_schema_idempotent(db: FlowstateDB) -> None:
    """Calling _initialize_schema() twice does not raise."""
    # The schema was already created in __init__. Call it again.
    db._initialize_schema()

    # Verify tables still exist
    cursor = db.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    tables = {row[0] for row in cursor.fetchall()}
    assert tables == EXPECTED_TABLES


def test_foreign_key_constraint_enforced(db: FlowstateDB) -> None:
    """Inserting a flow_run with a non-existent flow_definition_id raises IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        db.connection.execute(
            """
            INSERT INTO flow_runs (
                id, flow_definition_id, status, data_dir,
                budget_seconds, on_error
            ) VALUES (
                'run-1', 'nonexistent-flow-def', 'created',
                '/tmp/runs/run-1', 3600, 'pause'
            )
            """
        )


def test_row_factory_returns_row_objects(db: FlowstateDB) -> None:
    """Connection row_factory is set to sqlite3.Row for dict-like access."""
    assert db.connection.row_factory is sqlite3.Row


def test_context_manager(tmp_path: object) -> None:
    """FlowstateDB can be used as a context manager."""
    from pathlib import Path

    db_path = Path(str(tmp_path)) / "ctx_test.db"
    with FlowstateDB(str(db_path)) as db:
        result = db.connection.execute("PRAGMA foreign_keys").fetchone()
        assert result is not None
        assert result[0] == 1


def test_creates_parent_directory(tmp_path: object) -> None:
    """FlowstateDB creates parent directories if they do not exist."""
    from pathlib import Path

    db_path = Path(str(tmp_path)) / "subdir" / "nested" / "test.db"
    db = FlowstateDB(str(db_path))
    assert db_path.exists()
    db.close()


def test_accepts_path_object(tmp_path: object) -> None:
    """FlowstateDB accepts a pathlib.Path directly (not just str).

    STATE-012: the constructor signature is ``db_path: Path | str`` — production
    code passes ``Project.db_path`` (a ``Path``) directly rather than stringifying.
    """
    from pathlib import Path

    db_path = Path(str(tmp_path)) / "path_obj.db"
    db = FlowstateDB(db_path)
    try:
        assert db_path.exists()
        result = db.connection.execute("PRAGMA foreign_keys").fetchone()
        assert result is not None
        assert result[0] == 1
    finally:
        db.close()


def test_two_projects_have_isolated_databases(tmp_path: object) -> None:
    """STATE-012 / sprint-phase-31-1 TEST-5: two FlowstateDB instances backed by
    distinct file paths must not share any rows.

    This proves the unit-level invariant that underpins the sprint's batch-level
    DB isolation test: `create_app(project_a)` and `create_app(project_b)` will
    each wire a FlowstateDB at `project.db_path`, and those databases must be
    fully independent SQLite files with no cross-contamination.
    """
    from pathlib import Path

    tmp = Path(str(tmp_path))
    path_a = tmp / "project-a" / "flowstate.db"
    path_b = tmp / "project-b" / "flowstate.db"

    db_a = FlowstateDB(path_a)
    db_b = FlowstateDB(path_b)
    try:
        # Sanity: the two files are distinct on disk.
        assert path_a.exists()
        assert path_b.exists()
        assert path_a != path_b

        # Insert a flow_definition + flow_run into project A only.
        db_a.connection.execute(
            """
            INSERT INTO flow_definitions (id, name, source_dsl, ast_json)
            VALUES ('fd-a', 'flow-a', 'flow a {}', '{}')
            """
        )
        db_a.connection.execute(
            """
            INSERT INTO flow_runs (
                id, flow_definition_id, status, data_dir,
                budget_seconds, on_error
            ) VALUES ('run-a', 'fd-a', 'created', '/tmp/runs/run-a', 3600, 'pause')
            """
        )
        db_a.connection.commit()

        # Project A sees exactly one run.
        count_a = db_a.connection.execute("SELECT COUNT(*) FROM flow_runs").fetchone()[0]
        assert count_a == 1

        # Project B sees zero runs — no cross-contamination.
        count_b = db_b.connection.execute("SELECT COUNT(*) FROM flow_runs").fetchone()[0]
        assert count_b == 0

        # Also: project B has zero flow_definitions, confirming isolation
        # isn't just coincidental on one table.
        count_defs_b = db_b.connection.execute("SELECT COUNT(*) FROM flow_definitions").fetchone()[
            0
        ]
        assert count_defs_b == 0
    finally:
        db_a.close()
        db_b.close()


def test_check_constraints_on_flow_run_status(db: FlowstateDB) -> None:
    """flow_runs.status CHECK constraint rejects invalid values."""
    # First insert a valid flow_definition
    db.connection.execute(
        """
        INSERT INTO flow_definitions (id, name, source_dsl, ast_json)
        VALUES ('fd-1', 'test-flow', 'flow test {}', '{}')
        """
    )
    db.connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        db.connection.execute(
            """
            INSERT INTO flow_runs (
                id, flow_definition_id, status, data_dir,
                budget_seconds, on_error
            ) VALUES (
                'run-1', 'fd-1', 'invalid_status',
                '/tmp/runs/run-1', 3600, 'pause'
            )
            """
        )


def test_check_constraints_on_task_execution_node_type(db: FlowstateDB) -> None:
    """task_executions.node_type CHECK constraint rejects invalid values."""
    # Set up flow_definition and flow_run
    db.connection.execute(
        """
        INSERT INTO flow_definitions (id, name, source_dsl, ast_json)
        VALUES ('fd-1', 'test-flow', 'flow test {}', '{}')
        """
    )
    db.connection.execute(
        """
        INSERT INTO flow_runs (
            id, flow_definition_id, status, data_dir,
            budget_seconds, on_error
        ) VALUES ('run-1', 'fd-1', 'created', '/tmp/runs/run-1', 3600, 'pause')
        """
    )
    db.connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        db.connection.execute(
            """
            INSERT INTO task_executions (
                id, flow_run_id, node_name, node_type, status,
                generation, context_mode, cwd, task_dir, prompt_text
            ) VALUES (
                'task-1', 'run-1', 'my_task', 'invalid_type', 'pending',
                1, 'handoff', '/tmp', '/tmp/tasks/my_task-1', 'Do something'
            )
            """
        )


def test_valid_insert_chain(db: FlowstateDB) -> None:
    """Valid inserts across related tables succeed without errors."""
    conn = db.connection

    # Insert flow definition
    conn.execute(
        """
        INSERT INTO flow_definitions (id, name, source_dsl, ast_json)
        VALUES ('fd-1', 'test-flow', 'flow test {}', '{}')
        """
    )

    # Insert flow run
    conn.execute(
        """
        INSERT INTO flow_runs (
            id, flow_definition_id, status, data_dir,
            budget_seconds, on_error
        ) VALUES ('run-1', 'fd-1', 'created', '/tmp/runs/run-1', 3600, 'pause')
        """
    )

    # Insert task execution
    conn.execute(
        """
        INSERT INTO task_executions (
            id, flow_run_id, node_name, node_type, status,
            generation, context_mode, cwd, task_dir, prompt_text
        ) VALUES (
            'task-1', 'run-1', 'start', 'entry', 'pending',
            1, 'handoff', '/tmp', '/tmp/tasks/start-1', 'Begin work'
        )
        """
    )

    # Insert edge transition
    conn.execute(
        """
        INSERT INTO edge_transitions (
            id, flow_run_id, from_task_id, edge_type
        ) VALUES ('edge-1', 'run-1', 'task-1', 'unconditional')
        """
    )

    # Insert task log
    conn.execute(
        """
        INSERT INTO task_logs (task_execution_id, log_type, content)
        VALUES ('task-1', 'stdout', 'Hello, world!')
        """
    )

    conn.commit()

    # Verify all rows exist
    assert conn.execute("SELECT COUNT(*) FROM flow_definitions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM flow_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM task_executions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM edge_transitions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM task_logs").fetchone()[0] == 1
