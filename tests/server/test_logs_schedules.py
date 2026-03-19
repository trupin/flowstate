"""Tests for task logs and schedule management REST API endpoints (SERVER-004).

All tests mock the DB layer. Uses FastAPI TestClient with mocked dependencies.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app
from flowstate.server.flow_registry import FlowRegistry
from flowstate.server.run_manager import RunManager
from flowstate.state.models import (
    FlowDefinitionRow,
    FlowRunRow,
    FlowScheduleRow,
    TaskExecutionRow,
    TaskLogRow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FLOW_DEF_ROW = FlowDefinitionRow(
    id="def-1",
    name="code_review",
    source_dsl=(
        "flow code_review {\n"
        "    budget = 10m\n"
        "    on_error = pause\n"
        "    context = handoff\n"
        '    workspace = "."\n'
        '    entry start { prompt = "go" }\n'
        '    exit done { prompt = "done" }\n'
        "    start -> done\n"
        "}\n"
    ),
    ast_json="{}",
    created_at="2025-01-01T00:00:00+00:00",
    updated_at="2025-01-01T00:00:00+00:00",
)


def _make_flow_run_row(
    run_id: str = "run-1",
    flow_def_id: str = "def-1",
    status: str = "running",
) -> FlowRunRow:
    return FlowRunRow(
        id=run_id,
        flow_definition_id=flow_def_id,
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


def _make_task_row(
    task_id: str = "task-1",
    run_id: str = "run-1",
) -> TaskExecutionRow:
    return TaskExecutionRow(
        id=task_id,
        flow_run_id=run_id,
        node_name="start",
        node_type="entry",
        status="running",
        generation=1,
        context_mode="none",
        cwd=".",
        task_dir="/data/run-1/start_1",
        prompt_text="go",
        created_at="2025-01-01T00:00:00+00:00",
    )


def _make_log_row(
    log_id: int = 1,
    task_id: str = "task-1",
    timestamp: str = "2025-01-01T00:00:01+00:00",
    log_type: str = "assistant_message",
    content: str = "Hello world",
) -> TaskLogRow:
    return TaskLogRow(
        id=log_id,
        task_execution_id=task_id,
        timestamp=timestamp,
        log_type=log_type,
        content=content,
    )


def _make_schedule_row(
    schedule_id: str = "sched-1",
    flow_def_id: str = "def-1",
    cron: str = "0 9 * * 1",
    on_overlap: str = "skip",
    enabled: int = 1,
    last_triggered: str | None = None,
    next_trigger: str | None = "2025-01-06T09:00:00+00:00",
) -> FlowScheduleRow:
    return FlowScheduleRow(
        id=schedule_id,
        flow_definition_id=flow_def_id,
        cron_expression=cron,
        on_overlap=on_overlap,
        enabled=enabled,
        last_triggered_at=last_triggered,
        next_trigger_at=next_trigger,
        created_at="2025-01-01T00:00:00+00:00",
    )


def _make_test_client(
    db_mock: MagicMock | None = None,
    run_manager: RunManager | None = None,
) -> TestClient:
    """Create a TestClient with mocked dependencies."""
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

    # Mock WebSocket hub (routes access ws_hub.on_flow_event for executor creation)
    mock_ws_hub = MagicMock()
    mock_ws_hub.on_flow_event = MagicMock()
    app.state.ws_hub = mock_ws_hub

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /api/runs/:id/tasks/:tid/logs — Task logs
# ---------------------------------------------------------------------------


class TestGetTaskLogs:
    def test_get_task_logs(self) -> None:
        """Mock DB to return 5 log entries. Verify 200 with 5 entries, has_more=false."""
        logs = [
            _make_log_row(
                i, "task-1", f"2025-01-01T00:00:0{i}+00:00", "assistant_message", f"Log {i}"
            )
            for i in range(1, 6)
        ]
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row()
        mock_db.get_task_execution.return_value = _make_task_row()
        mock_db.get_task_logs.return_value = logs  # Only 5, less than limit+1

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/tasks/task-1/logs")

        assert response.status_code == 200
        body = response.json()
        assert body["task_execution_id"] == "task-1"
        assert len(body["logs"]) == 5
        assert body["has_more"] is False
        # Check structure
        for log in body["logs"]:
            assert "timestamp" in log
            assert "log_type" in log
            assert "content" in log


class TestGetTaskLogsPagination:
    def test_get_task_logs_pagination(self) -> None:
        """Mock DB to return 1001 entries (limit+1). Verify 1000 entries, has_more=true."""
        logs = [
            _make_log_row(i, "task-1", "2025-01-01T00:00:00+00:00", "stdout", f"Line {i}")
            for i in range(1, 1002)  # 1001 entries
        ]
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row()
        mock_db.get_task_execution.return_value = _make_task_row()
        mock_db.get_task_logs.return_value = logs

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/tasks/task-1/logs")

        assert response.status_code == 200
        body = response.json()
        assert len(body["logs"]) == 1000
        assert body["has_more"] is True


class TestGetTaskLogsWithAfter:
    def test_get_task_logs_with_after(self) -> None:
        """Send ?after=2024-01-01T00:00:00Z. Verify DB receives the after parameter."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row()
        mock_db.get_task_execution.return_value = _make_task_row()
        mock_db.get_task_logs.return_value = []

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/tasks/task-1/logs?after=2024-01-01T00:00:00Z")

        assert response.status_code == 200
        mock_db.get_task_logs.assert_called_once_with(
            task_execution_id="task-1",
            after_timestamp="2024-01-01T00:00:00Z",
            limit=1001,  # limit + 1 for has_more detection
        )


class TestGetTaskLogsCustomLimit:
    def test_get_task_logs_custom_limit(self) -> None:
        """Send ?limit=50. Verify only 50 entries returned."""
        logs = [
            _make_log_row(i, "task-1", "2025-01-01T00:00:00+00:00", "stdout", f"Line {i}")
            for i in range(1, 51)  # 50 entries
        ]
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row()
        mock_db.get_task_execution.return_value = _make_task_row()
        mock_db.get_task_logs.return_value = logs

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/tasks/task-1/logs?limit=50")

        assert response.status_code == 200
        body = response.json()
        assert len(body["logs"]) == 50
        assert body["has_more"] is False
        # Verify DB was called with limit+1
        mock_db.get_task_logs.assert_called_once_with(
            task_execution_id="task-1",
            after_timestamp=None,
            limit=51,
        )


class TestGetTaskLogsLimitClamped:
    def test_get_task_logs_limit_clamped(self) -> None:
        """Send ?limit=99999. Verify the effective limit is 10000."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row()
        mock_db.get_task_execution.return_value = _make_task_row()
        mock_db.get_task_logs.return_value = []

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/tasks/task-1/logs?limit=99999")

        assert response.status_code == 200
        # Verify DB was called with clamped limit (10000 + 1)
        mock_db.get_task_logs.assert_called_once_with(
            task_execution_id="task-1",
            after_timestamp=None,
            limit=10001,
        )


class TestGetTaskLogsRunNotFound:
    def test_get_task_logs_run_not_found(self) -> None:
        """Verify 404 when run doesn't exist."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/nonexistent/tasks/task-1/logs")

        assert response.status_code == 404
        body = response.json()
        assert "nonexistent" in body["error"]


class TestGetTaskLogsTaskNotFound:
    def test_get_task_logs_task_not_found(self) -> None:
        """Verify 404 when task doesn't exist."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row()
        mock_db.get_task_execution.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/tasks/nonexistent/logs")

        assert response.status_code == 404
        body = response.json()
        assert "nonexistent" in body["error"]

    def test_get_task_logs_task_belongs_to_different_run(self) -> None:
        """Verify 404 when task exists but belongs to a different run."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row("run-1")
        # Task belongs to run-2, not run-1
        mock_db.get_task_execution.return_value = _make_task_row("task-1", "run-2")

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/tasks/task-1/logs")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/schedules — List schedules
# ---------------------------------------------------------------------------


class TestListSchedules:
    def test_list_schedules(self) -> None:
        """Mock DB with 2 schedules. Verify 200 with correct structure."""
        sched1 = _make_schedule_row("sched-1", "def-1", "0 9 * * 1", "skip", 1)
        sched2 = _make_schedule_row("sched-2", "def-1", "0 0 * * *", "parallel", 0)

        mock_db = MagicMock()
        mock_db.list_flow_schedules.return_value = [sched1, sched2]
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/schedules")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2

        s1 = body[0]
        assert s1["id"] == "sched-1"
        assert s1["flow_name"] == "code_review"
        assert s1["cron_expression"] == "0 9 * * 1"
        assert s1["status"] == "active"
        assert s1["overlap_policy"] == "skip"
        assert s1["next_run_at"] is not None

        s2 = body[1]
        assert s2["id"] == "sched-2"
        assert s2["status"] == "paused"
        assert s2["overlap_policy"] == "parallel"


class TestListSchedulesEmpty:
    def test_list_schedules_empty(self) -> None:
        """No schedules. Verify 200 with empty list."""
        mock_db = MagicMock()
        mock_db.list_flow_schedules.return_value = []

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/schedules")

        assert response.status_code == 200
        assert response.json() == []


# ---------------------------------------------------------------------------
# POST /api/schedules/:id/pause — Pause schedule
# ---------------------------------------------------------------------------


class TestPauseSchedule:
    def test_pause_schedule(self) -> None:
        """Active schedule. Verify 200 and DB update called."""
        mock_db = MagicMock()
        mock_db.get_flow_schedule.return_value = _make_schedule_row(enabled=1)

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/schedules/sched-1/pause")

        assert response.status_code == 200
        assert response.json() == {"status": "paused"}
        mock_db.update_flow_schedule.assert_called_once_with("sched-1", enabled=0)


class TestPauseAlreadyPaused:
    def test_pause_already_paused(self) -> None:
        """Verify 409 when schedule is already paused."""
        mock_db = MagicMock()
        mock_db.get_flow_schedule.return_value = _make_schedule_row(enabled=0)

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/schedules/sched-1/pause")

        assert response.status_code == 409


class TestPauseScheduleNotFound:
    def test_pause_schedule_not_found(self) -> None:
        """Verify 404 when schedule not found."""
        mock_db = MagicMock()
        mock_db.get_flow_schedule.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/schedules/nonexistent/pause")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/schedules/:id/resume — Resume schedule
# ---------------------------------------------------------------------------


class TestResumeSchedule:
    def test_resume_schedule(self) -> None:
        """Paused schedule. Verify 200 and DB update called."""
        mock_db = MagicMock()
        mock_db.get_flow_schedule.return_value = _make_schedule_row(enabled=0)

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/schedules/sched-1/resume")

        assert response.status_code == 200
        assert response.json() == {"status": "active"}
        mock_db.update_flow_schedule.assert_called_once_with("sched-1", enabled=1)


class TestResumeAlreadyActive:
    def test_resume_already_active(self) -> None:
        """Verify 409 when schedule is already active."""
        mock_db = MagicMock()
        mock_db.get_flow_schedule.return_value = _make_schedule_row(enabled=1)

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/schedules/sched-1/resume")

        assert response.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/schedules/:id/trigger — Trigger schedule
# ---------------------------------------------------------------------------


class TestTriggerSchedule:
    def test_trigger_schedule(self) -> None:
        """Valid schedule and flow. Verify 202 with flow_run_id."""
        mock_db = MagicMock()
        mock_db.get_flow_schedule.return_value = _make_schedule_row(on_overlap="parallel")
        mock_db.get_flow_definition.return_value = FLOW_DEF_ROW

        run_manager = RunManager()

        with (
            patch("flowstate.server.routes.parse_flow") as mock_parse,
            patch("flowstate.server.routes.FlowExecutor") as mock_executor_cls,
        ):
            mock_flow_ast = MagicMock()
            mock_flow_ast.workspace = "."
            mock_parse.return_value = mock_flow_ast

            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value="run-123")
            mock_executor_cls.return_value = mock_executor

            client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
            response = client.post("/api/schedules/sched-1/trigger")

        assert response.status_code == 202
        body = response.json()
        assert "flow_run_id" in body
        assert isinstance(body["flow_run_id"], str)
        assert len(body["flow_run_id"]) > 0


class TestTriggerScheduleSkipOverlap:
    def test_trigger_schedule_skip_overlap(self) -> None:
        """Schedule has overlap_policy=skip and a run is active. Verify 409."""
        active_run = _make_flow_run_row("run-1", "def-1", "running")
        mock_db = MagicMock()
        mock_db.get_flow_schedule.return_value = _make_schedule_row(on_overlap="skip")
        mock_db.list_flow_runs.return_value = [active_run]

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/schedules/sched-1/trigger")

        assert response.status_code == 409
        body = response.json()
        assert "overlap" in body["error"].lower()


class TestTriggerScheduleNotFound:
    def test_trigger_schedule_not_found(self) -> None:
        """Verify 404 when schedule not found."""
        mock_db = MagicMock()
        mock_db.get_flow_schedule.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/schedules/nonexistent/trigger")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# All route handlers are async def
# ---------------------------------------------------------------------------


class TestAllLogsScheduleRoutesAsync:
    def test_all_routes_are_async(self) -> None:
        """Verify that all new route handler functions are async."""
        import asyncio

        from flowstate.server import routes

        route_handlers = [
            routes.get_task_logs,
            routes.list_schedules,
            routes.pause_schedule,
            routes.resume_schedule,
            routes.trigger_schedule,
        ]
        for handler in route_handlers:
            assert asyncio.iscoroutinefunction(
                handler
            ), f"{handler.__name__} is not an async function"
