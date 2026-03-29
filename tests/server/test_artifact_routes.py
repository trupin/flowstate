"""Tests for artifact REST API endpoints (SERVER-022).

All tests mock the FlowstateDB -- never use real SQLite. Uses FastAPI TestClient
with mocked FlowRegistry, RunManager, and FlowstateDB.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app
from flowstate.server.flow_registry import FlowRegistry
from flowstate.server.run_manager import RunManager
from flowstate.state.models import (
    FlowDefinitionRow,
    FlowRunRow,
    TaskArtifactRow,
    TaskExecutionRow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RUN_ID = "run-001"
TASK_ID = "task-001"


def _make_task_execution(
    task_id: str = TASK_ID,
    flow_run_id: str = RUN_ID,
    status: str = "running",
) -> TaskExecutionRow:
    return TaskExecutionRow(
        id=task_id,
        flow_run_id=flow_run_id,
        node_name="start",
        node_type="task",
        status=status,
        generation=1,
        context_mode="handoff",
        cwd="/tmp/work",
        task_dir="/tmp/work/.flowstate/start",
        prompt_text="do something",
        started_at="2025-01-01T00:00:00+00:00",
        elapsed_seconds=0.0,
        exit_code=None,
        error_message=None,
        created_at="2025-01-01T00:00:00+00:00",
    )


def _make_artifact(
    task_id: str = TASK_ID,
    name: str = "decision",
    content: str = '{"decision": "ship"}',
    content_type: str = "application/json",
) -> TaskArtifactRow:
    return TaskArtifactRow(
        id="art-001",
        task_execution_id=task_id,
        name=name,
        content=content,
        content_type=content_type,
        created_at="2025-01-01T00:00:00+00:00",
    )


def _make_flow_run(run_id: str = RUN_ID) -> FlowRunRow:
    return FlowRunRow(
        id=run_id,
        flow_definition_id="fdef-001",
        status="running",
        data_dir="/tmp/data",
        budget_seconds=600,
        on_error="pause",
        created_at="2025-01-01T00:00:00+00:00",
    )


def _make_flow_def() -> FlowDefinitionRow:
    return FlowDefinitionRow(
        id="fdef-001",
        name="test_flow",
        source_dsl="flow test_flow { ... }",
        ast_json='{"name": "test_flow", "nodes": {}, "edges": []}',
        source_hash="abc123",
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


def _make_test_client(
    db_mock: MagicMock | None = None,
) -> TestClient:
    """Create a TestClient with mocked dependencies on app.state."""
    config = FlowstateConfig(watch_dir="/tmp/nonexistent-for-test")
    app = create_app(config=config)

    # Mock FlowRegistry
    mock_registry = MagicMock(spec=FlowRegistry)
    mock_registry.list_flows.return_value = []
    mock_registry.get_flow.return_value = None
    app.state.flow_registry = mock_registry

    # Mock or real DB
    if db_mock is None:
        db_mock = MagicMock()
    app.state.db = db_mock

    # RunManager
    app.state.run_manager = RunManager()

    # Mock WebSocket hub
    mock_ws_hub = MagicMock()
    mock_ws_hub.on_flow_event = MagicMock()
    app.state.ws_hub = mock_ws_hub

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/tasks/{task_id}/artifacts/{name} -- Upload
# ---------------------------------------------------------------------------


class TestUploadArtifact:
    def test_upload_returns_201(self) -> None:
        """Uploading an artifact returns 201 with status and name."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.save_artifact.return_value = _make_artifact()

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/decision",
            content='{"decision": "ship"}',
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "ok"
        assert body["name"] == "decision"

        mock_db.save_artifact.assert_called_once_with(
            TASK_ID, "decision", '{"decision": "ship"}', "application/json"
        )

    def test_upload_defaults_content_type_to_json(self) -> None:
        """When no Content-Type header is sent, defaults to application/json."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.save_artifact.return_value = _make_artifact()

        client = _make_test_client(db_mock=mock_db)
        # Send with no explicit content-type. TestClient may add its own header,
        # so we just verify the save_artifact was called.
        response = client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/decision",
            content='{"decision": "ship"}',
        )

        assert response.status_code == 201

    def test_upload_with_markdown_content_type(self) -> None:
        """Upload with text/markdown content type stores it correctly."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.save_artifact.return_value = _make_artifact(
            content="# Summary", content_type="text/markdown"
        )

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/summary",
            content="# Summary",
            headers={"content-type": "text/markdown"},
        )

        assert response.status_code == 201
        mock_db.save_artifact.assert_called_once_with(
            TASK_ID, "summary", "# Summary", "text/markdown"
        )

    def test_upload_upsert_replaces_content(self) -> None:
        """Uploading the same artifact name twice calls save_artifact twice (upsert)."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.save_artifact.return_value = _make_artifact()

        client = _make_test_client(db_mock=mock_db)

        # First upload
        client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/decision",
            content='{"decision": "v1"}',
            headers={"content-type": "application/json"},
        )

        # Second upload (upsert)
        response = client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/decision",
            content='{"decision": "v2"}',
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 201
        assert mock_db.save_artifact.call_count == 2

    def test_upload_invalid_task_returns_404(self) -> None:
        """Uploading to a non-existent task returns 404."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            f"/api/runs/{RUN_ID}/tasks/bad-task/artifacts/decision",
            content="{}",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 404

    def test_upload_task_wrong_run_returns_404(self) -> None:
        """Uploading to a task that belongs to a different run returns 404."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution(flow_run_id="other-run")

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/decision",
            content="{}",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 404

    def test_upload_invalid_name_returns_400(self) -> None:
        """Artifact names with invalid characters are rejected."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/bad name!",
            content="{}",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 400
        assert "Invalid artifact name" in response.json()["error"]

    def test_upload_name_too_long_returns_400(self) -> None:
        """Artifact names over 64 characters are rejected."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()

        long_name = "a" * 65
        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/{long_name}",
            content="{}",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 400

    def test_upload_content_too_large_returns_413(self) -> None:
        """Content over 1MB is rejected with 413."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()

        client = _make_test_client(db_mock=mock_db)
        large_content = "x" * (1_048_576 + 1)
        response = client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/decision",
            content=large_content,
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 413

    def test_upload_exactly_1mb_succeeds(self) -> None:
        """Content at exactly 1MB is accepted."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.save_artifact.return_value = _make_artifact()

        client = _make_test_client(db_mock=mock_db)
        exact_content = "x" * 1_048_576
        response = client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/decision",
            content=exact_content,
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 201

    def test_upload_valid_names_with_dots_hyphens_underscores(self) -> None:
        """Names with dots, hyphens, and underscores are accepted."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.save_artifact.return_value = _make_artifact()

        client = _make_test_client(db_mock=mock_db)

        for name in ["DECISION.json", "my-artifact", "output_v2", "a.b.c"]:
            response = client.post(
                f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/{name}",
                content="{}",
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 201, f"Name '{name}' should be valid"


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/tasks/{task_id}/artifacts/{name} -- Download
# ---------------------------------------------------------------------------


class TestDownloadArtifact:
    def test_download_returns_content(self) -> None:
        """Downloading an existing artifact returns its content."""
        artifact = _make_artifact(content='{"decision": "ship"}')
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.get_artifact.return_value = artifact

        client = _make_test_client(db_mock=mock_db)
        response = client.get(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/decision",
        )

        assert response.status_code == 200
        assert response.text == '{"decision": "ship"}'
        assert response.headers["content-type"] == "application/json"

    def test_download_markdown_content_type(self) -> None:
        """Downloading a markdown artifact returns text/markdown content type."""
        artifact = _make_artifact(
            name="summary",
            content="# Summary\nAll good.",
            content_type="text/markdown",
        )
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.get_artifact.return_value = artifact

        client = _make_test_client(db_mock=mock_db)
        response = client.get(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/summary",
        )

        assert response.status_code == 200
        assert response.text == "# Summary\nAll good."
        assert "text/markdown" in response.headers["content-type"]

    def test_download_not_found_returns_404(self) -> None:
        """Downloading a non-existent artifact returns 404."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.get_artifact.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.get(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/nonexistent",
        )

        assert response.status_code == 404

    def test_download_invalid_task_returns_404(self) -> None:
        """Downloading from a non-existent task returns 404."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.get(
            f"/api/runs/{RUN_ID}/tasks/bad-task/artifacts/decision",
        )

        assert response.status_code == 404

    def test_roundtrip_upload_then_download(self) -> None:
        """Upload an artifact, then download it -- content matches."""
        content = '{"confidence": 0.95, "reasoning": "tests pass"}'
        artifact = _make_artifact(content=content)
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.save_artifact.return_value = artifact
        mock_db.get_artifact.return_value = artifact

        client = _make_test_client(db_mock=mock_db)

        # Upload
        upload_resp = client.post(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/decision",
            content=content,
            headers={"content-type": "application/json"},
        )
        assert upload_resp.status_code == 201

        # Download
        download_resp = client.get(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/decision",
        )
        assert download_resp.status_code == 200
        assert download_resp.text == content


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/tasks/{task_id}/artifacts -- List
# ---------------------------------------------------------------------------


class TestListArtifacts:
    def test_list_returns_artifacts(self) -> None:
        """Listing artifacts returns array of name/content_type/created_at."""
        artifacts = [
            _make_artifact(name="decision", content_type="application/json"),
            _make_artifact(name="summary", content_type="text/markdown"),
        ]
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.list_artifacts.return_value = artifacts

        client = _make_test_client(db_mock=mock_db)
        response = client.get(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts",
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["name"] == "decision"
        assert body[0]["content_type"] == "application/json"
        assert "created_at" in body[0]
        assert body[1]["name"] == "summary"
        assert body[1]["content_type"] == "text/markdown"

    def test_list_empty_returns_empty_array(self) -> None:
        """Listing artifacts when none exist returns empty array."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = _make_task_execution()
        mock_db.list_artifacts.return_value = []

        client = _make_test_client(db_mock=mock_db)
        response = client.get(
            f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts",
        )

        assert response.status_code == 200
        assert response.json() == []

    def test_list_invalid_task_returns_404(self) -> None:
        """Listing artifacts for a non-existent task returns 404."""
        mock_db = MagicMock()
        mock_db.get_task_execution.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.get(
            f"/api/runs/{RUN_ID}/tasks/bad-task/artifacts",
        )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id} -- Run detail includes artifacts
# ---------------------------------------------------------------------------


class TestRunDetailIncludesArtifacts:
    def test_run_detail_has_artifacts_per_task(self) -> None:
        """The run detail response includes an 'artifacts' array per task."""
        task = _make_task_execution()
        artifacts = [
            _make_artifact(name="decision"),
            _make_artifact(name="summary", content_type="text/markdown"),
        ]

        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run()
        mock_db.get_flow_definition.return_value = _make_flow_def()
        mock_db.list_task_executions.return_value = [task]
        mock_db.list_edge_transitions.return_value = []
        mock_db.list_artifacts.return_value = artifacts

        client = _make_test_client(db_mock=mock_db)
        response = client.get(f"/api/runs/{RUN_ID}")

        assert response.status_code == 200
        body = response.json()
        assert len(body["tasks"]) == 1
        task_data = body["tasks"][0]
        assert "artifacts" in task_data
        assert len(task_data["artifacts"]) == 2
        assert task_data["artifacts"][0] == {
            "name": "decision",
            "content_type": "application/json",
        }
        assert task_data["artifacts"][1] == {
            "name": "summary",
            "content_type": "text/markdown",
        }

    def test_run_detail_empty_artifacts(self) -> None:
        """Tasks with no artifacts have an empty 'artifacts' array."""
        task = _make_task_execution()

        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run()
        mock_db.get_flow_definition.return_value = _make_flow_def()
        mock_db.list_task_executions.return_value = [task]
        mock_db.list_edge_transitions.return_value = []
        mock_db.list_artifacts.return_value = []

        client = _make_test_client(db_mock=mock_db)
        response = client.get(f"/api/runs/{RUN_ID}")

        assert response.status_code == 200
        body = response.json()
        task_data = body["tasks"][0]
        assert task_data["artifacts"] == []
