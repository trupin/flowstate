"""Tests for FlowstateDB repository CRUD operations.

Covers flow definitions, flow runs, task executions, edge transitions,
and compound transaction atomicity. All tests use in-memory SQLite.
"""

import sqlite3

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
