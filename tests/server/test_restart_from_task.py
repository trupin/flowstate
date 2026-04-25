"""Tests for SERVER-018: retry/skip when no active executor exists.

Verifies that REST endpoints and WebSocket handlers reconstruct a FlowExecutor
via ``restart_from_task()`` when no active executor is available for a terminal
(cancelled/failed/budget_exceeded) flow run.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app
from flowstate.server.flow_registry import FlowRegistry
from flowstate.server.run_manager import RunManager
from flowstate.server.websocket import WebSocketHub
from flowstate.state.models import FlowDefinitionRow, FlowRunRow, TaskExecutionRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FLOW_SOURCE_DSL = (
    "flow code_review {\n"
    "    budget = 10m\n"
    "    on_error = pause\n"
    "    context = handoff\n"
    '    workspace = "."\n'
    "\n"
    "    entry start {\n"
    '        prompt = "go"\n'
    "    }\n"
    "\n"
    "    exit done {\n"
    '        prompt = "done"\n'
    "    }\n"
    "\n"
    "    start -> done\n"
    "}\n"
)

FLOW_DEF_ROW = FlowDefinitionRow(
    id="def-1",
    name="code_review",
    source_dsl=FLOW_SOURCE_DSL,
    ast_json="{}",
    created_at="2025-01-01T00:00:00+00:00",
    updated_at="2025-01-01T00:00:00+00:00",
)


def _make_task_row(
    task_id: str = "task-1",
    run_id: str = "run-1",
    status: str = "failed",
    node_name: str = "start",
) -> TaskExecutionRow:
    """Build a minimal TaskExecutionRow for restart-from-task tests."""
    return TaskExecutionRow(
        id=task_id,
        flow_run_id=run_id,
        node_name=node_name,
        node_type="entry",
        status=status,
        generation=1,
        context_mode="handoff",
        cwd=".",
        task_dir="/data/run-1/tasks/task-1",
        prompt_text="go",
        created_at="2025-01-01T00:00:00+00:00",
    )


def _make_flow_run_row(
    run_id: str = "run-1",
    flow_def_id: str = "def-1",
    status: str = "cancelled",
) -> FlowRunRow:
    return FlowRunRow(
        id=run_id,
        flow_definition_id=flow_def_id,
        status=status,
        default_workspace=".",
        data_dir="/data/run-1",
        params_json=None,
        budget_seconds=600,
        elapsed_seconds=10.0,
        on_error="pause",
        started_at="2025-01-01T00:00:00+00:00",
        completed_at=None,
        created_at="2025-01-01T00:00:00+00:00",
        error_message=None,
    )


def _make_test_client(
    db_mock: MagicMock | None = None,
    run_manager: RunManager | None = None,
) -> TestClient:
    """Create a TestClient with mocked dependencies for restart tests."""
    config = FlowstateConfig(watch_dir="/tmp/nonexistent-for-test")
    app = create_app(config=config)

    mock_registry = MagicMock(spec=FlowRegistry)
    mock_registry.list_flows.return_value = []
    mock_registry.get_flow.return_value = None
    app.state.flow_registry = mock_registry

    if db_mock is None:
        db_mock = MagicMock()
    app.state.db = db_mock

    if run_manager is None:
        run_manager = RunManager()
    app.state.run_manager = run_manager

    mock_ws_hub = MagicMock()
    mock_ws_hub.on_flow_event = MagicMock()
    app.state.ws_hub = mock_ws_hub

    return TestClient(app, raise_server_exceptions=False)


def _make_ws_test_client(
    ws_hub: WebSocketHub | None = None,
    run_manager: RunManager | None = None,
    db_mock: MagicMock | None = None,
) -> TestClient:
    """Create a TestClient with a real WebSocketHub for WS restart tests."""
    config = FlowstateConfig(watch_dir="/tmp/nonexistent-for-test")
    app = create_app(config=config)

    mock_registry = MagicMock(spec=FlowRegistry)
    mock_registry.list_flows.return_value = []
    mock_registry.get_flow.return_value = None
    app.state.flow_registry = mock_registry

    if db_mock is None:
        db_mock = MagicMock()
    app.state.db = db_mock

    if run_manager is None:
        run_manager = RunManager()
    app.state.run_manager = run_manager

    if ws_hub is None:
        ws_hub = WebSocketHub()
    ws_hub.set_run_manager(run_manager)
    ws_hub.set_db(db_mock)
    app.state.ws_hub = ws_hub

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# REST: retry/skip on terminal flows (no active executor)
# ---------------------------------------------------------------------------


class TestRetryTerminalFlowRestartsExecutor:
    """POST /api/runs/:id/tasks/:tid/retry reconstructs an executor for terminal flows."""

    def test_retry_cancelled_run_creates_executor(self) -> None:
        """Retry on a cancelled run creates a new executor via restart_from_task."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes._create_restart_executor") as mock_create,
        ):
            mock_flow_ast = MagicMock()
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.restart_from_task = AsyncMock(return_value="run-1")
            mock_create.return_value = mock_executor

            response = client.post("/api/runs/run-1/tasks/task-1/retry")

        assert response.status_code == 200
        assert response.json() == {"status": "running"}
        mock_executor.restart_from_task.assert_called_once_with(
            mock_flow_ast, "run-1", "task-1", "retry"
        )

    def test_retry_failed_run_creates_executor(self) -> None:
        """Retry on a failed run creates a new executor via restart_from_task."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="failed")
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes._create_restart_executor") as mock_create,
        ):
            mock_flow_ast = MagicMock()
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.restart_from_task = AsyncMock(return_value="run-1")
            mock_create.return_value = mock_executor

            response = client.post("/api/runs/run-1/tasks/task-1/retry")

        assert response.status_code == 200
        assert response.json() == {"status": "running"}

    def test_retry_budget_exceeded_run_creates_executor(self) -> None:
        """Retry on a budget_exceeded run creates a new executor."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="budget_exceeded")
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes._create_restart_executor") as mock_create,
        ):
            mock_flow_ast = MagicMock()
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.restart_from_task = AsyncMock(return_value="run-1")
            mock_create.return_value = mock_executor

            response = client.post("/api/runs/run-1/tasks/task-1/retry")

        assert response.status_code == 200
        assert response.json() == {"status": "running"}


class TestSkipTerminalFlowRestartsExecutor:
    """POST /api/runs/:id/tasks/:tid/skip reconstructs an executor for terminal flows."""

    def test_skip_cancelled_run_creates_executor(self) -> None:
        """Skip on a cancelled run creates a new executor via restart_from_task."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes._create_restart_executor") as mock_create,
        ):
            mock_flow_ast = MagicMock()
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.restart_from_task = AsyncMock(return_value="run-1")
            mock_create.return_value = mock_executor

            response = client.post("/api/runs/run-1/tasks/task-1/skip")

        assert response.status_code == 200
        assert response.json() == {"status": "skipped"}
        mock_executor.restart_from_task.assert_called_once_with(
            mock_flow_ast, "run-1", "task-1", "skip"
        )


# ---------------------------------------------------------------------------
# REST: retry/skip error cases
# ---------------------------------------------------------------------------


class TestRetrySkipErrorCases:
    """Error responses for retry/skip without an active executor."""

    def test_retry_nonexistent_run_returns_404(self) -> None:
        """Retry on a non-existent run returns 404."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = None

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        response = client.post("/api/runs/run-1/tasks/task-1/retry")
        assert response.status_code == 404

    def test_skip_nonexistent_run_returns_404(self) -> None:
        """Skip on a non-existent run returns 404."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = None

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        response = client.post("/api/runs/run-1/tasks/task-1/skip")
        assert response.status_code == 404

    def test_retry_completed_run_returns_409(self) -> None:
        """Retry on a completed (non-restartable) run returns 409."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="completed")

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        response = client.post("/api/runs/run-1/tasks/task-1/retry")
        assert response.status_code == 409
        assert "restartable" in response.json()["error"]

    def test_retry_orphaned_running_run_restarts(self) -> None:
        """Retry on an orphaned running run (no executor) restarts via restart_from_task.

        A run in status ``running`` but without an active executor is treated as
        orphaned (e.g., the server was restarted mid-flight). ``running`` is in
        ``_RUN_RESTARTABLE_STATUSES`` so retry reconstructs a fresh executor and
        restarts from the failed task.
        """
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="running")
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes._create_restart_executor") as mock_create,
        ):
            mock_flow_ast = MagicMock()
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.restart_from_task = AsyncMock(return_value="run-1")
            mock_create.return_value = mock_executor

            response = client.post("/api/runs/run-1/tasks/task-1/retry")

        assert response.status_code == 200
        assert response.json() == {"status": "running"}
        mock_executor.restart_from_task.assert_called_once_with(
            mock_flow_ast, "run-1", "task-1", "retry"
        )

    def test_retry_missing_flow_definition_returns_404(self) -> None:
        """Retry fails with 404 when the flow definition is missing."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_flow_definition.return_value = None

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        response = client.post("/api/runs/run-1/tasks/task-1/retry")
        assert response.status_code == 404

    def test_retry_invalid_flow_dsl_returns_400(self) -> None:
        """Retry fails with 400 when the flow DSL cannot be parsed."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_flow_definition.return_value = FlowDefinitionRow(
            id="def-bad",
            name="broken",
            source_dsl="invalid dsl that will not parse",
            ast_json="{}",
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
        )

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        with patch(
            "flowstate.server.routes.parse_flow",
            side_effect=Exception("Parse error"),
        ):
            response = client.post("/api/runs/run-1/tasks/task-1/retry")

        assert response.status_code == 400
        assert "parse" in response.json()["error"].lower()


# ---------------------------------------------------------------------------
# REST: retry/skip with active executor (existing behavior preserved)
# ---------------------------------------------------------------------------


class TestRetrySkipWithActiveExecutor:
    """Existing behavior is preserved when an active executor exists."""

    def test_retry_with_active_executor_delegates(self) -> None:
        """Retry delegates to the active executor when one exists."""
        mock_executor = MagicMock()
        mock_executor.retry_task = AsyncMock()
        mock_executor._flow_run_id = "db-run-1"

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/retry")

        assert response.status_code == 200
        assert response.json() == {"status": "running"}
        mock_executor.retry_task.assert_called_once_with("db-run-1", "task-1")

    def test_skip_with_active_executor_delegates(self) -> None:
        """Skip delegates to the active executor when one exists."""
        mock_executor = MagicMock()
        mock_executor.skip_task = AsyncMock()
        mock_executor._flow_run_id = "db-run-1"

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/skip")

        assert response.status_code == 200
        assert response.json() == {"status": "skipped"}
        mock_executor.skip_task.assert_called_once_with("db-run-1", "task-1")


# ---------------------------------------------------------------------------
# WebSocket: retry_task/skip_task with no active executor
# ---------------------------------------------------------------------------


class TestWsRetryNoExecutorRestartsFlow:
    """WebSocket retry_task attempts to restart when no active executor."""

    def test_ws_retry_no_executor_attempts_restart(self) -> None:
        """Send retry_task with no executor. Verify restart is attempted."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW
        mock_db.get_task_execution.return_value = _make_task_row(
            task_id="task-1", run_id="run-1", status="failed"
        )

        run_manager = RunManager()
        ws_hub = WebSocketHub()

        # Set executor config so restart can create an executor
        mock_harness = MagicMock()
        ws_hub.set_executor_config(
            harness=mock_harness,
            max_concurrent=4,
            worktree_cleanup=True,
        )

        client = _make_ws_test_client(
            ws_hub=ws_hub,
            run_manager=run_manager,
            db_mock=mock_db,
        )

        with (
            patch("flowstate.dsl.parser.parse_flow") as mock_parse,
            patch("flowstate.engine.executor.FlowExecutor") as mock_executor_cls,
        ):
            mock_flow_ast = MagicMock()
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.restart_from_task = AsyncMock(return_value="run-1")
            mock_executor_cls.return_value = mock_executor

            with client.websocket_connect("/ws") as ws:
                ws.send_json(
                    {
                        "action": "retry_task",
                        "flow_run_id": "run-1",
                        "payload": {"task_execution_id": "task-1"},
                    }
                )
                # Send sentinel to ensure retry_task is processed.
                # _try_restart_from_task does not currently emit an ack, so the
                # only response we expect is the error for unknown __ping__.
                ws.send_json({"action": "__ping__"})
                ws.receive_json()  # Consume the error for unknown __ping__

            mock_executor.restart_from_task.assert_called_once_with(
                mock_flow_ast, "run-1", "task-1", "retry"
            )


class TestWsSkipNoExecutorRestartsFlow:
    """WebSocket skip_task attempts to restart when no active executor."""

    def test_ws_skip_no_executor_attempts_restart(self) -> None:
        """Send skip_task with no executor. Verify restart is attempted."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="failed")
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW
        mock_db.get_task_execution.return_value = _make_task_row(
            task_id="task-1", run_id="run-1", status="failed"
        )

        run_manager = RunManager()
        ws_hub = WebSocketHub()

        mock_harness = MagicMock()
        ws_hub.set_executor_config(
            harness=mock_harness,
            max_concurrent=4,
            worktree_cleanup=True,
        )

        client = _make_ws_test_client(
            ws_hub=ws_hub,
            run_manager=run_manager,
            db_mock=mock_db,
        )

        with (
            patch("flowstate.dsl.parser.parse_flow") as mock_parse,
            patch("flowstate.engine.executor.FlowExecutor") as mock_executor_cls,
        ):
            mock_flow_ast = MagicMock()
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.restart_from_task = AsyncMock(return_value="run-1")
            mock_executor_cls.return_value = mock_executor

            with client.websocket_connect("/ws") as ws:
                ws.send_json(
                    {
                        "action": "skip_task",
                        "flow_run_id": "run-1",
                        "payload": {"task_execution_id": "task-1"},
                    }
                )
                # Send sentinel to ensure skip_task is processed
                ws.send_json({"action": "__ping__"})
                ws.receive_json()

            mock_executor.restart_from_task.assert_called_once_with(
                mock_flow_ast, "run-1", "task-1", "skip"
            )


# ---------------------------------------------------------------------------
# WebSocket: error responses
# ---------------------------------------------------------------------------


class TestWsTaskControlErrorResponses:
    """WebSocket handler sends error responses instead of silently failing."""

    def test_ws_retry_no_executor_non_restartable_sends_error(self) -> None:
        """retry_task on a completed run (non-restartable) sends an error response."""
        mock_db = MagicMock()
        # completed is not restartable
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="completed")

        run_manager = RunManager()
        ws_hub = WebSocketHub()
        mock_harness = MagicMock()
        ws_hub.set_executor_config(harness=mock_harness)

        client = _make_ws_test_client(
            ws_hub=ws_hub,
            run_manager=run_manager,
            db_mock=mock_db,
        )

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert "No active executor" in response["payload"]["message"]

    def test_ws_retry_missing_task_id_sends_error(self) -> None:
        """retry_task without task_execution_id sends an error response."""
        run_manager = RunManager()
        ws_hub = WebSocketHub()
        client = _make_ws_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert "task_execution_id" in response["payload"]["message"]

    def test_ws_skip_missing_task_id_sends_error(self) -> None:
        """skip_task without task_execution_id sends an error response."""
        run_manager = RunManager()
        ws_hub = WebSocketHub()
        client = _make_ws_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "skip_task",
                    "flow_run_id": "run-1",
                    "payload": {},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert "task_execution_id" in response["payload"]["message"]

    def test_ws_retry_executor_error_sends_error_response(self) -> None:
        """If executor.retry_task raises, an error is sent to the client."""
        mock_executor = MagicMock()
        mock_executor.retry_task = AsyncMock(side_effect=ValueError("Can only retry failed tasks"))

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_ws_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert response["payload"]["action"] == "retry_task"
        assert "Failed to retry task" in response["payload"]["message"]
        assert "Can only retry failed tasks" in response["payload"]["message"]

    def test_ws_skip_executor_error_sends_error_response(self) -> None:
        """If executor.skip_task raises, an error is sent to the client."""
        mock_executor = MagicMock()
        mock_executor.skip_task = AsyncMock(side_effect=RuntimeError("Flow run not found"))

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_ws_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "skip_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert response["payload"]["action"] == "skip_task"
        assert "Failed to skip task" in response["payload"]["message"]
        assert "Flow run not found" in response["payload"]["message"]


class TestWsNoHarnessConfigSkipsRestart:
    """WebSocket restart is not attempted when harness config is not set."""

    def test_ws_retry_no_harness_sends_error(self) -> None:
        """retry_task with no harness config sends error without attempting restart."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")

        run_manager = RunManager()
        ws_hub = WebSocketHub()
        # Do NOT call set_executor_config -- harness will be None

        client = _make_ws_test_client(
            ws_hub=ws_hub,
            run_manager=run_manager,
            db_mock=mock_db,
        )

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert "No active executor" in response["payload"]["message"]


# ---------------------------------------------------------------------------
# WebSocket: validation of task_execution_id on restart path (FAIL-1 fix)
# ---------------------------------------------------------------------------


class TestWsRestartValidatesTaskId:
    """_try_restart_from_task validates task_execution_id before hijacking state.

    Regression for FAIL-1 in issues/evals/UI-072-eval.md: sending retry_task
    against a terminal run with an invalid ``task_execution_id`` used to
    silently transition the flow to ``running`` without returning an error.
    These tests lock in the new validation behavior.
    """

    def _build_client(
        self, mock_db: MagicMock
    ) -> tuple[TestClient, WebSocketHub, RunManager, MagicMock]:
        run_manager = RunManager()
        ws_hub = WebSocketHub()
        mock_harness = MagicMock()
        ws_hub.set_executor_config(
            harness=mock_harness,
            max_concurrent=4,
            worktree_cleanup=True,
        )
        client = _make_ws_test_client(
            ws_hub=ws_hub,
            run_manager=run_manager,
            db_mock=mock_db,
        )
        return client, ws_hub, run_manager, mock_harness

    def test_ws_retry_invalid_task_id_sends_error(self) -> None:
        """retry_task with unknown task_execution_id returns an error.

        Also verifies no executor is registered and no status change occurs.
        """
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_task_execution.return_value = None  # unknown task id

        client, _, run_manager, _ = self._build_client(mock_db)

        with (
            patch("flowstate.dsl.parser.parse_flow") as mock_parse,
            patch("flowstate.engine.executor.FlowExecutor") as mock_executor_cls,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "00000000-0000-0000-0000-000000000000"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert response["payload"]["action"] == "retry_task"
        assert response["payload"]["flow_run_id"] == "run-1"
        assert response["payload"]["task_execution_id"] == ("00000000-0000-0000-0000-000000000000")
        assert "not found" in response["payload"]["message"]
        # Executor must NOT have been constructed: the whole point is that
        # validation runs before the state-machine is touched.
        mock_executor_cls.assert_not_called()
        mock_parse.assert_not_called()
        # run_manager should have no registered executors
        assert run_manager.get_executor("run-1") is None

    def test_ws_retry_task_from_wrong_run_sends_error(self) -> None:
        """retry_task with a task that belongs to a different run returns an error."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_task_execution.return_value = _make_task_row(
            task_id="task-1", run_id="other-run", status="failed"
        )

        client, _, run_manager, _ = self._build_client(mock_db)

        with (
            patch("flowstate.dsl.parser.parse_flow") as mock_parse,
            patch("flowstate.engine.executor.FlowExecutor") as mock_executor_cls,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert response["payload"]["action"] == "retry_task"
        assert "does not belong to run" in response["payload"]["message"]
        mock_executor_cls.assert_not_called()
        mock_parse.assert_not_called()
        assert run_manager.get_executor("run-1") is None

    def test_ws_retry_task_not_failed_sends_error(self) -> None:
        """retry_task against a completed task returns an error."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_task_execution.return_value = _make_task_row(
            task_id="task-1", run_id="run-1", status="completed"
        )

        client, _, run_manager, _ = self._build_client(mock_db)

        with (
            patch("flowstate.dsl.parser.parse_flow") as mock_parse,
            patch("flowstate.engine.executor.FlowExecutor") as mock_executor_cls,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert response["payload"]["action"] == "retry_task"
        assert "Can only retry failed tasks" in response["payload"]["message"]
        assert "completed" in response["payload"]["message"]
        mock_executor_cls.assert_not_called()
        mock_parse.assert_not_called()
        assert run_manager.get_executor("run-1") is None

    def test_ws_retry_task_pending_sends_error(self) -> None:
        """retry_task against a pending task returns an error."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_task_execution.return_value = _make_task_row(
            task_id="task-1", run_id="run-1", status="pending"
        )

        client, _, run_manager, _ = self._build_client(mock_db)

        with (
            patch("flowstate.dsl.parser.parse_flow") as mock_parse,
            patch("flowstate.engine.executor.FlowExecutor") as mock_executor_cls,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert "Can only retry failed tasks" in response["payload"]["message"]
        assert "pending" in response["payload"]["message"]
        mock_executor_cls.assert_not_called()
        mock_parse.assert_not_called()
        assert run_manager.get_executor("run-1") is None

    def test_ws_skip_invalid_task_id_sends_error(self) -> None:
        """skip_task with unknown task_execution_id returns an error."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="failed")
        mock_db.get_task_execution.return_value = None

        client, _, run_manager, _ = self._build_client(mock_db)

        with (
            patch("flowstate.dsl.parser.parse_flow") as mock_parse,
            patch("flowstate.engine.executor.FlowExecutor") as mock_executor_cls,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(
                {
                    "action": "skip_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "does-not-exist"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert response["payload"]["action"] == "skip_task"
        assert response["payload"]["task_execution_id"] == "does-not-exist"
        assert "not found" in response["payload"]["message"]
        mock_executor_cls.assert_not_called()
        mock_parse.assert_not_called()
        assert run_manager.get_executor("run-1") is None

    def test_ws_skip_task_from_wrong_run_sends_error(self) -> None:
        """skip_task with a task belonging to a different run returns an error."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="failed")
        mock_db.get_task_execution.return_value = _make_task_row(
            task_id="task-1", run_id="other-run", status="failed"
        )

        client, _, run_manager, _ = self._build_client(mock_db)

        with (
            patch("flowstate.dsl.parser.parse_flow") as mock_parse,
            patch("flowstate.engine.executor.FlowExecutor") as mock_executor_cls,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(
                {
                    "action": "skip_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert response["payload"]["action"] == "skip_task"
        assert "does not belong to run" in response["payload"]["message"]
        mock_executor_cls.assert_not_called()
        mock_parse.assert_not_called()
        assert run_manager.get_executor("run-1") is None

    def test_ws_skip_task_not_failed_sends_error(self) -> None:
        """skip_task against a non-failed task returns an error."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="failed")
        mock_db.get_task_execution.return_value = _make_task_row(
            task_id="task-1", run_id="run-1", status="running"
        )

        client, _, run_manager, _ = self._build_client(mock_db)

        with (
            patch("flowstate.dsl.parser.parse_flow") as mock_parse,
            patch("flowstate.engine.executor.FlowExecutor") as mock_executor_cls,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(
                {
                    "action": "skip_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()

        assert response["type"] == "error"
        assert "Can only skip failed tasks" in response["payload"]["message"]
        assert "running" in response["payload"]["message"]
        mock_executor_cls.assert_not_called()
        mock_parse.assert_not_called()
        assert run_manager.get_executor("run-1") is None
