"""Tests for Task Queue REST API endpoints (SERVER-011).

All tests mock the FlowstateDB -- never use real SQLite. Uses FastAPI TestClient
with mocked FlowRegistry, RunManager, and FlowstateDB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app
from flowstate.server.flow_registry import DiscoveredFlow, FlowRegistry
from flowstate.server.run_manager import RunManager
from flowstate.state.models import TaskNodeHistoryRow, TaskRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_FLOW = DiscoveredFlow(
    id="code_review",
    name="code_review",
    file_path="/flows/code_review.flow",
    source_dsl="flow code_review { ... }",
    status="valid",
    errors=[],
    ast_json={"name": "code_review", "nodes": {}, "edges": []},
    params=[],
)


def _make_task_row(
    task_id: str = "task-1",
    flow_name: str = "code_review",
    title: str = "Review PR #42",
    description: str | None = "Check auth module",
    status: str = "queued",
    priority: int = 0,
    flow_run_id: str | None = None,
    parent_task_id: str | None = None,
) -> TaskRow:
    return TaskRow(
        id=task_id,
        flow_name=flow_name,
        title=title,
        description=description,
        status=status,
        current_node=None,
        params_json=None,
        output_json=None,
        parent_task_id=parent_task_id,
        created_by="user",
        flow_run_id=flow_run_id,
        priority=priority,
        created_at="2025-01-01T00:00:00+00:00",
        started_at=None,
        completed_at=None,
        error_message=None,
    )


def _make_history_row(
    history_id: int = 1,
    task_id: str = "task-1",
    node_name: str = "start",
    flow_run_id: str | None = "run-1",
) -> TaskNodeHistoryRow:
    return TaskNodeHistoryRow(
        id=history_id,
        task_id=task_id,
        node_name=node_name,
        flow_run_id=flow_run_id,
        started_at="2025-01-01T00:00:00+00:00",
        completed_at="2025-01-01T00:01:00+00:00",
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
    app.state.flow_registry = mock_registry

    # Mock or real DB
    if db_mock is None:
        db_mock = MagicMock()
    app.state.db = db_mock

    # RunManager
    if run_manager is None:
        run_manager = RunManager()
    app.state.run_manager = run_manager

    # Mock WebSocket hub
    mock_ws_hub = MagicMock()
    mock_ws_hub.on_flow_event = MagicMock()
    app.state.ws_hub = mock_ws_hub

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /api/flows/:flow_name/tasks -- Submit task
# ---------------------------------------------------------------------------


class TestSubmitTask:
    def test_submit_task_returns_201(self) -> None:
        """Submitting a task to a valid flow returns 201 with task data."""
        task = _make_task_row()
        mock_db = MagicMock()
        mock_db.create_task.return_value = "task-1"
        mock_db.get_task.return_value = task

        client = _make_test_client(
            flows={"code_review": VALID_FLOW},
            db_mock=mock_db,
        )
        response = client.post(
            "/api/flows/code_review/tasks",
            json={"title": "Review PR #42", "description": "Check auth module"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "task-1"
        assert body["flow_name"] == "code_review"
        assert body["title"] == "Review PR #42"
        assert body["status"] == "queued"

    def test_submit_task_with_params_and_priority(self) -> None:
        """Submitting a task with params and priority passes them through."""
        task = _make_task_row(priority=5)
        mock_db = MagicMock()
        mock_db.create_task.return_value = "task-1"
        mock_db.get_task.return_value = task

        client = _make_test_client(
            flows={"code_review": VALID_FLOW},
            db_mock=mock_db,
        )
        response = client.post(
            "/api/flows/code_review/tasks",
            json={
                "title": "High-priority review",
                "params": {"focus": "security"},
                "priority": 5,
            },
        )

        assert response.status_code == 201
        # Verify the DB was called with correct args
        call_kwargs = mock_db.create_task.call_args
        assert call_kwargs[1]["priority"] == 5
        assert '"focus"' in (call_kwargs[1]["params_json"] or "")

    def test_submit_task_flow_not_found(self) -> None:
        """Submitting a task to a nonexistent flow returns 404."""
        client = _make_test_client()
        response = client.post(
            "/api/flows/nonexistent/tasks",
            json={"title": "Test"},
        )
        assert response.status_code == 404
        body = response.json()
        assert "nonexistent" in body["error"]


# ---------------------------------------------------------------------------
# GET /api/flows/:flow_name/tasks -- List flow tasks
# ---------------------------------------------------------------------------


class TestListFlowTasks:
    def test_list_flow_tasks(self) -> None:
        """List tasks for a flow returns all tasks for that flow."""
        tasks = [
            _make_task_row("task-1"),
            _make_task_row("task-2", title="Second task"),
        ]
        mock_db = MagicMock()
        mock_db.list_tasks.return_value = tasks

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/flows/code_review/tasks")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["id"] == "task-1"
        assert body[1]["id"] == "task-2"
        mock_db.list_tasks.assert_called_once_with(flow_name="code_review", status=None)

    def test_list_flow_tasks_filter_by_status(self) -> None:
        """List tasks for a flow with status filter calls DB accordingly."""
        mock_db = MagicMock()
        mock_db.list_tasks.return_value = []

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/flows/code_review/tasks?status=queued")

        assert response.status_code == 200
        mock_db.list_tasks.assert_called_once_with(flow_name="code_review", status="queued")

    def test_list_flow_tasks_empty(self) -> None:
        """List tasks for a flow with no tasks returns empty list."""
        mock_db = MagicMock()
        mock_db.list_tasks.return_value = []

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/flows/code_review/tasks")

        assert response.status_code == 200
        assert response.json() == []


# ---------------------------------------------------------------------------
# GET /api/tasks -- List all tasks
# ---------------------------------------------------------------------------


class TestListAllTasks:
    def test_list_all_tasks(self) -> None:
        """List all tasks returns tasks across flows."""
        tasks = [
            _make_task_row("task-1", flow_name="flow_a"),
            _make_task_row("task-2", flow_name="flow_b"),
        ]
        mock_db = MagicMock()
        mock_db.list_tasks.return_value = tasks

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/tasks")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        mock_db.list_tasks.assert_called_once_with(status=None, limit=100)

    def test_list_all_tasks_with_status_and_limit(self) -> None:
        """List all tasks respects status and limit query params."""
        mock_db = MagicMock()
        mock_db.list_tasks.return_value = []

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/tasks?status=running&limit=50")

        assert response.status_code == 200
        mock_db.list_tasks.assert_called_once_with(status="running", limit=50)


# ---------------------------------------------------------------------------
# GET /api/tasks/:task_id -- Get task detail
# ---------------------------------------------------------------------------


class TestGetTaskDetail:
    def test_get_task_with_history_and_children(self) -> None:
        """Get task detail returns task with history and children."""
        task = _make_task_row("task-1")
        history = [_make_history_row(1, "task-1", "start"), _make_history_row(2, "task-1", "done")]
        child = _make_task_row("task-2", parent_task_id="task-1")

        mock_db = MagicMock()
        mock_db.get_task.return_value = task
        mock_db.get_task_history.return_value = history
        mock_db.get_child_tasks.return_value = [child]

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/tasks/task-1")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "task-1"
        assert body["title"] == "Review PR #42"
        assert len(body["history"]) == 2
        assert body["history"][0]["node_name"] == "start"
        assert body["history"][1]["node_name"] == "done"
        assert len(body["children"]) == 1
        assert body["children"][0]["id"] == "task-2"

    def test_get_task_not_found(self) -> None:
        """Get nonexistent task returns 404."""
        mock_db = MagicMock()
        mock_db.get_task.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.get("/api/tasks/nonexistent")

        assert response.status_code == 404
        body = response.json()
        assert "nonexistent" in body["error"]


# ---------------------------------------------------------------------------
# POST /api/tasks/:task_id/cancel -- Cancel task
# ---------------------------------------------------------------------------


class TestCancelTask:
    def test_cancel_queued_task(self) -> None:
        """Cancel a queued task sets status to cancelled."""
        task = _make_task_row("task-1", status="queued")
        mock_db = MagicMock()
        mock_db.get_task.return_value = task

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/tasks/task-1/cancel")

        assert response.status_code == 200
        assert response.json() == {"status": "cancelled"}
        mock_db.update_task_queue_status.assert_called_once_with("task-1", "cancelled")

    def test_cancel_running_task_with_executor(self) -> None:
        """Cancel a running task also cancels the associated flow run."""
        task = _make_task_row("task-1", status="running", flow_run_id="run-1")
        mock_db = MagicMock()
        mock_db.get_task.return_value = task

        mock_executor = MagicMock()
        mock_executor.cancel = AsyncMock()

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)
        response = client.post("/api/tasks/task-1/cancel")

        assert response.status_code == 200
        assert response.json() == {"status": "cancelled"}
        mock_executor.cancel.assert_called_once_with("run-1")
        mock_db.update_task_queue_status.assert_called_once_with("task-1", "cancelled")

    def test_cancel_completed_task_returns_409(self) -> None:
        """Cannot cancel a completed task."""
        task = _make_task_row("task-1", status="completed")
        mock_db = MagicMock()
        mock_db.get_task.return_value = task

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/tasks/task-1/cancel")

        assert response.status_code == 409
        body = response.json()
        assert "completed" in body["error"]

    def test_cancel_nonexistent_task_returns_404(self) -> None:
        """Cancel nonexistent task returns 404."""
        mock_db = MagicMock()
        mock_db.get_task.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.post("/api/tasks/nonexistent/cancel")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/tasks/:task_id -- Update task
# ---------------------------------------------------------------------------


class TestUpdateTask:
    def test_update_queued_task(self) -> None:
        """Update a queued task's title and priority."""
        original = _make_task_row("task-1", status="queued")
        updated = _make_task_row("task-1", status="queued", title="Updated Title", priority=10)
        mock_db = MagicMock()
        mock_db.get_task.side_effect = [original, updated]

        client = _make_test_client(db_mock=mock_db)
        response = client.patch(
            "/api/tasks/task-1",
            json={"title": "Updated Title", "priority": 10},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Updated Title"
        assert body["priority"] == 10
        mock_db.update_task.assert_called_once()

    def test_update_non_queued_task_returns_409(self) -> None:
        """Cannot edit a running task."""
        task = _make_task_row("task-1", status="running")
        mock_db = MagicMock()
        mock_db.get_task.return_value = task

        client = _make_test_client(db_mock=mock_db)
        response = client.patch(
            "/api/tasks/task-1",
            json={"title": "New title"},
        )

        assert response.status_code == 409
        body = response.json()
        assert "queued" in body["error"].lower()

    def test_update_nonexistent_task_returns_404(self) -> None:
        """Update nonexistent task returns 404."""
        mock_db = MagicMock()
        mock_db.get_task.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.patch(
            "/api/tasks/nonexistent",
            json={"title": "New title"},
        )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/tasks/:task_id -- Delete task
# ---------------------------------------------------------------------------


class TestDeleteTask:
    def test_delete_queued_task(self) -> None:
        """Delete a queued task returns success."""
        task = _make_task_row("task-1", status="queued")
        mock_db = MagicMock()
        mock_db.get_task.return_value = task

        client = _make_test_client(db_mock=mock_db)
        response = client.delete("/api/tasks/task-1")

        assert response.status_code == 200
        assert response.json() == {"status": "deleted"}
        mock_db.delete_task.assert_called_once_with("task-1")

    def test_delete_non_queued_task_returns_409(self) -> None:
        """Cannot delete a running task."""
        task = _make_task_row("task-1", status="running")
        mock_db = MagicMock()
        mock_db.get_task.return_value = task

        client = _make_test_client(db_mock=mock_db)
        response = client.delete("/api/tasks/task-1")

        assert response.status_code == 409
        body = response.json()
        assert "queued" in body["error"].lower()

    def test_delete_nonexistent_task_returns_404(self) -> None:
        """Delete nonexistent task returns 404."""
        mock_db = MagicMock()
        mock_db.get_task.return_value = None

        client = _make_test_client(db_mock=mock_db)
        response = client.delete("/api/tasks/nonexistent")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/flows/:flow_name/tasks/reorder -- Reorder tasks
# ---------------------------------------------------------------------------


class TestReorderTasks:
    def test_reorder_tasks(self) -> None:
        """Reorder tasks delegates to db.reorder_tasks."""
        mock_db = MagicMock()

        client = _make_test_client(db_mock=mock_db)
        response = client.post(
            "/api/flows/code_review/tasks/reorder",
            json={"task_ids": ["task-3", "task-1", "task-2"]},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "reordered"}
        mock_db.reorder_tasks.assert_called_once_with("code_review", ["task-3", "task-1", "task-2"])


# ---------------------------------------------------------------------------
# All task route handlers are async
# ---------------------------------------------------------------------------


class TestAllTaskRoutesAsync:
    def test_all_task_routes_are_async(self) -> None:
        """Verify that all task route handler functions are async."""
        import asyncio

        from flowstate.server import routes

        route_handlers = [
            routes.submit_task,
            routes.list_flow_tasks,
            routes.list_all_tasks,
            routes.get_task,
            routes.cancel_task,
            routes.update_task,
            routes.delete_task,
            routes.reorder_tasks,
        ]
        for handler in route_handlers:
            assert asyncio.iscoroutinefunction(
                handler
            ), f"{handler.__name__} is not an async function"
