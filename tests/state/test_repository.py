"""Tests for FlowstateDB repository CRUD operations.

Covers flow definitions, flow runs, task executions, edge transitions,
compound transaction atomicity, fork groups, task logs, flow schedules,
recovery, and waiting tasks. All tests use in-memory SQLite.
"""

import sqlite3
from datetime import UTC, datetime

import pytest

from flowstate.state.repository import FlowstateDB

# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture()
def db() -> FlowstateDB:
    """Create an in-memory FlowstateDB for testing."""
    database = FlowstateDB(":memory:")
    yield database  # type: ignore[misc]
    database.close()


@pytest.fixture()
def flow_def_id(db: FlowstateDB) -> str:
    """Create a flow definition and return its ID."""
    return db.create_flow_definition("test-flow", "flow test {}", "{}")


@pytest.fixture()
def flow_run_id(db: FlowstateDB, flow_def_id: str) -> str:
    """Create a flow run and return its ID."""
    return db.create_flow_run(
        flow_definition_id=flow_def_id,
        data_dir="/tmp/test-run",
        budget_seconds=3600,
        on_error="pause",
    )


# ================================================================== #
# Flow Definition Tests
# ================================================================== #


class TestFlowDefinitions:
    """Tests for flow definition CRUD operations."""

    def test_create_and_get_flow_definition(self, db: FlowstateDB) -> None:
        """Create a definition, get it by ID, verify all fields."""
        def_id = db.create_flow_definition("my-flow", "flow my-flow {}", '{"nodes": []}')

        result = db.get_flow_definition(def_id)
        assert result is not None
        assert result.id == def_id
        assert result.name == "my-flow"
        assert result.source_dsl == "flow my-flow {}"
        assert result.ast_json == '{"nodes": []}'
        assert result.created_at is not None
        assert result.updated_at is not None
        assert result.created_at == result.updated_at

    def test_get_flow_definition_by_name(self, db: FlowstateDB) -> None:
        """Create a definition, retrieve by name."""
        def_id = db.create_flow_definition("named-flow", "source", "{}")

        result = db.get_flow_definition_by_name("named-flow")
        assert result is not None
        assert result.id == def_id
        assert result.name == "named-flow"

    def test_get_flow_definition_not_found(self, db: FlowstateDB) -> None:
        """get_flow_definition with bogus ID returns None."""
        result = db.get_flow_definition("nonexistent-id")
        assert result is None

    def test_get_flow_definition_by_name_not_found(self, db: FlowstateDB) -> None:
        """get_flow_definition_by_name with bogus name returns None."""
        result = db.get_flow_definition_by_name("nonexistent-name")
        assert result is None

    def test_list_flow_definitions(self, db: FlowstateDB) -> None:
        """Create 3 definitions, list returns all 3."""
        db.create_flow_definition("flow-a", "src-a", "{}")
        db.create_flow_definition("flow-b", "src-b", "{}")
        db.create_flow_definition("flow-c", "src-c", "{}")

        results = db.list_flow_definitions()
        assert len(results) == 3
        names = {r.name for r in results}
        assert names == {"flow-a", "flow-b", "flow-c"}

    def test_list_flow_definitions_empty(self, db: FlowstateDB) -> None:
        """list_flow_definitions on empty table returns empty list."""
        results = db.list_flow_definitions()
        assert results == []

    def test_update_flow_definition(self, db: FlowstateDB) -> None:
        """Create, update source_dsl and ast_json, verify updated_at changed."""
        def_id = db.create_flow_definition("update-flow", "old-source", '{"old": true}')

        original = db.get_flow_definition(def_id)
        assert original is not None

        db.update_flow_definition(def_id, "new-source", '{"new": true}')

        updated = db.get_flow_definition(def_id)
        assert updated is not None
        assert updated.source_dsl == "new-source"
        assert updated.ast_json == '{"new": true}'
        assert updated.created_at == original.created_at
        # updated_at should be >= original (may be same if test runs fast)
        assert updated.updated_at >= original.updated_at

    def test_delete_flow_definition(self, db: FlowstateDB) -> None:
        """Create, delete, get returns None."""
        def_id = db.create_flow_definition("delete-me", "source", "{}")
        assert db.get_flow_definition(def_id) is not None

        db.delete_flow_definition(def_id)
        assert db.get_flow_definition(def_id) is None

    def test_delete_flow_definition_nonexistent(self, db: FlowstateDB) -> None:
        """Deleting a non-existent ID is a no-op."""
        db.delete_flow_definition("nonexistent-id")  # Should not raise

    def test_duplicate_flow_definition_name(self, db: FlowstateDB) -> None:
        """Creating two definitions with the same name raises IntegrityError."""
        db.create_flow_definition("unique-name", "source-1", "{}")
        with pytest.raises(sqlite3.IntegrityError):
            db.create_flow_definition("unique-name", "source-2", "{}")


# ================================================================== #
# Flow Run Tests
# ================================================================== #


class TestFlowRuns:
    """Tests for flow run CRUD operations."""

    def test_create_and_get_flow_run(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Create a run, get by ID, verify status='created' and elapsed_seconds=0."""
        run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/run-data",
            budget_seconds=1800,
            on_error="abort",
            default_workspace="/workspace",
            params_json='{"key": "value"}',
        )

        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.id == run_id
        assert result.flow_definition_id == flow_def_id
        assert result.status == "created"
        assert result.default_workspace == "/workspace"
        assert result.data_dir == "/tmp/run-data"
        assert result.params_json == '{"key": "value"}'
        assert result.budget_seconds == 1800
        assert result.elapsed_seconds == 0.0
        assert result.on_error == "abort"
        assert result.started_at is None
        assert result.completed_at is None
        assert result.error_message is None
        assert result.created_at is not None

    def test_create_flow_run_minimal(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Create a run with only required params, optional fields default."""
        run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/run",
            budget_seconds=600,
            on_error="pause",
        )

        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.default_workspace is None
        assert result.params_json is None

    def test_get_flow_run_not_found(self, db: FlowstateDB) -> None:
        """get_flow_run with bogus ID returns None."""
        result = db.get_flow_run("nonexistent-run-id")
        assert result is None

    def test_list_flow_runs_all(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Create multiple runs, list without filter returns all."""
        db.create_flow_run(flow_def_id, "/tmp/r1", 300, "pause")
        db.create_flow_run(flow_def_id, "/tmp/r2", 300, "pause")
        db.create_flow_run(flow_def_id, "/tmp/r3", 300, "pause")

        results = db.list_flow_runs()
        assert len(results) == 3

    def test_list_flow_runs_by_status(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Create runs with different statuses, filter by 'running'."""
        run1 = db.create_flow_run(flow_def_id, "/tmp/r1", 300, "pause")
        run2 = db.create_flow_run(flow_def_id, "/tmp/r2", 300, "pause")
        db.create_flow_run(flow_def_id, "/tmp/r3", 300, "pause")

        db.update_flow_run_status(run1, "running")
        db.update_flow_run_status(run2, "running")

        running = db.list_flow_runs(status="running")
        assert len(running) == 2

        created = db.list_flow_runs(status="created")
        assert len(created) == 1

    def test_list_flow_runs_empty(self, db: FlowstateDB) -> None:
        """list_flow_runs on empty table returns empty list."""
        results = db.list_flow_runs()
        assert results == []

    def test_update_flow_run_status_to_running(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Update status to 'running', verify started_at is set."""
        run_id = db.create_flow_run(flow_def_id, "/tmp/r", 300, "pause")
        db.update_flow_run_status(run_id, "running")

        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.status == "running"
        assert result.started_at is not None
        assert result.completed_at is None

    def test_update_flow_run_status_running_preserves_started_at(
        self, db: FlowstateDB, flow_def_id: str
    ) -> None:
        """Setting status to 'running' a second time does not overwrite started_at."""
        run_id = db.create_flow_run(flow_def_id, "/tmp/r", 300, "pause")
        db.update_flow_run_status(run_id, "running")

        first = db.get_flow_run(run_id)
        assert first is not None
        first_started = first.started_at

        # Update to running again
        db.update_flow_run_status(run_id, "running")
        second = db.get_flow_run(run_id)
        assert second is not None
        assert second.started_at == first_started

    def test_update_flow_run_status_to_completed(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Update status to 'completed', verify completed_at is set."""
        run_id = db.create_flow_run(flow_def_id, "/tmp/r", 300, "pause")
        db.update_flow_run_status(run_id, "running")
        db.update_flow_run_status(run_id, "completed")

        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.status == "completed"
        assert result.completed_at is not None

    def test_update_flow_run_status_with_error(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Update to 'failed' with error_message, verify both set."""
        run_id = db.create_flow_run(flow_def_id, "/tmp/r", 300, "pause")
        db.update_flow_run_status(run_id, "failed", error_message="Something broke")

        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.status == "failed"
        assert result.error_message == "Something broke"
        assert result.completed_at is not None

    def test_update_flow_run_status_budget_exceeded(
        self, db: FlowstateDB, flow_def_id: str
    ) -> None:
        """Update to 'budget_exceeded' sets completed_at."""
        run_id = db.create_flow_run(flow_def_id, "/tmp/r", 300, "pause")
        db.update_flow_run_status(run_id, "budget_exceeded")

        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.status == "budget_exceeded"
        assert result.completed_at is not None

    def test_update_flow_run_elapsed(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Update elapsed_seconds, verify the new value."""
        run_id = db.create_flow_run(flow_def_id, "/tmp/r", 300, "pause")

        db.update_flow_run_elapsed(run_id, 42.5)
        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.elapsed_seconds == 42.5

        db.update_flow_run_elapsed(run_id, 100.0)
        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.elapsed_seconds == 100.0

    def test_update_flow_run_worktree(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Store and retrieve worktree_path for a flow run."""
        run_id = db.create_flow_run(flow_def_id, "/tmp/r", 300, "pause")

        # Initially None
        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.worktree_path is None

        # Set worktree path
        db.update_flow_run_worktree(run_id, "/tmp/worktrees/run-123")
        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.worktree_path == "/tmp/worktrees/run-123"

        # Update to a different path
        db.update_flow_run_worktree(run_id, "/tmp/worktrees/run-456")
        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.worktree_path == "/tmp/worktrees/run-456"

    def test_create_flow_run_invalid_definition_id(self, db: FlowstateDB) -> None:
        """Creating a flow_run with non-existent flow_definition_id raises IntegrityError."""
        with pytest.raises(sqlite3.IntegrityError):
            db.create_flow_run(
                flow_definition_id="nonexistent-def-id",
                data_dir="/tmp/r",
                budget_seconds=300,
                on_error="pause",
            )


# ================================================================== #
# Task Execution Tests
# ================================================================== #


class TestTaskExecutions:
    """Tests for task execution CRUD operations."""

    def test_create_and_get_task_execution(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Create a task, get by ID, verify status='pending'."""
        task_id = db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name="build",
            node_type="task",
            generation=1,
            context_mode="handoff",
            cwd="/workspace",
            task_dir="/tmp/tasks/build-1",
            prompt_text="Build the project",
        )

        result = db.get_task_execution(task_id)
        assert result is not None
        assert result.id == task_id
        assert result.flow_run_id == flow_run_id
        assert result.node_name == "build"
        assert result.node_type == "task"
        assert result.status == "pending"
        assert result.generation == 1
        assert result.context_mode == "handoff"
        assert result.cwd == "/workspace"
        assert result.task_dir == "/tmp/tasks/build-1"
        assert result.prompt_text == "Build the project"
        assert result.created_at is not None
        assert result.claude_session_id is None
        assert result.started_at is None
        assert result.completed_at is None
        assert result.elapsed_seconds is None
        assert result.exit_code is None
        assert result.summary_path is None
        assert result.error_message is None

    def test_get_task_execution_not_found(self, db: FlowstateDB) -> None:
        """get_task_execution with bogus ID returns None."""
        result = db.get_task_execution("nonexistent-task-id")
        assert result is None

    def test_list_task_executions(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Create 3 tasks, list returns all 3 ordered by created_at."""
        db.create_task_execution(
            flow_run_id, "task-a", "task", 1, "handoff", "/cwd", "/dir/a", "Do A"
        )
        db.create_task_execution(
            flow_run_id, "task-b", "task", 1, "session", "/cwd", "/dir/b", "Do B"
        )
        db.create_task_execution(flow_run_id, "task-c", "task", 1, "none", "/cwd", "/dir/c", "Do C")

        results = db.list_task_executions(flow_run_id)
        assert len(results) == 3
        names = [r.node_name for r in results]
        assert names == ["task-a", "task-b", "task-c"]

    def test_list_task_executions_empty(self, db: FlowstateDB, flow_run_id: str) -> None:
        """list_task_executions on flow run with no tasks returns empty list."""
        results = db.list_task_executions(flow_run_id)
        assert results == []

    def test_get_pending_tasks(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Create 2 pending + 1 running task, get_pending returns only 2."""
        t1 = db.create_task_execution(
            flow_run_id, "pending-1", "task", 1, "handoff", "/cwd", "/dir/1", "P1"
        )
        db.create_task_execution(
            flow_run_id, "pending-2", "task", 1, "handoff", "/cwd", "/dir/2", "P2"
        )
        t3 = db.create_task_execution(
            flow_run_id, "running-1", "task", 1, "handoff", "/cwd", "/dir/3", "R1"
        )

        # Make t3 running -- ignore t1 so it stays pending
        _ = t1
        db.update_task_status(t3, "running", started_at="2024-01-01T00:00:00")

        pending = db.get_pending_tasks(flow_run_id)
        assert len(pending) == 2
        names = {r.node_name for r in pending}
        assert names == {"pending-1", "pending-2"}

    def test_get_pending_tasks_empty(self, db: FlowstateDB, flow_run_id: str) -> None:
        """get_pending_tasks on flow run with no pending tasks returns empty list."""
        results = db.get_pending_tasks(flow_run_id)
        assert results == []

    def test_update_task_status_to_running(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Update to 'running' with claude_session_id and started_at."""
        task_id = db.create_task_execution(
            flow_run_id, "my-task", "task", 1, "handoff", "/cwd", "/dir", "Do it"
        )

        db.update_task_status(
            task_id,
            "running",
            claude_session_id="sess-abc",
            started_at="2024-06-15T10:00:00",
        )

        result = db.get_task_execution(task_id)
        assert result is not None
        assert result.status == "running"
        assert result.claude_session_id == "sess-abc"
        assert result.started_at == "2024-06-15T10:00:00"

    def test_update_task_status_to_completed(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Update to 'completed' with completed_at, elapsed_seconds, exit_code, summary_path."""
        task_id = db.create_task_execution(
            flow_run_id, "my-task", "task", 1, "handoff", "/cwd", "/dir", "Do it"
        )

        db.update_task_status(
            task_id,
            "completed",
            completed_at="2024-06-15T10:05:00",
            elapsed_seconds=300.0,
            exit_code=0,
            summary_path="/tmp/summary.md",
        )

        result = db.get_task_execution(task_id)
        assert result is not None
        assert result.status == "completed"
        assert result.completed_at == "2024-06-15T10:05:00"
        assert result.elapsed_seconds == 300.0
        assert result.exit_code == 0
        assert result.summary_path == "/tmp/summary.md"

    def test_update_task_status_to_failed(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Update to 'failed' with error_message."""
        task_id = db.create_task_execution(
            flow_run_id, "my-task", "task", 1, "handoff", "/cwd", "/dir", "Do it"
        )

        db.update_task_status(
            task_id,
            "failed",
            error_message="Task crashed",
            completed_at="2024-06-15T10:02:00",
        )

        result = db.get_task_execution(task_id)
        assert result is not None
        assert result.status == "failed"
        assert result.error_message == "Task crashed"

    def test_update_task_status_only(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Update with just status change and no kwargs is valid."""
        task_id = db.create_task_execution(
            flow_run_id, "my-task", "task", 1, "handoff", "/cwd", "/dir", "Do it"
        )

        db.update_task_status(task_id, "running")

        result = db.get_task_execution(task_id)
        assert result is not None
        assert result.status == "running"

    def test_update_task_status_invalid_kwarg(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Passing unknown kwarg raises ValueError."""
        task_id = db.create_task_execution(
            flow_run_id, "my-task", "task", 1, "handoff", "/cwd", "/dir", "Do it"
        )

        with pytest.raises(ValueError, match="Unknown task field: bogus_field"):
            db.update_task_status(task_id, "running", bogus_field="bad")

    def test_create_task_invalid_flow_run_id(self, db: FlowstateDB) -> None:
        """Creating task with non-existent flow_run_id raises IntegrityError."""
        with pytest.raises(sqlite3.IntegrityError):
            db.create_task_execution(
                flow_run_id="nonexistent-run",
                node_name="task",
                node_type="task",
                generation=1,
                context_mode="handoff",
                cwd="/cwd",
                task_dir="/dir",
                prompt_text="Do it",
            )


# ================================================================== #
# Edge Transition Tests
# ================================================================== #


class TestEdgeTransitions:
    """Tests for edge transition CRUD operations."""

    def _create_task(self, db: FlowstateDB, flow_run_id: str, name: str) -> str:
        """Helper to create a task execution and return its ID."""
        return db.create_task_execution(
            flow_run_id, name, "task", 1, "handoff", "/cwd", f"/dir/{name}", f"Do {name}"
        )

    def test_create_edge_transition_unconditional(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Create an unconditional edge, verify all fields."""
        from_id = self._create_task(db, flow_run_id, "from-task")
        to_id = self._create_task(db, flow_run_id, "to-task")

        edge_id = db.create_edge_transition(
            flow_run_id=flow_run_id,
            from_task_id=from_id,
            to_task_id=to_id,
            edge_type="unconditional",
        )

        # Verify by reading from the DB directly since we don't have a get method
        row = db.connection.execute(
            "SELECT * FROM edge_transitions WHERE id = ?", (edge_id,)
        ).fetchone()
        assert row is not None
        from flowstate.state.models import EdgeTransitionRow

        result = EdgeTransitionRow(**dict(row))
        assert result.id == edge_id
        assert result.flow_run_id == flow_run_id
        assert result.from_task_id == from_id
        assert result.to_task_id == to_id
        assert result.edge_type == "unconditional"
        assert result.condition_text is None
        assert result.judge_session_id is None
        assert result.judge_decision is None
        assert result.judge_reasoning is None
        assert result.judge_confidence is None
        assert result.created_at is not None

    def test_create_edge_transition_with_judge(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Create a conditional edge with all judge fields populated."""
        from_id = self._create_task(db, flow_run_id, "from-task")
        to_id = self._create_task(db, flow_run_id, "to-task")

        edge_id = db.create_edge_transition(
            flow_run_id=flow_run_id,
            from_task_id=from_id,
            to_task_id=to_id,
            edge_type="conditional",
            condition_text="tests pass",
            judge_session_id="judge-sess-1",
            judge_decision="pass",
            judge_reasoning="All tests passed successfully",
            judge_confidence=0.95,
        )

        row = db.connection.execute(
            "SELECT * FROM edge_transitions WHERE id = ?", (edge_id,)
        ).fetchone()
        assert row is not None
        from flowstate.state.models import EdgeTransitionRow

        result = EdgeTransitionRow(**dict(row))
        assert result.edge_type == "conditional"
        assert result.condition_text == "tests pass"
        assert result.judge_session_id == "judge-sess-1"
        assert result.judge_decision == "pass"
        assert result.judge_reasoning == "All tests passed successfully"
        assert result.judge_confidence == 0.95

    def test_create_edge_transition_null_to_task(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Edge with to_task_id=None is valid."""
        from_id = self._create_task(db, flow_run_id, "from-task")

        edge_id = db.create_edge_transition(
            flow_run_id=flow_run_id,
            from_task_id=from_id,
            to_task_id=None,
            edge_type="conditional",
            condition_text="no match",
        )

        row = db.connection.execute(
            "SELECT * FROM edge_transitions WHERE id = ?", (edge_id,)
        ).fetchone()
        assert row is not None
        assert row["to_task_id"] is None

    def test_edge_transition_invalid_from_task(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Creating edge with non-existent from_task_id raises IntegrityError."""
        with pytest.raises(sqlite3.IntegrityError):
            db.create_edge_transition(
                flow_run_id=flow_run_id,
                from_task_id="nonexistent-task-id",
                to_task_id=None,
                edge_type="unconditional",
            )

    def test_edge_transition_invalid_flow_run(self, db: FlowstateDB) -> None:
        """Creating edge with non-existent flow_run_id raises IntegrityError."""
        with pytest.raises(sqlite3.IntegrityError):
            db.create_edge_transition(
                flow_run_id="nonexistent-run",
                from_task_id="nonexistent-task",
                to_task_id=None,
                edge_type="unconditional",
            )


# ================================================================== #
# Compound Transaction Tests
# ================================================================== #


class TestCompoundTransactions:
    """Tests for atomic compound operations using _transaction()."""

    def _create_task(self, db: FlowstateDB, flow_run_id: str, name: str) -> str:
        """Helper to create a task execution and return its ID."""
        return db.create_task_execution(
            flow_run_id, name, "task", 1, "handoff", "/cwd", f"/dir/{name}", f"Do {name}"
        )

    def test_task_status_and_edge_atomic_success(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Within _transaction(), both task update and edge creation succeed."""
        from_id = self._create_task(db, flow_run_id, "from-task")
        to_id = self._create_task(db, flow_run_id, "to-task")

        with db._transaction():
            db.update_task_status(
                from_id,
                "completed",
                completed_at="2024-06-15T10:05:00",
                exit_code=0,
            )
            db.create_edge_transition(
                flow_run_id=flow_run_id,
                from_task_id=from_id,
                to_task_id=to_id,
                edge_type="unconditional",
            )

        # Verify both operations committed
        task = db.get_task_execution(from_id)
        assert task is not None
        assert task.status == "completed"

        edges = db.connection.execute(
            "SELECT COUNT(*) FROM edge_transitions WHERE from_task_id = ?", (from_id,)
        ).fetchone()
        assert edges is not None
        assert edges[0] == 1

    def test_task_status_and_edge_atomic_rollback(self, db: FlowstateDB, flow_run_id: str) -> None:
        """If edge creation fails inside _transaction(), task status is rolled back."""
        task_id = self._create_task(db, flow_run_id, "my-task")

        # Verify initial state
        task_before = db.get_task_execution(task_id)
        assert task_before is not None
        assert task_before.status == "pending"

        with pytest.raises(sqlite3.IntegrityError), db._transaction():
            db.update_task_status(
                task_id,
                "completed",
                completed_at="2024-06-15T10:05:00",
                exit_code=0,
            )
            # This should fail due to invalid FK
            db.create_edge_transition(
                flow_run_id=flow_run_id,
                from_task_id=task_id,
                to_task_id="nonexistent-task-id",  # Invalid FK
                edge_type="unconditional",
            )

        # Verify task status was rolled back
        task_after = db.get_task_execution(task_id)
        assert task_after is not None
        assert task_after.status == "pending"

    def test_transaction_flag_reset_on_success(self, db: FlowstateDB, flow_run_id: str) -> None:
        """After a successful transaction, _in_transaction is False."""
        task_id = self._create_task(db, flow_run_id, "task")

        with db._transaction():
            db.update_task_status(task_id, "running")

        assert db._in_transaction is False

    def test_transaction_flag_reset_on_failure(self, db: FlowstateDB, flow_run_id: str) -> None:
        """After a failed transaction, _in_transaction is False."""
        with pytest.raises(sqlite3.IntegrityError), db._transaction():
            db.create_edge_transition(
                flow_run_id=flow_run_id,
                from_task_id="nonexistent",
                to_task_id=None,
                edge_type="unconditional",
            )

        assert db._in_transaction is False


# ================================================================== #
# Fork Group Tests (STATE-005)
# ================================================================== #


class TestForkGroups:
    """Tests for fork group CRUD operations."""

    def _create_task(self, db: FlowstateDB, flow_run_id: str, name: str) -> str:
        """Helper to create a task execution and return its ID."""
        return db.create_task_execution(
            flow_run_id, name, "task", 1, "handoff", "/tmp", f"/tmp/{name}-1", f"do {name}"
        )

    def test_create_fork_group_with_members(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Create 3 tasks, create a fork group with 2 members, verify group and members."""
        t1 = self._create_task(db, flow_run_id, "a")
        t2 = self._create_task(db, flow_run_id, "b")
        t3 = self._create_task(db, flow_run_id, "c")

        group_id = db.create_fork_group(flow_run_id, t1, "join_node", 1, [t2, t3])

        group = db.get_fork_group(group_id)
        assert group is not None
        assert group.flow_run_id == flow_run_id
        assert group.source_task_id == t1
        assert group.join_node_name == "join_node"
        assert group.generation == 1
        assert group.status == "active"
        assert group.created_at is not None

        members = db.get_fork_group_members(group_id)
        assert len(members) == 2
        member_ids = {m.id for m in members}
        assert member_ids == {t2, t3}

    def test_fork_group_creation_atomicity(self, db: FlowstateDB, flow_run_id: str) -> None:
        """If a member insert fails, the group row is also rolled back."""
        t1 = self._create_task(db, flow_run_id, "a")

        with pytest.raises(sqlite3.IntegrityError):
            db.create_fork_group(flow_run_id, t1, "join_node", 1, [t1, "nonexistent-id"])

        # Verify no fork group was created
        groups = db.get_active_fork_groups(flow_run_id)
        assert len(groups) == 0

    def test_get_fork_group_not_found(self, db: FlowstateDB) -> None:
        """get_fork_group with bogus ID returns None."""
        result = db.get_fork_group("nonexistent-id")
        assert result is None

    def test_get_active_fork_groups(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Create 2 groups, mark one 'joined', get_active returns only the active one."""
        t1 = self._create_task(db, flow_run_id, "a")
        t2 = self._create_task(db, flow_run_id, "b")

        g1 = db.create_fork_group(flow_run_id, t1, "join1", 1, [t2])
        g2 = db.create_fork_group(flow_run_id, t1, "join2", 1, [t2])

        db.update_fork_group_status(g1, "joined")

        active = db.get_active_fork_groups(flow_run_id)
        assert len(active) == 1
        assert active[0].id == g2

    def test_get_active_fork_groups_empty(self, db: FlowstateDB, flow_run_id: str) -> None:
        """get_active_fork_groups returns empty list when none are active."""
        active = db.get_active_fork_groups(flow_run_id)
        assert active == []

    def test_update_fork_group_status(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Update fork group status from 'active' to 'joined'."""
        t1 = self._create_task(db, flow_run_id, "a")
        group_id = db.create_fork_group(flow_run_id, t1, "join_node", 1, [])

        db.update_fork_group_status(group_id, "joined")

        group = db.get_fork_group(group_id)
        assert group is not None
        assert group.status == "joined"

    def test_create_fork_group_empty_members(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Fork group with no members is technically valid."""
        t1 = self._create_task(db, flow_run_id, "a")
        group_id = db.create_fork_group(flow_run_id, t1, "join_node", 1, [])

        group = db.get_fork_group(group_id)
        assert group is not None
        assert group.status == "active"

        members = db.get_fork_group_members(group_id)
        assert members == []

    def test_get_fork_group_members_empty(self, db: FlowstateDB, flow_run_id: str) -> None:
        """get_fork_group_members returns empty list for group with no members."""
        t1 = self._create_task(db, flow_run_id, "a")
        group_id = db.create_fork_group(flow_run_id, t1, "join_node", 1, [])

        members = db.get_fork_group_members(group_id)
        assert members == []


# ================================================================== #
# Task Log Tests (STATE-005)
# ================================================================== #


class TestTaskLogs:
    """Tests for task log insertion and retrieval."""

    def _create_task(self, db: FlowstateDB, flow_run_id: str, name: str = "a") -> str:
        """Helper to create a task execution and return its ID."""
        return db.create_task_execution(
            flow_run_id, name, "task", 1, "handoff", "/tmp", f"/tmp/{name}-1", f"do {name}"
        )

    def test_insert_and_get_task_logs(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Insert 3 logs, get_task_logs returns all 3 in order."""
        task_id = self._create_task(db, flow_run_id)

        db.insert_task_log(task_id, "stdout", "line 1")
        db.insert_task_log(task_id, "stderr", "error 1")
        db.insert_task_log(task_id, "assistant_message", "thinking...")

        logs = db.get_task_logs(task_id)
        assert len(logs) == 3
        assert logs[0].log_type == "stdout"
        assert logs[0].content == "line 1"
        assert logs[1].log_type == "stderr"
        assert logs[1].content == "error 1"
        assert logs[2].log_type == "assistant_message"
        assert logs[2].content == "thinking..."

    def test_get_task_logs_with_limit(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Insert 5 logs, get with limit=2 returns first 2."""
        task_id = self._create_task(db, flow_run_id)
        for i in range(5):
            db.insert_task_log(task_id, "stdout", f"line {i}")

        logs = db.get_task_logs(task_id, limit=2)
        assert len(logs) == 2

    def test_get_task_logs_after_timestamp(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Insert logs, filter by after_timestamp to get only newer entries."""
        task_id = self._create_task(db, flow_run_id)
        db.insert_task_log(task_id, "stdout", "old line")

        logs_before = db.get_task_logs(task_id)
        cutoff = logs_before[0].timestamp  # timestamp of the first log

        db.insert_task_log(task_id, "stdout", "new line")

        logs_after = db.get_task_logs(task_id, after_timestamp=cutoff)
        # Should contain only the second log (timestamp strictly > cutoff)
        assert all(log.content != "old line" for log in logs_after)

    def test_task_log_ordering_by_id(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Logs inserted in rapid succession maintain insertion order via id tiebreaker."""
        task_id = self._create_task(db, flow_run_id)
        for i in range(10):
            db.insert_task_log(task_id, "stdout", f"line {i}")

        logs = db.get_task_logs(task_id)
        assert [log.content for log in logs] == [f"line {i}" for i in range(10)]

    def test_get_task_logs_empty(self, db: FlowstateDB, flow_run_id: str) -> None:
        """get_task_logs on task with no logs returns empty list."""
        task_id = self._create_task(db, flow_run_id)
        logs = db.get_task_logs(task_id)
        assert logs == []

    def test_get_task_logs_limit_zero(self, db: FlowstateDB, flow_run_id: str) -> None:
        """get_task_logs with limit=0 returns empty list."""
        task_id = self._create_task(db, flow_run_id)
        db.insert_task_log(task_id, "stdout", "line 1")

        logs = db.get_task_logs(task_id, limit=0)
        assert logs == []

    def test_task_log_timestamp_is_set(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Inserted log has a non-null timestamp set by SQLite."""
        task_id = self._create_task(db, flow_run_id)
        db.insert_task_log(task_id, "stdout", "hello")

        logs = db.get_task_logs(task_id)
        assert len(logs) == 1
        assert logs[0].timestamp is not None
        assert logs[0].id is not None


# ================================================================== #
# Flow Schedule Tests (STATE-006)
# ================================================================== #


class TestFlowSchedules:
    """Tests for flow schedule CRUD operations."""

    def test_create_and_get_flow_schedule(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Create a schedule, get by ID, verify fields."""
        schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *")

        schedule = db.get_flow_schedule(schedule_id)
        assert schedule is not None
        assert schedule.id == schedule_id
        assert schedule.flow_definition_id == flow_def_id
        assert schedule.cron_expression == "0 * * * *"
        assert schedule.on_overlap == "skip"
        assert schedule.enabled == 1
        assert schedule.last_triggered_at is None
        assert schedule.next_trigger_at is None
        assert schedule.created_at is not None

    def test_get_flow_schedule_not_found(self, db: FlowstateDB) -> None:
        """get_flow_schedule with bogus ID returns None."""
        result = db.get_flow_schedule("nonexistent-id")
        assert result is None

    def test_list_flow_schedules(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Create 2 schedules, list returns both."""
        db.create_flow_schedule(flow_def_id, "0 * * * *")
        db.create_flow_schedule(flow_def_id, "0 0 * * *")

        schedules = db.list_flow_schedules()
        assert len(schedules) == 2

    def test_list_flow_schedules_empty(self, db: FlowstateDB) -> None:
        """list_flow_schedules on empty table returns empty list."""
        schedules = db.list_flow_schedules()
        assert schedules == []

    def test_list_flow_schedules_by_definition(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Filter schedules by flow_definition_id."""
        other_def_id = db.create_flow_definition("other-flow", "source2", "{}")
        db.create_flow_schedule(flow_def_id, "0 * * * *")
        db.create_flow_schedule(other_def_id, "0 0 * * *")

        schedules = db.list_flow_schedules(flow_definition_id=flow_def_id)
        assert len(schedules) == 1
        assert schedules[0].flow_definition_id == flow_def_id

    def test_update_flow_schedule(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Update cron_expression and enabled status."""
        schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *")

        db.update_flow_schedule(schedule_id, cron_expression="0 0 * * *", enabled=0)

        schedule = db.get_flow_schedule(schedule_id)
        assert schedule is not None
        assert schedule.cron_expression == "0 0 * * *"
        assert schedule.enabled == 0

    def test_update_flow_schedule_last_triggered(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Update last_triggered_at and next_trigger_at."""
        schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *")
        now = datetime.now(UTC).isoformat()

        db.update_flow_schedule(
            schedule_id, last_triggered_at=now, next_trigger_at="2099-01-01T00:00:00"
        )

        schedule = db.get_flow_schedule(schedule_id)
        assert schedule is not None
        assert schedule.last_triggered_at == now
        assert schedule.next_trigger_at == "2099-01-01T00:00:00"

    def test_update_flow_schedule_invalid_kwarg(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Unknown kwarg raises ValueError."""
        schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *")

        with pytest.raises(ValueError, match="Unknown schedule field: nonexistent_field"):
            db.update_flow_schedule(schedule_id, nonexistent_field="value")

    def test_update_flow_schedule_no_kwargs(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Update with no kwargs is a no-op."""
        schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *")
        db.update_flow_schedule(schedule_id)  # Should not raise

        schedule = db.get_flow_schedule(schedule_id)
        assert schedule is not None
        assert schedule.cron_expression == "0 * * * *"

    def test_delete_flow_schedule(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Delete a schedule, verify it's gone."""
        schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *")
        db.delete_flow_schedule(schedule_id)

        assert db.get_flow_schedule(schedule_id) is None

    def test_delete_flow_schedule_nonexistent(self, db: FlowstateDB) -> None:
        """Deleting a non-existent ID is a no-op."""
        db.delete_flow_schedule("nonexistent-id")  # Should not raise

    def test_get_due_schedules(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Create schedules with different next_trigger_at, verify due filtering."""
        past = "2020-01-01T00:00:00"
        future = "2099-01-01T00:00:00"
        s1 = db.create_flow_schedule(flow_def_id, "0 * * * *", next_trigger_at=past)
        _s2 = db.create_flow_schedule(flow_def_id, "0 0 * * *", next_trigger_at=future)

        now = datetime.now(UTC).isoformat()
        due = db.get_due_schedules(now=now)
        assert len(due) == 1
        assert due[0].id == s1

    def test_get_due_schedules_excludes_disabled(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Disabled schedules are not returned even if due."""
        past = "2020-01-01T00:00:00"
        schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *", next_trigger_at=past)
        db.update_flow_schedule(schedule_id, enabled=0)

        due = db.get_due_schedules()
        assert len(due) == 0

    def test_get_due_schedules_excludes_null_trigger(
        self, db: FlowstateDB, flow_def_id: str
    ) -> None:
        """Schedules with NULL next_trigger_at are not returned."""
        db.create_flow_schedule(flow_def_id, "0 * * * *")  # next_trigger_at defaults to None
        due = db.get_due_schedules()
        assert len(due) == 0

    def test_get_due_schedules_empty(self, db: FlowstateDB) -> None:
        """get_due_schedules with no schedules returns empty list."""
        due = db.get_due_schedules()
        assert due == []

    def test_create_schedule_invalid_definition_id(self, db: FlowstateDB) -> None:
        """Creating schedule with non-existent flow_definition_id raises IntegrityError."""
        with pytest.raises(sqlite3.IntegrityError):
            db.create_flow_schedule("nonexistent-id", "0 * * * *")

    def test_create_schedule_with_next_trigger(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Create a schedule with explicit next_trigger_at."""
        next_at = "2025-06-01T12:00:00"
        schedule_id = db.create_flow_schedule(flow_def_id, "0 * * * *", next_trigger_at=next_at)

        schedule = db.get_flow_schedule(schedule_id)
        assert schedule is not None
        assert schedule.next_trigger_at == next_at


# ================================================================== #
# Recovery Tests (STATE-006)
# ================================================================== #


class TestRecovery:
    """Tests for crash recovery query methods."""

    def test_get_running_flow_runs(self, db: FlowstateDB, flow_def_id: str) -> None:
        """Create a 'running' flow run, verify get_running_flow_runs finds it."""
        run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/test-run",
            budget_seconds=3600,
            on_error="pause",
        )
        db.update_flow_run_status(run_id, "running")

        running = db.get_running_flow_runs()
        assert len(running) == 1
        assert running[0].id == run_id

    def test_get_running_flow_runs_excludes_completed(
        self, db: FlowstateDB, flow_def_id: str
    ) -> None:
        """Completed flow runs are not returned by get_running_flow_runs."""
        run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/test-run",
            budget_seconds=3600,
            on_error="pause",
        )
        db.update_flow_run_status(run_id, "running")
        db.update_flow_run_status(run_id, "completed")

        running = db.get_running_flow_runs()
        assert len(running) == 0

    def test_get_running_flow_runs_excludes_created(
        self, db: FlowstateDB, flow_def_id: str
    ) -> None:
        """Flow runs with status 'created' are not returned by get_running_flow_runs."""
        db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/test-run",
            budget_seconds=3600,
            on_error="pause",
        )

        running = db.get_running_flow_runs()
        assert len(running) == 0

    def test_get_running_flow_runs_empty(self, db: FlowstateDB) -> None:
        """get_running_flow_runs returns empty list when no flows are running."""
        running = db.get_running_flow_runs()
        assert running == []

    def test_get_running_tasks(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Create a 'running' task, verify get_running_tasks finds it."""
        task_id = db.create_task_execution(
            flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a"
        )
        db.update_task_status(task_id, "running", started_at=datetime.now(UTC).isoformat())

        running = db.get_running_tasks(flow_run_id)
        assert len(running) == 1
        assert running[0].id == task_id

    def test_get_running_tasks_excludes_completed(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Completed tasks are not returned by get_running_tasks."""
        task_id = db.create_task_execution(
            flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a"
        )
        db.update_task_status(task_id, "running", started_at=datetime.now(UTC).isoformat())
        db.update_task_status(task_id, "completed", completed_at=datetime.now(UTC).isoformat())

        running = db.get_running_tasks(flow_run_id)
        assert len(running) == 0

    def test_get_running_tasks_excludes_pending(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Pending tasks are not returned by get_running_tasks."""
        db.create_task_execution(flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a")

        running = db.get_running_tasks(flow_run_id)
        assert len(running) == 0

    def test_get_running_tasks_empty(self, db: FlowstateDB, flow_run_id: str) -> None:
        """get_running_tasks returns empty list for flow with no running tasks."""
        running = db.get_running_tasks(flow_run_id)
        assert running == []


# ================================================================== #
# Waiting Task Tests (STATE-006)
# ================================================================== #


class TestWaitingTasks:
    """Tests for waiting task query methods."""

    def test_get_waiting_tasks(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Create a waiting task with past wait_until, verify it's found."""
        task_id = db.create_task_execution(
            flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a"
        )
        past = "2020-01-01T00:00:00"
        db.update_task_status(task_id, "waiting", wait_until=past)

        waiting = db.get_waiting_tasks(flow_run_id)
        assert len(waiting) == 1
        assert waiting[0].id == task_id

    def test_get_waiting_tasks_excludes_future(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Waiting tasks with future wait_until are not returned."""
        task_id = db.create_task_execution(
            flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a"
        )
        future = "2099-01-01T00:00:00"
        db.update_task_status(task_id, "waiting", wait_until=future)

        waiting = db.get_waiting_tasks(flow_run_id)
        assert len(waiting) == 0

    def test_get_waiting_tasks_excludes_non_waiting(
        self, db: FlowstateDB, flow_run_id: str
    ) -> None:
        """Only tasks with status 'waiting' are returned."""
        db.create_task_execution(flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a")
        # Task is 'pending', not 'waiting'

        waiting = db.get_waiting_tasks(flow_run_id)
        assert len(waiting) == 0

    def test_get_waiting_tasks_empty(self, db: FlowstateDB, flow_run_id: str) -> None:
        """get_waiting_tasks returns empty list when no tasks are waiting."""
        waiting = db.get_waiting_tasks(flow_run_id)
        assert waiting == []

    def test_get_waiting_tasks_with_explicit_now(self, db: FlowstateDB, flow_run_id: str) -> None:
        """get_waiting_tasks with explicit now parameter filters correctly."""
        task_id = db.create_task_execution(
            flow_run_id, "a", "task", 1, "handoff", "/tmp", "/tmp/a-1", "do a"
        )
        db.update_task_status(task_id, "waiting", wait_until="2025-06-01T12:00:00")

        # Before the wait_until time
        waiting_before = db.get_waiting_tasks(flow_run_id, now="2025-06-01T11:00:00")
        assert len(waiting_before) == 0

        # Exactly at the wait_until time
        waiting_at = db.get_waiting_tasks(flow_run_id, now="2025-06-01T12:00:00")
        assert len(waiting_at) == 1

        # After the wait_until time
        waiting_after = db.get_waiting_tasks(flow_run_id, now="2025-06-01T13:00:00")
        assert len(waiting_after) == 1


# ================================================================== #
# Task Queue Operations (tasks table)
# ================================================================== #


class TestTaskOperations:
    """Tests for task queue CRUD operations (tasks table)."""

    def test_create_and_get_task(self, db: FlowstateDB) -> None:
        """Create a task and retrieve it by ID, verify all fields."""
        task_id = db.create_task(
            flow_name="my-flow",
            title="Fix login bug",
            description="The login page crashes on submit",
            params_json='{"repo": "myapp"}',
            created_by="user",
            priority=5,
        )

        result = db.get_task(task_id)
        assert result is not None
        assert result.id == task_id
        assert result.flow_name == "my-flow"
        assert result.title == "Fix login bug"
        assert result.description == "The login page crashes on submit"
        assert result.status == "queued"
        assert result.current_node is None
        assert result.params_json == '{"repo": "myapp"}'
        assert result.output_json is None
        assert result.parent_task_id is None
        assert result.created_by == "user"
        assert result.flow_run_id is None
        assert result.priority == 5
        assert result.created_at is not None
        assert result.started_at is None
        assert result.completed_at is None
        assert result.error_message is None

    def test_create_task_minimal(self, db: FlowstateDB) -> None:
        """Create a task with only required params, verify defaults."""
        task_id = db.create_task(flow_name="my-flow", title="Do something")

        result = db.get_task(task_id)
        assert result is not None
        assert result.description is None
        assert result.params_json is None
        assert result.parent_task_id is None
        assert result.created_by is None
        assert result.priority == 0

    def test_get_task_not_found(self, db: FlowstateDB) -> None:
        """get_task with bogus ID returns None."""
        result = db.get_task("nonexistent-task-id")
        assert result is None

    def test_list_tasks_all(self, db: FlowstateDB) -> None:
        """Create multiple tasks, list without filters returns all."""
        db.create_task("flow-a", "Task 1")
        db.create_task("flow-a", "Task 2")
        db.create_task("flow-b", "Task 3")

        results = db.list_tasks()
        assert len(results) == 3

    def test_list_tasks_by_flow_name(self, db: FlowstateDB) -> None:
        """Filter tasks by flow_name."""
        db.create_task("flow-a", "Task 1")
        db.create_task("flow-a", "Task 2")
        db.create_task("flow-b", "Task 3")

        results = db.list_tasks(flow_name="flow-a")
        assert len(results) == 2
        assert all(r.flow_name == "flow-a" for r in results)

    def test_list_tasks_by_status(self, db: FlowstateDB) -> None:
        """Filter tasks by status."""
        t1 = db.create_task("flow-a", "Task 1")
        db.create_task("flow-a", "Task 2")
        db.update_task_queue_status(t1, "running")

        queued = db.list_tasks(status="queued")
        assert len(queued) == 1
        assert queued[0].title == "Task 2"

        running = db.list_tasks(status="running")
        assert len(running) == 1
        assert running[0].id == t1

    def test_list_tasks_with_limit(self, db: FlowstateDB) -> None:
        """Limit the number of returned tasks."""
        db.create_task("flow-a", "Task 1")
        db.create_task("flow-a", "Task 2")
        db.create_task("flow-a", "Task 3")

        results = db.list_tasks(limit=2)
        assert len(results) == 2

    def test_list_tasks_combined_filters(self, db: FlowstateDB) -> None:
        """Use both flow_name and status filters together."""
        db.create_task("flow-a", "A1")
        t2 = db.create_task("flow-a", "A2")
        db.create_task("flow-b", "B1")
        db.update_task_queue_status(t2, "running")

        results = db.list_tasks(flow_name="flow-a", status="queued")
        assert len(results) == 1
        assert results[0].title == "A1"

    def test_list_tasks_empty(self, db: FlowstateDB) -> None:
        """list_tasks on empty table returns empty list."""
        results = db.list_tasks()
        assert results == []

    def test_update_task_queue_status_to_running(self, db: FlowstateDB) -> None:
        """Transition to 'running' sets started_at automatically."""
        task_id = db.create_task("my-flow", "Task 1")
        db.update_task_queue_status(task_id, "running", current_node="plan")

        result = db.get_task(task_id)
        assert result is not None
        assert result.status == "running"
        assert result.current_node == "plan"
        assert result.started_at is not None

    def test_update_task_queue_status_to_completed(self, db: FlowstateDB) -> None:
        """Transition to 'completed' sets completed_at and output_json."""
        task_id = db.create_task("my-flow", "Task 1")
        db.update_task_queue_status(task_id, "running")
        db.update_task_queue_status(
            task_id,
            "completed",
            output_json='{"result": "done"}',
        )

        result = db.get_task(task_id)
        assert result is not None
        assert result.status == "completed"
        assert result.completed_at is not None
        assert result.output_json == '{"result": "done"}'

    def test_update_task_queue_status_to_failed(self, db: FlowstateDB) -> None:
        """Transition to 'failed' sets completed_at and error_message."""
        task_id = db.create_task("my-flow", "Task 1")
        db.update_task_queue_status(task_id, "running")
        db.update_task_queue_status(
            task_id,
            "failed",
            error_message="Node crashed",
        )

        result = db.get_task(task_id)
        assert result is not None
        assert result.status == "failed"
        assert result.completed_at is not None
        assert result.error_message == "Node crashed"

    def test_update_task_queue_status_with_flow_run_id(
        self, db: FlowstateDB, flow_run_id: str
    ) -> None:
        """Set flow_run_id when starting a task."""
        task_id = db.create_task("my-flow", "Task 1")
        db.update_task_queue_status(
            task_id,
            "running",
            flow_run_id=flow_run_id,
            current_node="entry",
        )

        result = db.get_task(task_id)
        assert result is not None
        assert result.flow_run_id == flow_run_id

    def test_update_task_queue_status_cancelled(self, db: FlowstateDB) -> None:
        """Transition to 'cancelled' sets completed_at."""
        task_id = db.create_task("my-flow", "Task 1")
        db.update_task_queue_status(task_id, "cancelled")

        result = db.get_task(task_id)
        assert result is not None
        assert result.status == "cancelled"
        assert result.completed_at is not None

    def test_update_task(self, db: FlowstateDB) -> None:
        """Edit mutable fields of a queued task."""
        task_id = db.create_task("my-flow", "Original Title", description="Old desc")

        db.update_task(
            task_id,
            title="New Title",
            description="New desc",
            params_json='{"key": "value"}',
            priority=10,
        )

        result = db.get_task(task_id)
        assert result is not None
        assert result.title == "New Title"
        assert result.description == "New desc"
        assert result.params_json == '{"key": "value"}'
        assert result.priority == 10

    def test_update_task_partial(self, db: FlowstateDB) -> None:
        """Update only some fields, others remain unchanged."""
        task_id = db.create_task("my-flow", "Original", description="Keep this")
        db.update_task(task_id, title="Changed")

        result = db.get_task(task_id)
        assert result is not None
        assert result.title == "Changed"
        assert result.description == "Keep this"

    def test_update_task_no_changes(self, db: FlowstateDB) -> None:
        """Calling update_task with no kwargs is a no-op."""
        task_id = db.create_task("my-flow", "No Change")
        db.update_task(task_id)  # should not raise

        result = db.get_task(task_id)
        assert result is not None
        assert result.title == "No Change"

    def test_delete_task_queued(self, db: FlowstateDB) -> None:
        """Deleting a queued task removes it from the database."""
        task_id = db.create_task("my-flow", "Delete me")
        db.delete_task(task_id)

        result = db.get_task(task_id)
        assert result is None

    def test_delete_task_running_is_noop(self, db: FlowstateDB) -> None:
        """Deleting a running task is a no-op (only queued tasks can be deleted)."""
        task_id = db.create_task("my-flow", "Running task")
        db.update_task_queue_status(task_id, "running")

        db.delete_task(task_id)

        result = db.get_task(task_id)
        assert result is not None
        assert result.status == "running"

    def test_delete_task_completed_is_noop(self, db: FlowstateDB) -> None:
        """Deleting a completed task is a no-op."""
        task_id = db.create_task("my-flow", "Done task")
        db.update_task_queue_status(task_id, "completed")

        db.delete_task(task_id)

        result = db.get_task(task_id)
        assert result is not None
        assert result.status == "completed"

    def test_delete_task_nonexistent_is_noop(self, db: FlowstateDB) -> None:
        """Deleting a non-existent task ID is a no-op."""
        db.delete_task("nonexistent-id")  # should not raise

    def test_get_next_queued_task_fifo(self, db: FlowstateDB) -> None:
        """When priorities are equal, the oldest task is returned first (FIFO)."""
        t1 = db.create_task("my-flow", "First")
        db.create_task("my-flow", "Second")

        result = db.get_next_queued_task("my-flow")
        assert result is not None
        assert result.id == t1
        assert result.title == "First"

    def test_get_next_queued_task_respects_priority(self, db: FlowstateDB) -> None:
        """Higher priority task is returned first even if created later."""
        db.create_task("my-flow", "Normal priority", priority=0)
        t2 = db.create_task("my-flow", "High priority", priority=10)

        result = db.get_next_queued_task("my-flow")
        assert result is not None
        assert result.id == t2
        assert result.title == "High priority"

    def test_get_next_queued_task_skips_running(self, db: FlowstateDB) -> None:
        """Running tasks are not returned by get_next_queued_task."""
        t1 = db.create_task("my-flow", "Running")
        db.create_task("my-flow", "Queued")
        db.update_task_queue_status(t1, "running")

        result = db.get_next_queued_task("my-flow")
        assert result is not None
        assert result.title == "Queued"

    def test_get_next_queued_task_empty(self, db: FlowstateDB) -> None:
        """No queued tasks returns None."""
        result = db.get_next_queued_task("my-flow")
        assert result is None

    def test_get_next_queued_task_filters_by_flow(self, db: FlowstateDB) -> None:
        """Only returns tasks for the specified flow."""
        db.create_task("flow-a", "Task A")
        db.create_task("flow-b", "Task B")

        result = db.get_next_queued_task("flow-a")
        assert result is not None
        assert result.flow_name == "flow-a"

    def test_count_running_tasks(self, db: FlowstateDB) -> None:
        """Count only tasks with status 'running' for the given flow."""
        t1 = db.create_task("my-flow", "Task 1")
        t2 = db.create_task("my-flow", "Task 2")
        db.create_task("my-flow", "Task 3")
        db.create_task("other-flow", "Other task")

        assert db.count_running_tasks("my-flow") == 0

        db.update_task_queue_status(t1, "running")
        assert db.count_running_tasks("my-flow") == 1

        db.update_task_queue_status(t2, "running")
        assert db.count_running_tasks("my-flow") == 2

        # Other flow is unaffected
        assert db.count_running_tasks("other-flow") == 0

    def test_count_running_tasks_no_flow(self, db: FlowstateDB) -> None:
        """count_running_tasks for a non-existent flow returns 0."""
        assert db.count_running_tasks("nonexistent") == 0

    def test_reorder_tasks(self, db: FlowstateDB) -> None:
        """Reorder queued tasks by setting priorities based on list position."""
        t1 = db.create_task("my-flow", "Task 1")
        t2 = db.create_task("my-flow", "Task 2")
        t3 = db.create_task("my-flow", "Task 3")

        # Reorder: t3 first, then t1, then t2
        db.reorder_tasks("my-flow", [t3, t1, t2])

        # t3 should now be the next queued task (highest priority)
        result = db.get_next_queued_task("my-flow")
        assert result is not None
        assert result.id == t3

        # Verify priorities: t3=2, t1=1, t2=0
        task3 = db.get_task(t3)
        task1 = db.get_task(t1)
        task2 = db.get_task(t2)
        assert task3 is not None and task3.priority == 2
        assert task1 is not None and task1.priority == 1
        assert task2 is not None and task2.priority == 0

    def test_reorder_tasks_ignores_non_queued(self, db: FlowstateDB) -> None:
        """Reorder only affects queued tasks; running tasks are untouched."""
        t1 = db.create_task("my-flow", "Task 1")
        t2 = db.create_task("my-flow", "Task 2")
        db.update_task_queue_status(t1, "running")

        db.reorder_tasks("my-flow", [t1, t2])

        # t1 is running -- its priority should not change
        task1 = db.get_task(t1)
        assert task1 is not None
        assert task1.status == "running"

    def test_task_node_history_crud(self, db: FlowstateDB) -> None:
        """Add, complete, and retrieve task node history entries."""
        task_id = db.create_task("my-flow", "Task with history")

        # Add two history entries
        h1 = db.add_task_node_history(task_id, "plan")
        h2 = db.add_task_node_history(task_id, "implement")

        assert h1 > 0
        assert h2 > h1

        # Complete the first entry
        db.complete_task_node_history(task_id, "plan")

        # Retrieve all history
        history = db.get_task_history(task_id)
        assert len(history) == 2

        assert history[0].node_name == "plan"
        assert history[0].started_at is not None
        assert history[0].completed_at is not None

        assert history[1].node_name == "implement"
        assert history[1].started_at is not None
        assert history[1].completed_at is None  # not completed yet

    def test_task_node_history_with_flow_run_id(self, db: FlowstateDB, flow_run_id: str) -> None:
        """Node history entries can be associated with a flow run."""
        task_id = db.create_task("my-flow", "Task with run")

        entry_id = db.add_task_node_history(task_id, "build", flow_run_id=flow_run_id)
        assert entry_id > 0

        history = db.get_task_history(task_id)
        assert len(history) == 1
        assert history[0].flow_run_id == flow_run_id

    def test_task_node_history_empty(self, db: FlowstateDB) -> None:
        """get_task_history for a task with no history returns empty list."""
        task_id = db.create_task("my-flow", "No history")
        history = db.get_task_history(task_id)
        assert history == []

    def test_get_child_tasks(self, db: FlowstateDB) -> None:
        """Create parent and child tasks, verify lineage."""
        parent_id = db.create_task("flow-a", "Parent task")
        child1_id = db.create_task(
            "flow-b",
            "Child 1",
            parent_task_id=parent_id,
            created_by="flow:flow-a/node:review",
        )
        child2_id = db.create_task(
            "flow-c",
            "Child 2",
            parent_task_id=parent_id,
            created_by="flow:flow-a/node:report",
        )

        children = db.get_child_tasks(parent_id)
        assert len(children) == 2
        child_ids = {c.id for c in children}
        assert child_ids == {child1_id, child2_id}

        # Verify parent_task_id is set
        for child in children:
            assert child.parent_task_id == parent_id

    def test_get_child_tasks_empty(self, db: FlowstateDB) -> None:
        """get_child_tasks for a task with no children returns empty list."""
        task_id = db.create_task("my-flow", "No children")
        children = db.get_child_tasks(task_id)
        assert children == []

    def test_get_child_tasks_nonexistent_parent(self, db: FlowstateDB) -> None:
        """get_child_tasks for a non-existent parent returns empty list."""
        children = db.get_child_tasks("nonexistent-parent-id")
        assert children == []

    def test_task_with_parent_lineage(self, db: FlowstateDB) -> None:
        """Multi-level parent-child lineage is tracked correctly."""
        grandparent = db.create_task("flow-a", "Grandparent")
        parent = db.create_task("flow-b", "Parent", parent_task_id=grandparent)
        child = db.create_task("flow-c", "Child", parent_task_id=parent)

        # Verify each level
        gp = db.get_task(grandparent)
        assert gp is not None
        assert gp.parent_task_id is None

        p = db.get_task(parent)
        assert p is not None
        assert p.parent_task_id == grandparent

        c = db.get_task(child)
        assert c is not None
        assert c.parent_task_id == parent

        # get_child_tasks only returns direct children
        gp_children = db.get_child_tasks(grandparent)
        assert len(gp_children) == 1
        assert gp_children[0].id == parent

        p_children = db.get_child_tasks(parent)
        assert len(p_children) == 1
        assert p_children[0].id == child

    def test_flow_run_task_id_field(self, db: FlowstateDB, flow_def_id: str) -> None:
        """FlowRunRow includes the new task_id field (defaults to None)."""
        run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/run",
            budget_seconds=300,
            on_error="pause",
        )
        result = db.get_flow_run(run_id)
        assert result is not None
        assert result.task_id is None


# ================================================================== #
# Task Scheduling Tests
# ================================================================== #


class TestTaskScheduling:
    """Tests for scheduled and recurring task functionality."""

    def test_create_task_with_scheduled_at_has_scheduled_status(self, db: FlowstateDB) -> None:
        """A task created with scheduled_at should have status 'scheduled'."""
        future = "2099-01-01T00:00:00+00:00"
        task_id = db.create_task(
            flow_name="my-flow",
            title="Deferred work",
            scheduled_at=future,
        )

        result = db.get_task(task_id)
        assert result is not None
        assert result.status == "scheduled"
        assert result.scheduled_at == future
        assert result.cron_expression is None

    def test_create_task_without_scheduled_at_has_queued_status(self, db: FlowstateDB) -> None:
        """A task created without scheduled_at should have status 'queued' (existing behaviour)."""
        task_id = db.create_task(flow_name="my-flow", title="Immediate work")

        result = db.get_task(task_id)
        assert result is not None
        assert result.status == "queued"
        assert result.scheduled_at is None

    def test_create_task_with_cron_expression(self, db: FlowstateDB) -> None:
        """A recurring task stores its cron_expression."""
        future = "2099-06-15T12:00:00+00:00"
        task_id = db.create_task(
            flow_name="my-flow",
            title="Recurring report",
            scheduled_at=future,
            cron_expression="0 12 * * *",
        )

        result = db.get_task(task_id)
        assert result is not None
        assert result.status == "scheduled"
        assert result.cron_expression == "0 12 * * *"
        assert result.scheduled_at == future

    def test_get_due_scheduled_tasks_returns_past_tasks(self, db: FlowstateDB) -> None:
        """get_due_scheduled_tasks returns tasks whose scheduled_at is in the past."""
        past = "2000-01-01T00:00:00+00:00"
        task_id = db.create_task(
            flow_name="my-flow",
            title="Overdue task",
            scheduled_at=past,
        )

        due = db.get_due_scheduled_tasks()
        assert len(due) >= 1
        ids = [t.id for t in due]
        assert task_id in ids

    def test_get_due_scheduled_tasks_excludes_future_tasks(self, db: FlowstateDB) -> None:
        """get_due_scheduled_tasks does NOT return tasks scheduled far in the future."""
        future = "2099-12-31T23:59:59+00:00"
        task_id = db.create_task(
            flow_name="my-flow",
            title="Far future task",
            scheduled_at=future,
        )

        due = db.get_due_scheduled_tasks()
        ids = [t.id for t in due]
        assert task_id not in ids

    def test_get_due_scheduled_tasks_excludes_non_scheduled(self, db: FlowstateDB) -> None:
        """get_due_scheduled_tasks ignores tasks with status other than 'scheduled'."""
        # Create a queued task (no scheduled_at)
        task_id = db.create_task(flow_name="my-flow", title="Immediate task")

        due = db.get_due_scheduled_tasks()
        ids = [t.id for t in due]
        assert task_id not in ids

    def test_get_due_scheduled_tasks_ordered_by_scheduled_at(self, db: FlowstateDB) -> None:
        """Due tasks are ordered by scheduled_at ascending (oldest first)."""
        t1 = db.create_task(
            flow_name="my-flow",
            title="First",
            scheduled_at="2000-01-01T00:00:00+00:00",
        )
        t2 = db.create_task(
            flow_name="my-flow",
            title="Second",
            scheduled_at="2000-06-01T00:00:00+00:00",
        )

        due = db.get_due_scheduled_tasks()
        ids = [t.id for t in due]
        assert ids.index(t1) < ids.index(t2)

    def test_get_due_scheduled_tasks_empty(self, db: FlowstateDB) -> None:
        """get_due_scheduled_tasks returns an empty list when there are no scheduled tasks."""
        assert db.get_due_scheduled_tasks() == []

    def test_create_next_recurring_task_computes_next_time(self, db: FlowstateDB) -> None:
        """create_next_recurring_task creates a new scheduled task with the correct next time."""
        past = "2000-01-01T00:00:00+00:00"
        task_id = db.create_task(
            flow_name="report-flow",
            title="Daily report",
            description="Runs every day at noon",
            params_json='{"format": "pdf"}',
            created_by="scheduler",
            priority=3,
            scheduled_at=past,
            cron_expression="0 12 * * *",
        )

        original = db.get_task(task_id)
        assert original is not None

        new_id = db.create_next_recurring_task(original)
        assert new_id is not None

        new_task = db.get_task(new_id)
        assert new_task is not None
        assert new_task.status == "scheduled"
        assert new_task.scheduled_at is not None
        assert new_task.cron_expression == "0 12 * * *"
        assert new_task.flow_name == "report-flow"
        assert new_task.title == "Daily report"
        assert new_task.description == "Runs every day at noon"
        assert new_task.params_json == '{"format": "pdf"}'
        assert new_task.created_by == "scheduler"
        assert new_task.priority == 3

        # The next scheduled_at should be in the future (after now)
        now = datetime.now(UTC).isoformat()
        assert new_task.scheduled_at > now

    def test_create_next_recurring_task_returns_none_without_cron(self, db: FlowstateDB) -> None:
        """create_next_recurring_task returns None if the task has no cron_expression."""
        task_id = db.create_task(
            flow_name="my-flow",
            title="One-time task",
            scheduled_at="2000-01-01T00:00:00+00:00",
        )

        original = db.get_task(task_id)
        assert original is not None
        assert db.create_next_recurring_task(original) is None

    def test_create_next_recurring_task_defaults_created_by(self, db: FlowstateDB) -> None:
        """create_next_recurring_task sets created_by to 'recurring' when original is None."""
        task_id = db.create_task(
            flow_name="my-flow",
            title="Recurring no author",
            scheduled_at="2000-01-01T00:00:00+00:00",
            cron_expression="*/5 * * * *",
        )

        original = db.get_task(task_id)
        assert original is not None
        assert original.created_by is None

        new_id = db.create_next_recurring_task(original)
        assert new_id is not None

        new_task = db.get_task(new_id)
        assert new_task is not None
        assert new_task.created_by == "recurring"

    def test_list_queued_flow_names_includes_due_scheduled(self, db: FlowstateDB) -> None:
        """list_queued_flow_names includes flows with due scheduled tasks."""
        past = "2000-01-01T00:00:00+00:00"
        db.create_task(
            flow_name="scheduled-flow",
            title="Due scheduled",
            scheduled_at=past,
        )

        names = db.list_queued_flow_names()
        assert "scheduled-flow" in names

    def test_list_queued_flow_names_excludes_future_scheduled(self, db: FlowstateDB) -> None:
        """list_queued_flow_names excludes flows with only future scheduled tasks."""
        future = "2099-12-31T23:59:59+00:00"
        db.create_task(
            flow_name="future-only-flow",
            title="Not yet due",
            scheduled_at=future,
        )

        names = db.list_queued_flow_names()
        assert "future-only-flow" not in names

    def test_get_next_queued_task_returns_due_scheduled(self, db: FlowstateDB) -> None:
        """get_next_queued_task picks up scheduled tasks that are due."""
        past = "2000-01-01T00:00:00+00:00"
        task_id = db.create_task(
            flow_name="my-flow",
            title="Due scheduled task",
            scheduled_at=past,
        )

        result = db.get_next_queued_task("my-flow")
        assert result is not None
        assert result.id == task_id

    def test_get_next_queued_task_ignores_future_scheduled(self, db: FlowstateDB) -> None:
        """get_next_queued_task does not return future scheduled tasks."""
        future = "2099-12-31T23:59:59+00:00"
        db.create_task(
            flow_name="my-flow",
            title="Future task",
            scheduled_at=future,
        )

        result = db.get_next_queued_task("my-flow")
        assert result is None

    def test_delete_task_scheduled(self, db: FlowstateDB) -> None:
        """delete_task removes a task in 'scheduled' status."""
        task_id = db.create_task(
            flow_name="my-flow",
            title="Delete me",
            scheduled_at="2099-01-01T00:00:00+00:00",
        )
        db.delete_task(task_id)
        assert db.get_task(task_id) is None


# ================================================================== #
# Flow Enable/Disable Tests
# ================================================================== #


class TestFlowEnabled:
    """Tests for flow enable/disable operations."""

    def test_default_is_enabled(self, db: FlowstateDB) -> None:
        """A flow with no explicit state is enabled by default."""
        assert db.is_flow_enabled("my-flow") is True

    def test_disable_flow(self, db: FlowstateDB) -> None:
        """Disabling a flow sets enabled to False."""
        db.set_flow_enabled("my-flow", False)
        assert db.is_flow_enabled("my-flow") is False

    def test_enable_flow(self, db: FlowstateDB) -> None:
        """Enabling a flow after disabling it sets enabled to True."""
        db.set_flow_enabled("my-flow", False)
        assert db.is_flow_enabled("my-flow") is False

        db.set_flow_enabled("my-flow", True)
        assert db.is_flow_enabled("my-flow") is True

    def test_enable_already_enabled(self, db: FlowstateDB) -> None:
        """Enabling an already-enabled flow is idempotent."""
        db.set_flow_enabled("my-flow", True)
        assert db.is_flow_enabled("my-flow") is True

        db.set_flow_enabled("my-flow", True)
        assert db.is_flow_enabled("my-flow") is True

    def test_disable_already_disabled(self, db: FlowstateDB) -> None:
        """Disabling an already-disabled flow is idempotent."""
        db.set_flow_enabled("my-flow", False)
        assert db.is_flow_enabled("my-flow") is False

        db.set_flow_enabled("my-flow", False)
        assert db.is_flow_enabled("my-flow") is False

    def test_independent_flows(self, db: FlowstateDB) -> None:
        """Different flows have independent enabled states."""
        db.set_flow_enabled("flow-a", False)
        db.set_flow_enabled("flow-b", True)

        assert db.is_flow_enabled("flow-a") is False
        assert db.is_flow_enabled("flow-b") is True
        assert db.is_flow_enabled("flow-c") is True  # default

    def test_toggle_multiple_times(self, db: FlowstateDB) -> None:
        """Toggling enabled state multiple times works correctly."""
        for _ in range(5):
            db.set_flow_enabled("my-flow", False)
            assert db.is_flow_enabled("my-flow") is False
            db.set_flow_enabled("my-flow", True)
            assert db.is_flow_enabled("my-flow") is True
