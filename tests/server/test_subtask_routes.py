"""Tests for agent subtask REST API endpoints (SERVER-015).

Tests the subtask CRUD endpoints:
- POST /api/runs/{run_id}/tasks/{task_execution_id}/subtasks
- GET /api/runs/{run_id}/tasks/{task_execution_id}/subtasks
- PATCH /api/runs/{run_id}/tasks/{task_execution_id}/subtasks/{subtask_id}

All tests mock the FlowstateDB. Uses FastAPI TestClient with mocked
dependencies on app.state.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app
from flowstate.state.models import AgentSubtaskRow, TaskExecutionRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task_execution_row(
    task_id: str = "task-1",
    run_id: str = "run-1",
    status: str = "running",
) -> TaskExecutionRow:
    return TaskExecutionRow(
        id=task_id,
        flow_run_id=run_id,
        node_name="build",
        node_type="task",
        status=status,
        generation=1,
        context_mode="handoff",
        cwd=".",
        task_dir="/data/run-1/build_1",
        prompt_text="go",
        started_at="2025-01-01T00:00:10+00:00",
        completed_at=None,
        elapsed_seconds=5.0,
        exit_code=None,
        summary_path=None,
        error_message=None,
        created_at="2025-01-01T00:00:00+00:00",
    )


def _make_subtask_row(
    subtask_id: str = "sub-1",
    task_execution_id: str = "task-1",
    title: str = "Write unit tests",
    status: str = "todo",
) -> AgentSubtaskRow:
    return AgentSubtaskRow(
        id=subtask_id,
        task_execution_id=task_execution_id,
        title=title,
        status=status,
        created_at="2025-01-01T00:01:00+00:00",
        updated_at="2025-01-01T00:01:00+00:00",
    )


def _make_test_client(
    db_mock: MagicMock | None = None,
) -> TestClient:
    """Create a TestClient with mocked dependencies on app.state."""
    config = FlowstateConfig(watch_dir="/tmp/nonexistent-for-test")
    app = create_app(config=config)

    if db_mock is None:
        db_mock = MagicMock()
    app.state.db = db_mock

    mock_registry = MagicMock()
    mock_registry.list_flows.return_value = []
    mock_registry.get_flow.return_value = None
    app.state.flow_registry = mock_registry

    mock_ws_hub = MagicMock()
    mock_ws_hub.on_flow_event = MagicMock()
    app.state.ws_hub = mock_ws_hub

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/tasks/{task_execution_id}/subtasks
# ---------------------------------------------------------------------------


class TestCreateSubtask:
    """POST creates a subtask and returns 201."""

    def test_create_subtask_returns_201(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.count_agent_subtasks.return_value = 0
        subtask = _make_subtask_row()
        mock_db.create_agent_subtask.return_value = subtask

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/subtasks",
            json={"title": "Write unit tests"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "sub-1"
        assert data["task_execution_id"] == "task-1"
        assert data["title"] == "Write unit tests"
        assert data["status"] == "todo"
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_subtask_calls_db(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.count_agent_subtasks.return_value = 0
        mock_db.create_agent_subtask.return_value = _make_subtask_row()

        client = _make_test_client(db_mock=mock_db)
        client.post(
            "/api/runs/run-1/tasks/task-1/subtasks",
            json={"title": "Write unit tests"},
        )

        mock_db.create_agent_subtask.assert_called_once_with("task-1", "Write unit tests")

    def test_create_subtask_emits_websocket_event(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.count_agent_subtasks.return_value = 0
        mock_db.create_agent_subtask.return_value = _make_subtask_row()

        client = _make_test_client(db_mock=mock_db)
        client.post(
            "/api/runs/run-1/tasks/task-1/subtasks",
            json={"title": "Write unit tests"},
        )

        ws_hub = client.app.state.ws_hub  # type: ignore[union-attr]
        ws_hub.on_flow_event.assert_called_once()
        event = ws_hub.on_flow_event.call_args[0][0]
        assert event.type.value == "subtask.updated"
        assert event.flow_run_id == "run-1"
        assert event.payload["subtask_id"] == "sub-1"
        assert event.payload["task_execution_id"] == "task-1"
        assert event.payload["title"] == "Write unit tests"
        assert event.payload["status"] == "todo"

    def test_create_subtask_nonexistent_task_returns_404(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/runs/run-1/tasks/nonexistent/subtasks",
            json={"title": "Write unit tests"},
        )

        assert response.status_code == 404

    def test_create_subtask_task_wrong_run_returns_404(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row(run_id="other-run")

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/subtasks",
            json={"title": "Write unit tests"},
        )

        assert response.status_code == 404

    def test_create_subtask_empty_title_returns_422(self) -> None:
        """Empty title string fails Pydantic validation (422)."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/subtasks",
            json={"title": ""},
        )

        assert response.status_code == 422

    def test_create_subtask_title_too_long_returns_422(self) -> None:
        """Title exceeding 200 characters fails Pydantic validation (422)."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/subtasks",
            json={"title": "x" * 201},
        )

        assert response.status_code == 422

    def test_create_subtask_title_at_max_length_succeeds(self) -> None:
        """Title exactly at 200 characters is accepted."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.count_agent_subtasks.return_value = 0
        mock_db.create_agent_subtask.return_value = _make_subtask_row(title="x" * 200)

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/subtasks",
            json={"title": "x" * 200},
        )

        assert response.status_code == 201

    def test_create_subtask_exceeds_limit_returns_400(self) -> None:
        """Creating the 51st subtask returns 400 with a clear message."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.count_agent_subtasks.return_value = 50

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/subtasks",
            json={"title": "One too many"},
        )

        assert response.status_code == 400
        data = response.json()
        assert "limit" in data["error"].lower() or "50" in data["error"]
        assert "detail" in data

    def test_create_subtask_at_limit_succeeds(self) -> None:
        """Creating the 50th subtask (exactly at limit) succeeds."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.count_agent_subtasks.return_value = 49
        mock_db.create_agent_subtask.return_value = _make_subtask_row(subtask_id="sub-49")

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/subtasks",
            json={"title": "Just under the wire"},
        )

        assert response.status_code == 201


# ---------------------------------------------------------------------------
# Error response format
# ---------------------------------------------------------------------------


class TestSubtaskErrorFormat:
    """Verify error responses include a 'detail' field for consistent parsing."""

    def test_invalid_status_error_has_detail_field(self) -> None:
        """400 error from invalid status includes 'detail' in JSON."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()

        client = _make_test_client(db_mock=mock_db)
        response = client.patch(
            "/api/runs/run-1/tasks/task-1/subtasks/sub-1",
            json={"status": "bogus"},
        )

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)
        assert "bogus" in data["detail"]

    def test_subtask_not_found_error_has_detail_field(self) -> None:
        """404 error from missing subtask includes 'detail' in JSON."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.update_agent_subtask.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.patch(
            "/api/runs/run-1/tasks/task-1/subtasks/no-such-id",
            json={"status": "done"},
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)

    def test_subtask_limit_error_has_detail_field(self) -> None:
        """400 error from subtask limit includes 'detail' in JSON."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.count_agent_subtasks.return_value = 50

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/subtasks",
            json={"title": "Overflow"},
        )

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "50" in data["detail"]


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/tasks/{task_execution_id}/subtasks
# ---------------------------------------------------------------------------


class TestListSubtasks:
    """GET returns subtasks for a task execution."""

    def test_list_subtasks_empty(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.list_agent_subtasks.return_value = []

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/tasks/task-1/subtasks")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_subtasks_returns_in_creation_order(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        subtask_1 = _make_subtask_row(
            subtask_id="sub-1",
            title="First",
        )
        subtask_2 = _make_subtask_row(
            subtask_id="sub-2",
            title="Second",
            status="in_progress",
        )
        mock_db.list_agent_subtasks.return_value = [subtask_1, subtask_2]

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/tasks/task-1/subtasks")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == "sub-1"
        assert data[0]["title"] == "First"
        assert data[1]["id"] == "sub-2"
        assert data[1]["title"] == "Second"
        assert data[1]["status"] == "in_progress"

    def test_list_subtasks_nonexistent_task_returns_404(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/tasks/nonexistent/subtasks")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/runs/{run_id}/tasks/{task_execution_id}/subtasks/{subtask_id}
# ---------------------------------------------------------------------------


class TestUpdateSubtask:
    """PATCH updates subtask status."""

    def test_update_subtask_returns_200(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        updated = _make_subtask_row(status="in_progress")
        mock_db.update_agent_subtask.return_value = updated

        client = _make_test_client(db_mock=mock_db)
        response = client.patch(
            "/api/runs/run-1/tasks/task-1/subtasks/sub-1",
            json={"status": "in_progress"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "sub-1"
        assert data["status"] == "in_progress"

    def test_update_subtask_to_done(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        updated = _make_subtask_row(status="done")
        mock_db.update_agent_subtask.return_value = updated

        client = _make_test_client(db_mock=mock_db)
        response = client.patch(
            "/api/runs/run-1/tasks/task-1/subtasks/sub-1",
            json={"status": "done"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "done"

    def test_update_subtask_calls_db(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.update_agent_subtask.return_value = _make_subtask_row(status="done")

        client = _make_test_client(db_mock=mock_db)
        client.patch(
            "/api/runs/run-1/tasks/task-1/subtasks/sub-1",
            json={"status": "done"},
        )

        mock_db.update_agent_subtask.assert_called_once_with("sub-1", "done")

    def test_update_subtask_emits_websocket_event(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        updated = _make_subtask_row(status="done")
        mock_db.update_agent_subtask.return_value = updated

        client = _make_test_client(db_mock=mock_db)
        client.patch(
            "/api/runs/run-1/tasks/task-1/subtasks/sub-1",
            json={"status": "done"},
        )

        ws_hub = client.app.state.ws_hub  # type: ignore[union-attr]
        ws_hub.on_flow_event.assert_called_once()
        event = ws_hub.on_flow_event.call_args[0][0]
        assert event.type.value == "subtask.updated"
        assert event.flow_run_id == "run-1"
        assert event.payload["subtask_id"] == "sub-1"
        assert event.payload["status"] == "done"

    def test_update_subtask_not_found_returns_404(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()
        mock_db.update_agent_subtask.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.patch(
            "/api/runs/run-1/tasks/task-1/subtasks/nonexistent",
            json={"status": "done"},
        )

        assert response.status_code == 404
        assert "nonexistent" in response.json()["error"]

    def test_update_subtask_invalid_status_returns_400(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution_row()

        client = _make_test_client(db_mock=mock_db)
        response = client.patch(
            "/api/runs/run-1/tasks/task-1/subtasks/sub-1",
            json={"status": "invalid_status"},
        )

        assert response.status_code == 400
        assert "invalid_status" in response.json()["error"]

    def test_update_subtask_task_not_found_returns_404(self) -> None:
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.patch(
            "/api/runs/run-1/tasks/nonexistent/subtasks/sub-1",
            json={"status": "done"},
        )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Route handlers are async
# ---------------------------------------------------------------------------


class TestSubtaskRoutesAsync:
    def test_subtask_routes_are_async(self) -> None:
        import asyncio

        from flowstate.server import routes

        for handler in [routes.create_subtask, routes.list_subtasks, routes.update_subtask]:
            assert asyncio.iscoroutinefunction(
                handler
            ), f"{handler.__name__} is not an async function"
