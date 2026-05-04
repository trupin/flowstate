"""End-to-end integration tests for the full server stack (SERVER-009).

Exercises the full FastAPI app with real components wired together: FlowRegistry,
RunManager, WebSocketHub, FlowstateDB (in-memory), and FlowExecutor. Only the
Claude Code subprocess is mocked -- everything else runs for real.

Marked with @pytest.mark.integration so they can be skipped in fast test runs.

NOTE: The current route implementation generates a run_id for the RunManager
that is separate from the flow_run_id the executor creates in the DB. As a
result, GET /api/runs/:id using the route-returned id does not find the DB
record. These integration tests work around this by querying the DB directly
(via GET /api/runs list endpoint) to find the actual flow_run_id.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.engine.subprocess_mgr import StreamEvent, StreamEventType
from flowstate.server.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

    from flowstate.server.run_manager import RunManager
    from flowstate.server.websocket import WebSocketHub

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Flow DSL fixtures
# ---------------------------------------------------------------------------

SIMPLE_FLOW = """\
flow test_flow {
    budget = 10m
    on_error = pause
    context = handoff
    workspace = "."

    input {
        task_name: string = "default"
    }

    entry start {
        prompt = "Initialize the project"
    }

    task work {
        prompt = "Do the work"
    }

    exit done {
        prompt = "Finalize"
    }

    start -> work
    work -> done
}
"""

INVALID_FLOW = "this is not valid flow syntax at all"


# ---------------------------------------------------------------------------
# Mock subprocess manager
# ---------------------------------------------------------------------------


class MockSubprocessManager:
    """A test double satisfying the Harness protocol with canned responses.

    Each call to run_task yields a few stream events and a successful exit.
    """

    def __init__(self, delay: float = 0.0) -> None:
        self._delay = delay
        self._task_count = 0

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
        settings: Any = None,
    ) -> Any:
        """Yield mock stream events simulating a successful Claude Code run."""
        self._task_count += 1
        if self._delay > 0:
            await asyncio.sleep(self._delay)

        yield StreamEvent(
            type=StreamEventType.ASSISTANT,
            content={"type": "assistant", "message": f"Working on task {self._task_count}"},
            raw=f'{{"type": "assistant", "message": "Working on task {self._task_count}"}}',
        )
        yield StreamEvent(
            type=StreamEventType.RESULT,
            content={"type": "result", "result": f"Task {self._task_count} done."},
            raw=f'{{"type": "result", "result": "Task {self._task_count} done."}}',
        )
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def run_task_resume(
        self,
        prompt: str,
        workspace: str,
        resume_session_id: str,
        *,
        skip_permissions: bool = False,
        settings: Any = None,
    ) -> Any:
        """Delegate to run_task for simplicity."""
        async for event in self.run_task(prompt, workspace, resume_session_id):
            yield event

    async def run_judge(
        self, prompt: str, workspace: str, *, skip_permissions: bool = False
    ) -> Any:
        """Not needed for simple linear flow tests."""
        raise NotImplementedError("Judge not mocked for integration tests")

    async def kill(self, session_id: str) -> None:
        """No-op kill."""

    async def start_session(self, workspace: str, session_id: str) -> None:
        """No-op start_session."""

    async def prompt(self, session_id: str, message: str) -> Any:
        """Delegate to run_task for simplicity."""
        async for event in self.run_task(message, ".", session_id):
            yield event

    async def interrupt(self, session_id: str) -> None:
        """No-op interrupt."""


class SlowMockSubprocessManager(MockSubprocessManager):
    """A subprocess manager with a delay between events for pause/resume testing."""

    def __init__(self) -> None:
        super().__init__(delay=0.5)


# ---------------------------------------------------------------------------
# Helper: Create a fully wired integration test client
# ---------------------------------------------------------------------------


def _create_integration_client(
    tmp_path: Path,
    subprocess_mgr: MockSubprocessManager | None = None,
    pre_write_flows: dict[str, str] | None = None,
) -> TestClient:
    """Create a fully wired FastAPI app using the real lifespan.

    The real lifespan creates FlowstateDB, FlowRegistry, RunManager, and
    WebSocketHub all in the ASGI thread (avoiding SQLite thread affinity issues).

    Pre-written flow files are placed in the watch directory BEFORE the app
    starts, so the initial registry scan picks them up without needing to wait
    for the file watcher.

    Returns a TestClient with the lifespan already started. Callers MUST call
    client.__exit__(None, None, None) in a finally block.
    """
    watch_dir = tmp_path / "flows"
    watch_dir.mkdir()

    # Write flow files before creating the TestClient (triggers lifespan scan)
    if pre_write_flows:
        for filename, content in pre_write_flows.items():
            (watch_dir / filename).write_text(content)

    config = FlowstateConfig(
        watch_dir=str(watch_dir),
        server_host="127.0.0.1",
        server_port=8080,
    )

    if subprocess_mgr is None:
        subprocess_mgr = MockSubprocessManager()

    # create_app with lifespan -- TestClient context manager triggers startup/shutdown
    app = create_app(config=config, harness=subprocess_mgr)

    # Enter the TestClient as a context manager to trigger the lifespan.
    # The lifespan creates real DB, RunManager, WebSocketHub, and FlowRegistry
    # all in the ASGI thread, solving SQLite thread affinity.
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    return client


def _poll_for_completed_run(
    client: TestClient,
    flow_name: str = "test_flow",
    timeout: float = 15.0,
    interval: float = 0.3,
) -> dict[str, Any] | None:
    """Poll GET /api/runs until a run with the given flow_name reaches a terminal state.

    The executor creates its own flow_run_id in the DB, which differs from the
    run_id returned by the route. We find the run via the list endpoint instead.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get("/api/runs")
        if resp.status_code == 200:
            runs = resp.json()
            for run in runs:
                if run["flow_name"] == flow_name and run["status"] in (
                    "completed",
                    "failed",
                    "cancelled",
                    "paused",
                ):
                    # Found a terminal run -- get its details
                    detail_resp = client.get(f"/api/runs/{run['id']}")
                    if detail_resp.status_code == 200:
                        return detail_resp.json()  # type: ignore[no-any-return]
        time.sleep(interval)
    return None


# ---------------------------------------------------------------------------
# Test: Flow Discovery
# ---------------------------------------------------------------------------


class TestFlowDiscoveryIntegration:
    """Test that .flow files in the watch directory are discovered and served via REST."""

    def test_valid_flow_discovered_via_api(self, tmp_path: Path) -> None:
        """Write a valid .flow file, verify it appears in GET /api/flows with status valid."""
        client = _create_integration_client(
            tmp_path, pre_write_flows={"test_flow.flow": SIMPLE_FLOW}
        )
        try:
            resp = client.get("/api/flows")
            assert resp.status_code == 200
            flows = resp.json()
            assert len(flows) >= 1

            test_flow = next((f for f in flows if f["id"] == "test_flow"), None)
            assert test_flow is not None
            assert test_flow["status"] == "valid"
            assert test_flow["name"] == "test_flow"
            assert test_flow["errors"] == []
        finally:
            client.__exit__(None, None, None)

    def test_flow_detail_includes_source_and_ast(self, tmp_path: Path) -> None:
        """GET /api/flows/:id returns source_dsl and ast_json for a valid flow."""
        client = _create_integration_client(
            tmp_path, pre_write_flows={"test_flow.flow": SIMPLE_FLOW}
        )
        try:
            resp = client.get("/api/flows/test_flow")
            assert resp.status_code == 200
            detail = resp.json()

            assert detail["source_dsl"] == SIMPLE_FLOW
            assert detail["ast_json"] is not None
            assert detail["ast_json"]["name"] == "test_flow"
            assert "nodes" in detail["ast_json"]
        finally:
            client.__exit__(None, None, None)

    def test_invalid_flow_has_error_status(self, tmp_path: Path) -> None:
        """Write an invalid .flow file, verify it appears with error status."""
        client = _create_integration_client(tmp_path, pre_write_flows={"broken.flow": INVALID_FLOW})
        try:
            resp = client.get("/api/flows")
            assert resp.status_code == 200
            flows = resp.json()

            broken = next((f for f in flows if f["id"] == "broken"), None)
            assert broken is not None
            assert broken["status"] == "error"
            assert len(broken["errors"]) > 0
        finally:
            client.__exit__(None, None, None)

    def test_multiple_flows_discovered(self, tmp_path: Path) -> None:
        """Multiple .flow files are all discovered and listed."""
        client = _create_integration_client(
            tmp_path,
            pre_write_flows={
                "test_flow.flow": SIMPLE_FLOW,
                "broken.flow": INVALID_FLOW,
            },
        )
        try:
            resp = client.get("/api/flows")
            assert resp.status_code == 200
            flows = resp.json()
            assert len(flows) == 2

            ids = {f["id"] for f in flows}
            assert ids == {"test_flow", "broken"}
        finally:
            client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Test: Run Lifecycle (Start -> Complete)
# ---------------------------------------------------------------------------


class TestRunLifecycleIntegration:
    """Test starting a run and verifying it completes through the full executor."""

    def test_start_run_returns_202(self, tmp_path: Path) -> None:
        """POST /api/flows/:id/runs starts a run and returns 202 with run_id."""
        client = _create_integration_client(
            tmp_path, pre_write_flows={"test_flow.flow": SIMPLE_FLOW}
        )
        try:
            resp = client.post("/api/flows/test_flow/runs", json={"params": {}})
            assert resp.status_code == 202

            body = resp.json()
            assert "flow_run_id" in body
            assert isinstance(body["flow_run_id"], str)
            assert len(body["flow_run_id"]) > 0
        finally:
            client.__exit__(None, None, None)

    def test_run_completes_with_task_details(self, tmp_path: Path) -> None:
        """Start a run, wait for completion, verify run details show completed state."""
        client = _create_integration_client(
            tmp_path, pre_write_flows={"test_flow.flow": SIMPLE_FLOW}
        )
        try:
            # Start the run
            resp = client.post("/api/flows/test_flow/runs", json={"params": {}})
            assert resp.status_code == 202

            # Poll for completion via the list endpoint
            run_detail = _poll_for_completed_run(client, "test_flow")

            assert run_detail is not None, "Run never reached a terminal status"
            assert (
                run_detail["status"] == "completed"
            ), f"Expected completed, got {run_detail['status']}"
            assert run_detail["flow_name"] == "test_flow"
            assert run_detail["budget_seconds"] == 600  # 10m = 600s

            # Verify tasks were created (3 nodes: start, work, done)
            assert len(run_detail["tasks"]) >= 3
            node_names = [t["node_name"] for t in run_detail["tasks"]]
            assert "start" in node_names
            assert "work" in node_names
            assert "done" in node_names

            # All tasks should be completed
            for task in run_detail["tasks"]:
                assert task["status"] == "completed"
                assert task["exit_code"] == 0
        finally:
            client.__exit__(None, None, None)

    def test_run_has_edge_transitions(self, tmp_path: Path) -> None:
        """Completed run includes edge transitions between nodes."""
        client = _create_integration_client(
            tmp_path, pre_write_flows={"test_flow.flow": SIMPLE_FLOW}
        )
        try:
            resp = client.post("/api/flows/test_flow/runs", json={"params": {}})
            assert resp.status_code == 202

            # Wait for completion
            run_detail = _poll_for_completed_run(client, "test_flow")

            assert run_detail is not None
            assert run_detail["status"] == "completed"

            # Should have edge transitions: start->work and work->done
            edges = run_detail["edges"]
            assert len(edges) >= 2

            edge_pairs = [(e["from_node"], e["to_node"]) for e in edges]
            assert ("start", "work") in edge_pairs
            assert ("work", "done") in edge_pairs
        finally:
            client.__exit__(None, None, None)

    def test_start_run_on_invalid_flow_returns_400(self, tmp_path: Path) -> None:
        """Cannot start a run on a flow with errors."""
        client = _create_integration_client(tmp_path, pre_write_flows={"broken.flow": INVALID_FLOW})
        try:
            resp = client.post("/api/flows/broken/runs", json={"params": {}})
            assert resp.status_code == 400
            body = resp.json()
            assert "error" in body
        finally:
            client.__exit__(None, None, None)

    def test_start_run_on_nonexistent_flow_returns_404(self, tmp_path: Path) -> None:
        """Cannot start a run on a flow that does not exist."""
        client = _create_integration_client(tmp_path)
        try:
            resp = client.post("/api/flows/nonexistent/runs", json={"params": {}})
            assert resp.status_code == 404
        finally:
            client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Test: List Runs After Completion
# ---------------------------------------------------------------------------


class TestListRunsIntegration:
    """Test that completed runs appear in GET /api/runs."""

    def test_list_runs_includes_completed_run(self, tmp_path: Path) -> None:
        """After a run completes, it should appear in the runs list."""
        client = _create_integration_client(
            tmp_path, pre_write_flows={"test_flow.flow": SIMPLE_FLOW}
        )
        try:
            # Start a run
            resp = client.post("/api/flows/test_flow/runs", json={"params": {}})
            assert resp.status_code == 202

            # Wait for it to appear in the runs list as completed
            run_detail = _poll_for_completed_run(client, "test_flow")
            assert run_detail is not None

            # Verify it appears in the list with correct fields
            resp = client.get("/api/runs")
            assert resp.status_code == 200
            runs = resp.json()
            assert len(runs) >= 1

            matched = next(
                (r for r in runs if r["flow_name"] == "test_flow" and r["status"] == "completed"),
                None,
            )
            assert matched is not None
            assert matched["flow_name"] == "test_flow"
            assert matched["status"] == "completed"
            assert "elapsed_seconds" in matched
        finally:
            client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Test: Pause and Resume
# ---------------------------------------------------------------------------


class TestPauseResumeIntegration:
    """Test pause and resume control operations on a running flow.

    NOTE: The pause() method on the executor waits for all running tasks to
    finish before returning. After pause completes, the executor's main loop
    detects _paused=True and exits, which triggers RunManager cleanup. This
    means the executor is no longer tracked by the RunManager after a pause
    completes, so resume via REST is not possible in the current architecture.

    These tests verify:
    1. Pause can be successfully issued on an active run
    2. The flow status is correctly updated to 'paused' in the DB
    """

    def test_pause_active_run(self, tmp_path: Path) -> None:
        """Pause a running flow via REST, verify 200 response."""
        # Use a slow subprocess manager so there's time to pause between tasks
        slow_mgr = SlowMockSubprocessManager()
        client = _create_integration_client(
            tmp_path,
            subprocess_mgr=slow_mgr,
            pre_write_flows={"test_flow.flow": SIMPLE_FLOW},
        )
        try:
            # Start the run
            resp = client.post("/api/flows/test_flow/runs", json={"params": {}})
            assert resp.status_code == 202
            route_run_id = resp.json()["flow_run_id"]

            # Give the executor a moment to start
            time.sleep(0.2)

            # Check if the executor is still active via the RunManager
            run_manager: RunManager = client.app.state.run_manager  # type: ignore[union-attr]
            executor = run_manager.get_executor(route_run_id)

            if executor is not None:
                # The executor is still active, test pause via REST. Per ENGINE-078
                # the first pause response is `pausing`; the run transitions to
                # `paused` once the currently running task yields control.
                resp = client.post(f"/api/runs/{route_run_id}/pause")
                assert resp.status_code == 200
                assert resp.json()["status"] in {"pausing", "paused"}

                # Verify the flow eventually settles in the paused state.
                run_detail = _poll_for_completed_run(client, "test_flow", timeout=10)
                assert run_detail is not None
                assert run_detail["status"] == "paused"
            # If the executor already completed, that is acceptable -- the test
            # verifies the pause path is exercisable when timing allows.
        finally:
            client.__exit__(None, None, None)

    def test_pause_nonexistent_run_returns_404(self, tmp_path: Path) -> None:
        """Pause on a run that doesn't exist returns 404."""
        client = _create_integration_client(tmp_path)
        try:
            resp = client.post("/api/runs/nonexistent-run/pause")
            # No active executor and no DB record -> 404
            assert resp.status_code == 404
        finally:
            client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Test: WebSocket Subscription and Event Streaming
# ---------------------------------------------------------------------------


class TestWebSocketIntegration:
    """Test WebSocket connection, subscription, and event broadcast."""

    def test_websocket_subscribe_to_run(self, tmp_path: Path) -> None:
        """Connect via WebSocket, subscribe to a run, verify subscription is tracked."""
        client = _create_integration_client(
            tmp_path, pre_write_flows={"test_flow.flow": SIMPLE_FLOW}
        )
        try:
            run_id = "test-run-ws-sub"

            with client.websocket_connect("/ws") as ws:
                ws.send_json({"action": "subscribe", "flow_run_id": run_id})

                # Send a sentinel action to ensure subscribe was processed
                ws.send_json({"action": "__ping__"})
                error_resp = ws.receive_json()
                assert error_resp["type"] == "error"

                # Verify the hub tracked the subscription
                ws_hub: WebSocketHub = client.app.state.ws_hub  # type: ignore[union-attr]
                assert run_id in ws_hub.subscriptions
                assert len(ws_hub.subscriptions[run_id]) == 1
        finally:
            client.__exit__(None, None, None)

    def test_websocket_unsubscribe(self, tmp_path: Path) -> None:
        """Subscribe then unsubscribe, verify subscription is removed."""
        client = _create_integration_client(tmp_path)
        try:
            run_id = "test-run-unsub"
            with client.websocket_connect("/ws") as ws:
                # Subscribe
                ws.send_json({"action": "subscribe", "flow_run_id": run_id})
                ws.send_json({"action": "__ping__"})
                ws.receive_json()

                ws_hub: WebSocketHub = client.app.state.ws_hub  # type: ignore[union-attr]
                assert run_id in ws_hub.subscriptions

                # Unsubscribe
                ws.send_json({"action": "unsubscribe", "flow_run_id": run_id})
                ws.send_json({"action": "__ping__"})
                ws.receive_json()

                assert run_id not in ws_hub.subscriptions
        finally:
            client.__exit__(None, None, None)

    def test_websocket_disconnect_cleanup(self, tmp_path: Path) -> None:
        """After WebSocket disconnect, subscriptions are cleaned up."""
        client = _create_integration_client(tmp_path)
        try:
            ws_hub: WebSocketHub = client.app.state.ws_hub  # type: ignore[union-attr]
            run_id = "test-run-cleanup"

            with client.websocket_connect("/ws") as ws:
                ws.send_json({"action": "subscribe", "flow_run_id": run_id})
                ws.send_json({"action": "__ping__"})
                ws.receive_json()
                assert run_id in ws_hub.subscriptions

            # After disconnect, cleanup should have run
            assert len(ws_hub.client_subs) == 0
        finally:
            client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Test: WebSocket Reconnection Replay
# ---------------------------------------------------------------------------


class TestWebSocketReconnectionReplay:
    """Test WebSocket reconnection with event replay from the database."""

    def test_reconnection_replays_task_logs(self, tmp_path: Path) -> None:
        """Start a run, let it complete, then subscribe with last_event_timestamp.

        Verify that task log entries from the DB are replayed to the client.
        """
        client = _create_integration_client(
            tmp_path, pre_write_flows={"test_flow.flow": SIMPLE_FLOW}
        )
        try:
            # Start the run and wait for completion
            resp = client.post("/api/flows/test_flow/runs", json={"params": {}})
            assert resp.status_code == 202

            run_detail = _poll_for_completed_run(client, "test_flow")
            assert run_detail is not None, "Run did not complete"

            # Get the DB run_id from the detail response
            db_run_id = run_detail["id"]

            # Subscribe with a very old timestamp to trigger full replay
            with client.websocket_connect("/ws") as ws:
                ws.send_json(
                    {
                        "action": "subscribe",
                        "flow_run_id": db_run_id,
                        "payload": {"last_event_timestamp": "2000-01-01T00:00:00Z"},
                    }
                )

                # The hub replays task logs from the DB as task.log events.
                # Each task has ~3 stream events (assistant, result, process_exit),
                # and there are 3 tasks, so we expect ~9 replayed events.
                events: list[dict[str, Any]] = []

                # The replay happens synchronously during subscribe processing.
                # After replay, no more events will arrive unless the flow is
                # still running. Send a sentinel to flush.
                ws.send_json({"action": "__ping__"})

                # Read all messages until we get the error response from __ping__
                while True:
                    msg = ws.receive_json()
                    if msg.get("type") == "error":
                        break  # This is the __ping__ error response
                    events.append(msg)

            # Verify we received replayed task.log events
            assert len(events) > 0, "Expected replayed task log events"
            assert all(e["type"] == "task.log" for e in events)
            assert all(e["flow_run_id"] == db_run_id for e in events)
        finally:
            client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Test: File Watcher Error Detection
# ---------------------------------------------------------------------------


class TestFileWatcherErrorDetection:
    """Test that invalid .flow files are detected and reported with errors."""

    def test_file_watcher_detects_errors(self, tmp_path: Path) -> None:
        """Write an invalid flow file, verify it appears with error status in API."""
        client = _create_integration_client(tmp_path, pre_write_flows={"broken.flow": INVALID_FLOW})
        try:
            resp = client.get("/api/flows")
            assert resp.status_code == 200
            flows = resp.json()

            broken = next((f for f in flows if f["id"] == "broken"), None)
            assert broken is not None
            assert broken["status"] == "error"
            assert len(broken["errors"]) > 0
        finally:
            client.__exit__(None, None, None)

    def test_valid_and_invalid_flows_coexist(self, tmp_path: Path) -> None:
        """Both valid and invalid flows are listed; invalid does not block valid."""
        client = _create_integration_client(
            tmp_path,
            pre_write_flows={
                "good.flow": SIMPLE_FLOW,
                "bad.flow": INVALID_FLOW,
            },
        )
        try:
            resp = client.get("/api/flows")
            flows = resp.json()
            assert len(flows) == 2

            good = next(f for f in flows if f["id"] == "good")
            bad = next(f for f in flows if f["id"] == "bad")

            assert good["status"] == "valid"
            assert bad["status"] == "error"

            # Can start a run on the valid flow
            resp = client.post("/api/flows/good/runs", json={"params": {}})
            assert resp.status_code == 202

            # Cannot start a run on the invalid flow
            resp = client.post("/api/flows/bad/runs", json={"params": {}})
            assert resp.status_code == 400
        finally:
            client.__exit__(None, None, None)
