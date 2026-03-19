"""Tests for orchestrator session REST API endpoints.

Tests for:
  - GET /api/runs/:run_id/orchestrators
  - GET /api/runs/:run_id/orchestrators/:session_id/logs

All tests mock the DB layer and filesystem. Uses FastAPI TestClient.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app
from flowstate.server.flow_registry import FlowRegistry
from flowstate.server.run_manager import RunManager
from flowstate.state.models import FlowRunRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_flow_run_row(
    run_id: str = "run-1",
    flow_def_id: str = "def-1",
    status: str = "running",
    data_dir: str = "/data/run-1",
) -> FlowRunRow:
    return FlowRunRow(
        id=run_id,
        flow_definition_id=flow_def_id,
        status=status,
        default_workspace=".",
        data_dir=data_dir,
        params_json=None,
        budget_seconds=600,
        elapsed_seconds=42.5,
        on_error="pause",
        started_at="2025-01-01T00:00:00+00:00",
        completed_at=None,
        created_at="2025-01-01T00:00:00+00:00",
        error_message=None,
    )


def _make_test_client(
    db_mock: MagicMock | None = None,
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

    run_manager = RunManager()
    app.state.run_manager = run_manager

    mock_ws_hub = MagicMock()
    mock_ws_hub.on_flow_event = MagicMock()
    app.state.ws_hub = mock_ws_hub

    return TestClient(app, raise_server_exceptions=False)


def _create_orchestrator_dir(base: Path, key: str, session_id: str, prompt: str = "") -> None:
    """Create an orchestrator session directory with session_id and system_prompt.md."""
    orch_dir = base / "orchestrator" / key
    orch_dir.mkdir(parents=True, exist_ok=True)
    (orch_dir / "session_id").write_text(session_id)
    if prompt:
        (orch_dir / "system_prompt.md").write_text(prompt)


# ---------------------------------------------------------------------------
# GET /api/runs/:run_id/orchestrators
# ---------------------------------------------------------------------------


class TestListOrchestrators:
    def test_list_orchestrators_returns_sessions(self) -> None:
        """Orchestrator directory with two sessions returns both."""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            _create_orchestrator_dir(
                data_dir, "claude-abc123", "sess-uuid-1", "# System Prompt\nHello"
            )
            _create_orchestrator_dir(data_dir, "claude-def456", "sess-uuid-2", "# Another Prompt")

            mock_db = MagicMock()
            mock_db.get_flow_run.return_value = _make_flow_run_row(data_dir=str(data_dir))

            client = _make_test_client(db_mock=mock_db)
            response = client.get("/api/runs/run-1/orchestrators")

            assert response.status_code == 200
            body = response.json()
            assert len(body) == 2

            # Sessions sorted by directory name
            s1 = body[0]
            assert s1["key"] == "claude-abc123"
            assert s1["session_id"] == "sess-uuid-1"
            assert s1["system_prompt"] == "# System Prompt\nHello"
            assert s1["data_dir"] == str(data_dir / "orchestrator" / "claude-abc123")

            s2 = body[1]
            assert s2["key"] == "claude-def456"
            assert s2["session_id"] == "sess-uuid-2"
            assert s2["system_prompt"] == "# Another Prompt"


class TestListOrchestratorsEmpty:
    def test_no_orchestrator_dir(self) -> None:
        """Run with no orchestrator/ directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_db = MagicMock()
            mock_db.get_flow_run.return_value = _make_flow_run_row(data_dir=tmp)

            client = _make_test_client(db_mock=mock_db)
            response = client.get("/api/runs/run-1/orchestrators")

            assert response.status_code == 200
            assert response.json() == []

    def test_empty_orchestrator_dir(self) -> None:
        """Run with empty orchestrator/ directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "orchestrator").mkdir()

            mock_db = MagicMock()
            mock_db.get_flow_run.return_value = _make_flow_run_row(data_dir=tmp)

            client = _make_test_client(db_mock=mock_db)
            response = client.get("/api/runs/run-1/orchestrators")

            assert response.status_code == 200
            assert response.json() == []


class TestListOrchestratorsRunNotFound:
    def test_run_not_found_returns_404(self) -> None:
        """Unknown run_id returns 404."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/nonexistent/orchestrators")

        assert response.status_code == 404
        body = response.json()
        assert "nonexistent" in body["error"]


class TestListOrchestratorsMissingFiles:
    def test_skip_dir_without_session_id(self) -> None:
        """Subdirectory without session_id file is skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            # Create dir without session_id file
            (data_dir / "orchestrator" / "bad-session").mkdir(parents=True)
            # Create valid session
            _create_orchestrator_dir(data_dir, "good-session", "sess-1", "prompt")

            mock_db = MagicMock()
            mock_db.get_flow_run.return_value = _make_flow_run_row(data_dir=str(data_dir))

            client = _make_test_client(db_mock=mock_db)
            response = client.get("/api/runs/run-1/orchestrators")

            assert response.status_code == 200
            body = response.json()
            assert len(body) == 1
            assert body[0]["key"] == "good-session"

    def test_missing_system_prompt_returns_empty_string(self) -> None:
        """Session without system_prompt.md still returns with empty prompt."""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            orch_dir = data_dir / "orchestrator" / "no-prompt"
            orch_dir.mkdir(parents=True)
            (orch_dir / "session_id").write_text("sess-1")
            # No system_prompt.md

            mock_db = MagicMock()
            mock_db.get_flow_run.return_value = _make_flow_run_row(data_dir=str(data_dir))

            client = _make_test_client(db_mock=mock_db)
            response = client.get("/api/runs/run-1/orchestrators")

            assert response.status_code == 200
            body = response.json()
            assert len(body) == 1
            assert body[0]["system_prompt"] == ""

    def test_empty_session_id_file_skipped(self) -> None:
        """Session with empty session_id file is skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            orch_dir = data_dir / "orchestrator" / "empty-id"
            orch_dir.mkdir(parents=True)
            (orch_dir / "session_id").write_text("")

            mock_db = MagicMock()
            mock_db.get_flow_run.return_value = _make_flow_run_row(data_dir=str(data_dir))

            client = _make_test_client(db_mock=mock_db)
            response = client.get("/api/runs/run-1/orchestrators")

            assert response.status_code == 200
            assert response.json() == []

    def test_files_in_orchestrator_dir_ignored(self) -> None:
        """Non-directory entries in orchestrator/ are ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            orch_dir = data_dir / "orchestrator"
            orch_dir.mkdir(parents=True)
            # Create a file (not a directory) in orchestrator/
            (orch_dir / "some-file.txt").write_text("not a directory")
            # Create valid session
            _create_orchestrator_dir(data_dir, "valid", "sess-1", "prompt")

            mock_db = MagicMock()
            mock_db.get_flow_run.return_value = _make_flow_run_row(data_dir=str(data_dir))

            client = _make_test_client(db_mock=mock_db)
            response = client.get("/api/runs/run-1/orchestrators")

            assert response.status_code == 200
            body = response.json()
            assert len(body) == 1
            assert body[0]["key"] == "valid"


# ---------------------------------------------------------------------------
# GET /api/runs/:run_id/orchestrators/:session_id/logs
# ---------------------------------------------------------------------------


class TestGetOrchestratorLogs:
    def test_get_logs_returns_entries(self) -> None:
        """Query logs by session_id returns matching log entries."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row()
        # Simulate raw SQL query returning rows as tuples
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (
                1,
                "task-exec-1",
                "assistant_message",
                "Hello from orchestrator",
                "2025-01-01T00:00:01",
            ),
            (2, "task-exec-1", "tool_use", "Running command...", "2025-01-01T00:00:02"),
        ]
        mock_db.connection.execute.return_value = mock_cursor

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/orchestrators/sess-uuid-1/logs")

        assert response.status_code == 200
        body = response.json()
        assert len(body["logs"]) == 2

        log1 = body["logs"][0]
        assert log1["id"] == 1
        assert log1["task_execution_id"] == "task-exec-1"
        assert log1["log_type"] == "assistant_message"
        assert log1["content"] == "Hello from orchestrator"
        assert log1["timestamp"] == "2025-01-01T00:00:01"

        log2 = body["logs"][1]
        assert log2["id"] == 2
        assert log2["log_type"] == "tool_use"

    def test_get_logs_passes_correct_params_to_query(self) -> None:
        """Verify SQL query receives correct run_id and session_id."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(run_id="run-42")
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_db.connection.execute.return_value = mock_cursor

        client = _make_test_client(db_mock=mock_db)
        client.get("/api/runs/run-42/orchestrators/my-session-id/logs")

        # Check the SQL was called with correct params
        call_args = mock_db.connection.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "te.flow_run_id = ?" in sql
        assert "te.claude_session_id = ?" in sql
        assert params == ("run-42", "my-session-id")


class TestGetOrchestratorLogsEmpty:
    def test_no_logs_returns_empty(self) -> None:
        """No matching logs returns empty list."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_db.connection.execute.return_value = mock_cursor

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/run-1/orchestrators/nonexistent-session/logs")

        assert response.status_code == 200
        body = response.json()
        assert body["logs"] == []


class TestGetOrchestratorLogsRunNotFound:
    def test_run_not_found_returns_404(self) -> None:
        """Unknown run_id returns 404."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/runs/nonexistent/orchestrators/sess-1/logs")

        assert response.status_code == 404
        body = response.json()
        assert "nonexistent" in body["error"]


# ---------------------------------------------------------------------------
# All route handlers are async def
# ---------------------------------------------------------------------------


class TestOrchestratorRoutesAsync:
    def test_all_orchestrator_routes_are_async(self) -> None:
        """Verify that orchestrator route handler functions are async."""
        import asyncio

        from flowstate.server import routes

        route_handlers = [
            routes.list_orchestrators,
            routes.get_orchestrator_logs,
        ]
        for handler in route_handlers:
            assert asyncio.iscoroutinefunction(
                handler
            ), f"{handler.__name__} is not an async function"
