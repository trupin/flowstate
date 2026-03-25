"""Tests for user input REST API endpoints (SERVER-014).

Tests the message and interrupt endpoints:
- POST /api/runs/{run_id}/tasks/{task_execution_id}/message
- POST /api/runs/{run_id}/tasks/{task_execution_id}/interrupt

All tests mock the FlowExecutor. Uses FastAPI TestClient with mocked
FlowstateDB, RunManager, and WebSocket hub.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app
from flowstate.server.run_manager import RunManager
from flowstate.state.models import FlowRunRow, TaskExecutionRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_flow_run_row(
    run_id: str = "run-1",
    status: str = "running",
) -> FlowRunRow:
    return FlowRunRow(
        id=run_id,
        flow_definition_id="def-1",
        status=status,
        default_workspace=".",
        data_dir="/data/run-1",
        params_json=None,
        budget_seconds=600,
        elapsed_seconds=42.5,
        on_error="pause",
        started_at="2025-01-01T00:00:00+00:00",
        completed_at=None,
        created_at="2025-01-01T00:00:00+00:00",
        error_message=None,
    )


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


def _make_test_client(
    db_mock: MagicMock | None = None,
    run_manager: RunManager | None = None,
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

    if run_manager is None:
        run_manager = RunManager()
    app.state.run_manager = run_manager

    mock_ws_hub = MagicMock()
    mock_ws_hub.on_flow_event = MagicMock()
    app.state.ws_hub = mock_ws_hub

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/tasks/{task_execution_id}/message
# ---------------------------------------------------------------------------


class TestSendMessageRunningTask:
    """Message to a running task returns status=queued."""

    def test_message_running_task_returns_queued(self) -> None:
        mock_executor = MagicMock()
        mock_executor.send_message = AsyncMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(status="running")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/message",
            json={"message": "Please refactor this function"},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "queued"}
        mock_executor.send_message.assert_called_once_with(
            "task-1", "Please refactor this function"
        )

    def test_message_stores_task_log(self) -> None:
        """User message is persisted as a user_input task log entry."""
        mock_executor = MagicMock()
        mock_executor.send_message = AsyncMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(status="running")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        client.post(
            "/api/runs/run-1/tasks/task-1/message",
            json={"message": "Do something"},
        )

        mock_db.insert_task_log.assert_called_once()
        call_args = mock_db.insert_task_log.call_args
        assert call_args[0][0] == "task-1"
        assert call_args[0][1] == "user_input"
        assert '"message"' in call_args[0][2]
        assert "Do something" in call_args[0][2]

    def test_message_broadcasts_websocket_event(self) -> None:
        """User message triggers a WebSocket broadcast via on_flow_event."""
        mock_executor = MagicMock()
        mock_executor.send_message = AsyncMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(status="running")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        client.post(
            "/api/runs/run-1/tasks/task-1/message",
            json={"message": "hello"},
        )

        ws_hub = client.app.state.ws_hub  # type: ignore[union-attr]
        ws_hub.on_flow_event.assert_called_once()
        event = ws_hub.on_flow_event.call_args[0][0]
        assert event.type.value == "task.log"
        assert event.flow_run_id == "run-1"
        assert event.payload["task_execution_id"] == "task-1"
        assert event.payload["log_type"] == "user_input"


class TestSendMessageInterruptedTask:
    """Message to an interrupted task returns status=resumed."""

    def test_message_interrupted_task_returns_resumed(self) -> None:
        mock_executor = MagicMock()
        mock_executor.send_message = AsyncMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(status="interrupted")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/message",
            json={"message": "Continue with the fix"},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "resumed"}
        mock_executor.send_message.assert_called_once_with("task-1", "Continue with the fix")


class TestSendMessageErrors:
    """Error cases for the message endpoint."""

    def test_message_empty_returns_422(self) -> None:
        """Empty message string fails Pydantic validation (422)."""
        mock_executor = MagicMock()

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/message",
            json={"message": ""},
        )

        assert response.status_code == 422

    def test_message_missing_body_returns_422(self) -> None:
        """No body at all returns 422."""
        mock_executor = MagicMock()

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/message")

        assert response.status_code == 422

    def test_message_run_not_found_returns_404(self) -> None:
        """Message to a nonexistent run returns 404."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/runs/nonexistent/tasks/task-1/message",
            json={"message": "hello"},
        )

        assert response.status_code == 404

    def test_message_task_not_found_returns_404(self) -> None:
        """Message to a nonexistent task returns 404."""
        mock_executor = MagicMock()
        mock_executor.send_message = AsyncMock()

        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = None

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post(
            "/api/runs/run-1/tasks/nonexistent/message",
            json={"message": "hello"},
        )

        assert response.status_code == 404

    def test_message_task_wrong_run_returns_404(self) -> None:
        """Task exists but belongs to a different run returns 404."""
        mock_executor = MagicMock()
        mock_executor.send_message = AsyncMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(run_id="other-run")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/message",
            json={"message": "hello"},
        )

        assert response.status_code == 404

    def test_message_completed_task_returns_409(self) -> None:
        """Message to a completed task returns 409."""
        mock_executor = MagicMock()
        mock_executor.send_message = AsyncMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(status="completed")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/message",
            json={"message": "hello"},
        )

        assert response.status_code == 409
        assert "completed" in response.json()["error"]

    def test_message_failed_task_returns_409(self) -> None:
        """Message to a failed task returns 409."""
        mock_executor = MagicMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(status="failed")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/message",
            json={"message": "hello"},
        )

        assert response.status_code == 409
        assert "failed" in response.json()["error"]

    def test_message_no_active_executor_returns_409(self) -> None:
        """Run exists in DB but no active executor returns 409."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="completed")

        run_manager = RunManager()  # empty -- no active executors

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post(
            "/api/runs/run-1/tasks/task-1/message",
            json={"message": "hello"},
        )

        assert response.status_code == 409
        assert "not active" in response.json()["error"]


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/tasks/{task_execution_id}/interrupt
# ---------------------------------------------------------------------------


class TestInterruptTask:
    """Interrupt a running task."""

    def test_interrupt_running_task(self) -> None:
        mock_executor = MagicMock()
        mock_executor.interrupt_task = AsyncMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(status="running")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/interrupt")

        assert response.status_code == 200
        assert response.json() == {"status": "interrupted"}
        mock_executor.interrupt_task.assert_called_once_with("task-1")


class TestInterruptErrors:
    """Error cases for the interrupt endpoint."""

    def test_interrupt_run_not_found_returns_404(self) -> None:
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/runs/nonexistent/tasks/task-1/interrupt")

        assert response.status_code == 404

    def test_interrupt_task_not_found_returns_404(self) -> None:
        mock_executor = MagicMock()

        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = None

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/nonexistent/interrupt")

        assert response.status_code == 404

    def test_interrupt_task_wrong_run_returns_404(self) -> None:
        mock_executor = MagicMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(run_id="other-run")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/interrupt")

        assert response.status_code == 404

    def test_interrupt_completed_task_returns_409(self) -> None:
        mock_executor = MagicMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(status="completed")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/interrupt")

        assert response.status_code == 409
        assert "completed" in response.json()["error"]

    def test_interrupt_interrupted_task_returns_409(self) -> None:
        """Interrupting an already-interrupted task returns 409."""
        mock_executor = MagicMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(status="interrupted")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/interrupt")

        assert response.status_code == 409
        assert "interrupted" in response.json()["error"]

    def test_interrupt_failed_task_returns_409(self) -> None:
        mock_executor = MagicMock()

        mock_db = MagicMock()
        task = _make_task_execution_row(status="failed")
        mock_db.get_task_execution.return_value = task

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/interrupt")

        assert response.status_code == 409
        assert "failed" in response.json()["error"]

    def test_interrupt_no_active_executor_returns_409(self) -> None:
        """Run exists in DB but no active executor returns 409."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="completed")

        run_manager = RunManager()

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/interrupt")

        assert response.status_code == 409
        assert "not active" in response.json()["error"]


# ---------------------------------------------------------------------------
# Route handlers are async
# ---------------------------------------------------------------------------


class TestUserInputRoutesAsync:
    def test_user_input_routes_are_async(self) -> None:
        import asyncio

        from flowstate.server import routes

        for handler in [routes.send_task_message, routes.interrupt_task]:
            assert asyncio.iscoroutinefunction(
                handler
            ), f"{handler.__name__} is not an async function"
