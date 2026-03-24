"""Tests for run management REST API endpoints (SERVER-003).

All tests mock the FlowExecutor -- never run real flows. Uses FastAPI TestClient
with mocked FlowRegistry, RunManager, and FlowstateDB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app
from flowstate.server.flow_registry import DiscoveredFlow, FlowRegistry
from flowstate.server.run_manager import InvalidStateError, RunManager
from flowstate.state.models import (
    EdgeTransitionRow,
    FlowDefinitionRow,
    FlowRunRow,
    TaskExecutionRow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_FLOW = DiscoveredFlow(
    id="code_review",
    name="code_review",
    file_path="/flows/code_review.flow",
    source_dsl=(
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
    ),
    status="valid",
    errors=[],
    ast_json={"name": "code_review", "nodes": {}, "edges": []},
    params=[{"name": "focus", "type": "string", "default_value": "all"}],
)

VALID_FLOW_WITH_REQUIRED_PARAM = DiscoveredFlow(
    id="needs_param",
    name="needs_param",
    file_path="/flows/needs_param.flow",
    source_dsl=(
        "flow needs_param {\n"
        "    budget = 10m\n"
        "    on_error = pause\n"
        "    context = handoff\n"
        '    workspace = "."\n'
        "\n"
        "    input {\n"
        "        focus: string\n"
        "    }\n"
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
    ),
    status="valid",
    errors=[],
    ast_json={"name": "needs_param", "nodes": {}, "edges": []},
    params=[{"name": "focus", "type": "string", "default_value": None}],
)

VALID_FLOW_NO_WORKSPACE = DiscoveredFlow(
    id="no_workspace_flow",
    name="no_workspace_flow",
    file_path="/flows/no_workspace_flow.flow",
    source_dsl=(
        "flow no_workspace_flow {\n"
        "    budget = 10m\n"
        "    on_error = pause\n"
        "    context = handoff\n"
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
    ),
    status="valid",
    errors=[],
    ast_json={"name": "no_workspace_flow", "nodes": {}, "edges": []},
    params=[],
)

ERROR_FLOW = DiscoveredFlow(
    id="broken",
    name=None,
    file_path="/flows/broken.flow",
    source_dsl="invalid dsl",
    status="error",
    errors=["Parse error: unexpected input"],
    ast_json=None,
    params=[],
)

FLOW_DEF_ROW = FlowDefinitionRow(
    id="def-1",
    name="code_review",
    source_dsl="...",
    ast_json="{}",
    created_at="2025-01-01T00:00:00+00:00",
    updated_at="2025-01-01T00:00:00+00:00",
)


def _make_flow_run_row(
    run_id: str = "run-1",
    flow_def_id: str = "def-1",
    status: str = "running",
    started_at: str | None = "2025-01-01T00:00:00+00:00",
    elapsed: float = 42.5,
    budget: int = 600,
) -> FlowRunRow:
    return FlowRunRow(
        id=run_id,
        flow_definition_id=flow_def_id,
        status=status,
        default_workspace=".",
        data_dir="/data/run-1",
        params_json=None,
        budget_seconds=budget,
        elapsed_seconds=elapsed,
        on_error="pause",
        started_at=started_at,
        completed_at=None,
        created_at="2025-01-01T00:00:00+00:00",
        error_message=None,
    )


def _make_task_row(
    task_id: str = "task-1",
    run_id: str = "run-1",
    node_name: str = "start",
    status: str = "completed",
    generation: int = 1,
    started_at: str | None = "2025-01-01T00:00:10+00:00",
    elapsed: float | None = 10.5,
    exit_code: int | None = 0,
) -> TaskExecutionRow:
    return TaskExecutionRow(
        id=task_id,
        flow_run_id=run_id,
        node_name=node_name,
        node_type="entry",
        status=status,
        generation=generation,
        context_mode="none",
        cwd=".",
        task_dir="/data/run-1/start_1",
        prompt_text="go",
        started_at=started_at,
        completed_at=None,
        elapsed_seconds=elapsed,
        exit_code=exit_code,
        summary_path=None,
        error_message=None,
        created_at="2025-01-01T00:00:00+00:00",
    )


def _make_edge_row(
    edge_id: str = "edge-1",
    run_id: str = "run-1",
    from_task_id: str = "task-1",
    to_task_id: str | None = "task-2",
    edge_type: str = "unconditional",
) -> EdgeTransitionRow:
    return EdgeTransitionRow(
        id=edge_id,
        flow_run_id=run_id,
        from_task_id=from_task_id,
        to_task_id=to_task_id,
        edge_type=edge_type,
        condition_text=None,
        judge_session_id=None,
        judge_decision=None,
        judge_reasoning=None,
        judge_confidence=None,
        created_at="2025-01-01T00:01:00+00:00",
    )


def _make_test_client(
    flows: dict[str, DiscoveredFlow] | None = None,
    db_mock: MagicMock | None = None,
    run_manager: RunManager | None = None,
) -> TestClient:
    """Create a TestClient with mocked dependencies on app.state."""
    config = FlowstateConfig(watch_dir="/tmp/nonexistent-for-test")
    app = create_app(config=config)

    # Mock FlowRegistry
    mock_registry = MagicMock(spec=FlowRegistry)
    if flows is None:
        flows = {}
    mock_registry.list_flows.return_value = list(flows.values())
    mock_registry.get_flow.side_effect = lambda fid: flows.get(fid)
    mock_registry.get_flow_by_name.side_effect = lambda name: next(
        (f for f in flows.values() if f.name == name), None
    )
    app.state.flow_registry = mock_registry

    # Mock or real DB
    if db_mock is None:
        db_mock = MagicMock()
    app.state.db = db_mock

    # RunManager
    if run_manager is None:
        run_manager = RunManager()
    app.state.run_manager = run_manager

    # Mock WebSocket hub (routes access ws_hub.on_flow_event for executor creation)
    mock_ws_hub = MagicMock()
    mock_ws_hub.on_flow_event = MagicMock()
    app.state.ws_hub = mock_ws_hub

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /api/flows/:id/runs — Start a run
# ---------------------------------------------------------------------------


class TestStartRunReturns202:
    def test_start_run_returns_202(self) -> None:
        """Start a run on a valid flow with valid params returns 202."""
        mock_db = MagicMock()
        run_manager = RunManager()

        # Patch the executor and parse_flow to avoid real imports
        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes.FlowExecutor") as mock_executor_cls,
        ):
            mock_flow_ast = MagicMock()
            mock_flow_ast.default_workspace = "."
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value="run-123")
            mock_executor_cls.return_value = mock_executor

            client = _make_test_client(
                flows={"code_review": VALID_FLOW},
                db_mock=mock_db,
                run_manager=run_manager,
            )

            response = client.post(
                "/api/flows/code_review/runs",
                json={"params": {"focus": "auth"}},
            )

        assert response.status_code == 202
        body = response.json()
        assert "flow_run_id" in body
        assert isinstance(body["flow_run_id"], str)
        assert len(body["flow_run_id"]) > 0


class TestStartRunPassesHarnessManager:
    def test_start_run_passes_harness_mgr_to_executor(self) -> None:
        """FlowExecutor is constructed with harness_mgr from app.state."""
        mock_db = MagicMock()
        run_manager = RunManager()

        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes.FlowExecutor") as mock_executor_cls,
        ):
            mock_flow_ast = MagicMock()
            mock_flow_ast.default_workspace = "."
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value="run-123")
            mock_executor_cls.return_value = mock_executor

            client = _make_test_client(
                flows={"code_review": VALID_FLOW},
                db_mock=mock_db,
                run_manager=run_manager,
            )

            response = client.post(
                "/api/flows/code_review/runs",
                json={"params": {"focus": "auth"}},
            )

        assert response.status_code == 202
        # Verify FlowExecutor was called with harness_mgr keyword argument
        call_kwargs = mock_executor_cls.call_args[1]
        assert "harness_mgr" in call_kwargs
        # The harness_mgr should be a HarnessManager instance (created by create_app)
        from flowstate.engine.harness import HarnessManager

        assert isinstance(call_kwargs["harness_mgr"], HarnessManager)


class TestStartRunFlowNotFound:
    def test_start_run_flow_not_found(self) -> None:
        """POST /api/flows/nonexistent/runs returns 404."""
        client = _make_test_client()
        response = client.post(
            "/api/flows/nonexistent/runs",
            json={"params": {}},
        )
        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert "nonexistent" in body["error"]


class TestStartRunFlowHasErrors:
    def test_start_run_flow_has_errors(self) -> None:
        """POST /api/flows/broken/runs returns 400 when flow has parse errors."""
        client = _make_test_client(flows={"broken": ERROR_FLOW})
        response = client.post(
            "/api/flows/broken/runs",
            json={"params": {}},
        )
        assert response.status_code == 400
        body = response.json()
        assert "error" in body
        assert "errors" in body["error"].lower() or "error" in body["error"].lower()
        assert len(body["details"]) > 0


class TestStartRunMissingRequiredParam:
    def test_start_run_missing_required_param(self) -> None:
        """Flow declares param focus: string (no default). Missing param returns 400."""
        client = _make_test_client(flows={"needs_param": VALID_FLOW_WITH_REQUIRED_PARAM})
        response = client.post(
            "/api/flows/needs_param/runs",
            json={"params": {}},
        )
        assert response.status_code == 400
        body = response.json()
        assert "focus" in body["error"]


class TestStartRunUnknownParam:
    def test_start_run_unknown_param(self) -> None:
        """Sending an undeclared param returns 400."""
        client = _make_test_client(flows={"code_review": VALID_FLOW})
        response = client.post(
            "/api/flows/code_review/runs",
            json={"params": {"nonexistent": "value"}},
        )
        assert response.status_code == 400
        body = response.json()
        assert "nonexistent" in body["error"]


# ---------------------------------------------------------------------------
# GET /api/runs — List runs
# ---------------------------------------------------------------------------


class TestListRuns:
    def test_list_runs(self) -> None:
        """GET /api/runs returns all runs, sorted by started_at descending."""
        run1 = _make_flow_run_row("run-1", started_at="2025-01-01T00:00:00+00:00")
        run2 = _make_flow_run_row("run-2", started_at="2025-01-02T00:00:00+00:00")
        mock_db = MagicMock()
        mock_db.list_flow_runs.return_value = [run2, run1]  # newest first from DB
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["id"] == "run-2"
        assert body[1]["id"] == "run-1"
        # Check structure
        for run in body:
            assert "id" in run
            assert "flow_name" in run
            assert "status" in run
            assert "started_at" in run
            assert "elapsed_seconds" in run

    def test_list_runs_empty(self) -> None:
        """GET /api/runs with no runs returns empty list, not 404."""
        mock_db = MagicMock()
        mock_db.list_flow_runs.return_value = []
        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs")
        assert response.status_code == 200
        assert response.json() == []


class TestListRunsFilterByStatus:
    def test_list_runs_filter_by_status(self) -> None:
        """GET /api/runs?status=running returns only running runs."""
        running = _make_flow_run_row("run-1", status="running")
        mock_db = MagicMock()
        mock_db.list_flow_runs.return_value = [running]
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs?status=running")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["status"] == "running"
        # Verify the DB was called with the status filter
        mock_db.list_flow_runs.assert_called_once_with(status="running")


# ---------------------------------------------------------------------------
# GET /api/runs/:id — Run detail
# ---------------------------------------------------------------------------


class TestGetRunDetail:
    def test_get_run_detail(self) -> None:
        """GET /api/runs/:id returns full response with tasks and edges."""
        run = _make_flow_run_row("run-1")
        task1 = _make_task_row("task-1", "run-1", "start", "completed", exit_code=0)
        task2 = _make_task_row("task-2", "run-1", "done", "running", exit_code=None)
        edge1 = _make_edge_row("edge-1", "run-1", "task-1", "task-2")

        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = run
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW
        mock_db.list_task_executions.return_value = [task1, task2]
        mock_db.list_edge_transitions.return_value = [edge1]

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1")

        assert response.status_code == 200
        body = response.json()

        # Top-level fields
        assert body["id"] == "run-1"
        assert body["flow_name"] == "code_review"
        assert body["status"] == "running"
        assert body["started_at"] == "2025-01-01T00:00:00+00:00"
        assert body["elapsed_seconds"] == 42.5
        assert body["budget_seconds"] == 600

        # Tasks
        assert len(body["tasks"]) == 2
        t1 = body["tasks"][0]
        assert t1["id"] == "task-1"
        assert t1["node_name"] == "start"
        assert t1["status"] == "completed"
        assert t1["generation"] == 1
        assert t1["exit_code"] == 0

        t2 = body["tasks"][1]
        assert t2["id"] == "task-2"
        assert t2["node_name"] == "done"
        assert t2["exit_code"] is None

        # Edges
        assert len(body["edges"]) == 1
        e1 = body["edges"][0]
        assert e1["from_node"] == "start"
        assert e1["to_node"] == "done"
        assert e1["edge_type"] == "unconditional"
        assert e1["created_at"] == "2025-01-01T00:01:00+00:00"


class TestGetRunNotFound:
    def test_get_run_not_found(self) -> None:
        """GET /api/runs/nonexistent returns 404."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/nonexistent")

        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert "nonexistent" in body["error"]


# ---------------------------------------------------------------------------
# POST /api/runs/:id/pause — Pause a run
# ---------------------------------------------------------------------------


class TestPauseRun:
    def test_pause_run(self) -> None:
        """POST /api/runs/:id/pause delegates to executor.pause() and returns 200."""
        mock_executor = MagicMock()
        mock_executor.pause = AsyncMock()

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/pause")

        assert response.status_code == 200
        assert response.json() == {"status": "paused"}
        mock_executor.pause.assert_called_once()


class TestResumeRun:
    def test_resume_run(self) -> None:
        """POST /api/runs/:id/resume delegates to executor.resume() and returns 200."""
        mock_executor = MagicMock()
        mock_executor.resume = AsyncMock()

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/resume")

        assert response.status_code == 200
        assert response.json() == {"status": "running"}
        mock_executor.resume.assert_called_once()


class TestCancelRun:
    def test_cancel_run(self) -> None:
        """POST /api/runs/:id/cancel delegates to executor.cancel() and returns 200."""
        mock_executor = MagicMock()
        mock_executor.cancel = AsyncMock()

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/cancel")

        assert response.status_code == 200
        assert response.json() == {"status": "cancelled"}
        mock_executor.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/runs/:id/tasks/:tid/retry — Retry a task
# ---------------------------------------------------------------------------


class TestRetryTask:
    def test_retry_task(self) -> None:
        """POST /api/runs/:id/tasks/:tid/retry delegates to executor.retry_task()."""
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


class TestSkipTask:
    def test_skip_task(self) -> None:
        """POST /api/runs/:id/tasks/:tid/skip delegates to executor.skip_task()."""
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
# Control operations on completed/nonexistent runs — 409 / 404
# ---------------------------------------------------------------------------


class TestPauseCompletedRunReturns409:
    def test_pause_completed_run_returns_409(self) -> None:
        """Pause on a completed run (no active executor) returns 409."""
        completed_run = _make_flow_run_row("run-1", status="completed")
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = completed_run

        # Empty run manager -- no active executor
        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        response = client.post("/api/runs/run-1/pause")
        assert response.status_code == 409
        body = response.json()
        assert "not active" in body["error"]

    def test_pause_nonexistent_run_returns_404(self) -> None:
        """Pause on a run that does not exist returns 404."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = None

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        response = client.post("/api/runs/run-1/pause")
        assert response.status_code == 404


class TestExecutorRaisesInvalidStateError:
    def test_pause_raises_invalid_state_error(self) -> None:
        """When executor.pause() raises InvalidStateError, route returns 409."""
        mock_executor = MagicMock()
        mock_executor.pause = AsyncMock(side_effect=InvalidStateError("Cannot pause"))

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/pause")

        assert response.status_code == 409
        body = response.json()
        assert "Cannot pause" in body["error"]

    def test_resume_raises_invalid_state_error(self) -> None:
        """When executor.resume() raises InvalidStateError, route returns 409."""
        mock_executor = MagicMock()
        mock_executor.resume = AsyncMock(side_effect=InvalidStateError("Not paused"))

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/resume")

        assert response.status_code == 409
        body = response.json()
        assert "Not paused" in body["error"]

    def test_cancel_raises_invalid_state_error(self) -> None:
        """When executor.cancel() raises InvalidStateError, route returns 409."""
        mock_executor = MagicMock()
        mock_executor.cancel = AsyncMock(side_effect=InvalidStateError("Already cancelled"))

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/cancel")

        assert response.status_code == 409

    def test_retry_raises_invalid_state_error(self) -> None:
        """When executor.retry_task() raises InvalidStateError, route returns 409."""
        mock_executor = MagicMock()
        mock_executor.retry_task = AsyncMock(side_effect=InvalidStateError("Task not failed"))

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/retry")

        assert response.status_code == 409

    def test_skip_raises_invalid_state_error(self) -> None:
        """When executor.skip_task() raises InvalidStateError, route returns 409."""
        mock_executor = MagicMock()
        mock_executor.skip_task = AsyncMock(side_effect=InvalidStateError("Task not failed"))

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(run_manager=run_manager)
        response = client.post("/api/runs/run-1/tasks/task-1/skip")

        assert response.status_code == 409


# ---------------------------------------------------------------------------
# All route handlers are async def
# ---------------------------------------------------------------------------


class TestAllRoutesAsync:
    def test_all_run_management_routes_are_async(self) -> None:
        """Verify that all route handler functions are async (coroutine functions)."""
        import asyncio

        from flowstate.server import routes

        route_handlers = [
            routes.start_run,
            routes.list_runs,
            routes.get_run,
            routes.pause_run,
            routes.resume_run,
            routes.cancel_run,
            routes.retry_task,
            routes.skip_task,
        ]
        for handler in route_handlers:
            assert asyncio.iscoroutinefunction(
                handler
            ), f"{handler.__name__} is not an async function"


# ---------------------------------------------------------------------------
# Auto-generated workspace when workspace is omitted (ENGINE-026)
# ---------------------------------------------------------------------------


class TestAutoWorkspaceWhenOmitted:
    def test_start_run_without_workspace_generates_isolated_path(self) -> None:
        """When a flow omits workspace, the route auto-generates a path under
        ~/.flowstate/workspaces/<flow-name>/<run-id[:8]>/."""
        mock_db = MagicMock()
        run_manager = RunManager()

        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes.FlowExecutor") as mock_executor_cls,
        ):
            mock_flow_ast = MagicMock()
            mock_flow_ast.workspace = None  # No workspace declared
            mock_flow_ast.name = "no_workspace_flow"
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value="run-123")
            mock_executor_cls.return_value = mock_executor

            client = _make_test_client(
                flows={"no_workspace_flow": VALID_FLOW_NO_WORKSPACE},
                db_mock=mock_db,
                run_manager=run_manager,
            )

            response = client.post(
                "/api/flows/no_workspace_flow/runs",
                json={"params": {}},
            )

        assert response.status_code == 202
        body = response.json()
        run_id = body["flow_run_id"]

        # Verify executor.execute was called with the auto-generated workspace
        call_args = mock_executor.execute.call_args
        workspace_arg = call_args[0][2] if len(call_args[0]) > 2 else call_args[1]["workspace"]
        import os

        expected_prefix = os.path.expanduser("~/.flowstate/workspaces/no_workspace_flow/")
        assert workspace_arg.startswith(
            expected_prefix
        ), f"Expected workspace to start with {expected_prefix}, got {workspace_arg}"
        # The suffix should be the first 8 chars of the run_id
        assert workspace_arg.endswith(run_id[:8])

    def test_start_run_with_explicit_workspace_uses_declared_path(self) -> None:
        """When a flow declares workspace, the route uses the declared path."""
        mock_db = MagicMock()
        run_manager = RunManager()

        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes.FlowExecutor") as mock_executor_cls,
        ):
            mock_flow_ast = MagicMock()
            mock_flow_ast.workspace = "/my/explicit/workspace"
            mock_flow_ast.name = "code_review"
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value="run-123")
            mock_executor_cls.return_value = mock_executor

            client = _make_test_client(
                flows={"code_review": VALID_FLOW},
                db_mock=mock_db,
                run_manager=run_manager,
            )

            response = client.post(
                "/api/flows/code_review/runs",
                json={"params": {"focus": "auth"}},
            )

        assert response.status_code == 202

        # Verify executor.execute was called with the explicit workspace
        call_args = mock_executor.execute.call_args
        workspace_arg = call_args[0][2] if len(call_args[0]) > 2 else call_args[1]["workspace"]
        assert workspace_arg == "/my/explicit/workspace"

    def test_concurrent_runs_get_different_workspaces(self) -> None:
        """Two runs of the same flow without workspace get different auto-generated paths."""
        mock_db = MagicMock()
        run_manager = RunManager()

        workspaces: list[str] = []

        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes.FlowExecutor") as mock_executor_cls,
        ):
            mock_flow_ast = MagicMock()
            mock_flow_ast.workspace = None
            mock_flow_ast.name = "no_workspace_flow"
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value="run-123")
            mock_executor_cls.return_value = mock_executor

            client = _make_test_client(
                flows={"no_workspace_flow": VALID_FLOW_NO_WORKSPACE},
                db_mock=mock_db,
                run_manager=run_manager,
            )

            for _ in range(2):
                response = client.post(
                    "/api/flows/no_workspace_flow/runs",
                    json={"params": {}},
                )
                assert response.status_code == 202
                call_args = mock_executor.execute.call_args
                ws = call_args[0][2] if len(call_args[0]) > 2 else call_args[1]["workspace"]
                workspaces.append(ws)

        assert len(workspaces) == 2
        assert workspaces[0] != workspaces[1], "Concurrent runs should get different workspaces"
