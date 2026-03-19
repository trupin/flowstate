# [SERVER-009] End-to-End Integration Test

## Domain
server

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-005, ENGINE-008
- Blocks: none

## Spec References
- specs.md Section 10.2 — "REST API" (all endpoints)
- specs.md Section 10.3 — "WebSocket Protocol" (full event flow)
- specs.md Section 10.8 — "File Watcher"
- agents/04-server.md — full server spec

## Summary
Write an end-to-end integration test that exercises the full server stack: file discovery, run management, WebSocket event streaming, and run control. This test starts a real FastAPI app (using `TestClient`), writes `.flow` files to a watched directory, verifies they appear in the API, starts a run, subscribes via WebSocket, verifies events arrive in the expected order, and exercises pause/resume. The Claude Code subprocess is mocked — the test validates the server orchestration, not the actual AI execution.

## Acceptance Criteria
- [ ] One integration test file: `tests/server/test_integration.py`
- [ ] Test starts a full FastAPI app with all components wired (FlowRegistry, RunManager, WebSocketHub, DB)
- [ ] Test creates a valid `.flow` file in a temporary watch directory
- [ ] Test verifies the flow appears in `GET /api/flows` with status "valid"
- [ ] Test starts a run via `POST /api/flows/:id/runs` and receives 202
- [ ] Test subscribes to the run via WebSocket and receives events
- [ ] Test verifies the event sequence includes: `flow.started`, at least one `task.started`, at least one `task.completed`, `flow.completed`
- [ ] Test exercises pause and resume: sends pause via WebSocket or REST, verifies `flow.status_changed` event
- [ ] Test verifies `GET /api/runs/:id` returns the completed run with task details
- [ ] Claude Code subprocess is mocked (no real `claude` process is launched)
- [ ] The test uses an in-memory SQLite database (`:memory:`)
- [ ] The test cleans up all resources (temp files, background tasks)
- [ ] All tests pass: `uv run pytest tests/server/test_integration.py`

## Technical Design

### Files to Create/Modify
- `tests/server/test_integration.py` — integration tests
- `tests/server/conftest.py` — shared fixtures (if not already created by other SERVER issues)

### Key Implementation Details

#### Test Fixture: Full App

```python
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app


SIMPLE_FLOW = """\
flow test_flow {
    budget = "10m"

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


@pytest.fixture
def integration_app(tmp_path: Path):
    """Create a fully wired FastAPI app with temp directories and mocked subprocess."""
    watch_dir = tmp_path / "flows"
    watch_dir.mkdir()

    config = FlowstateConfig(
        database_path=":memory:",
        watch_dir=str(watch_dir),
        server_host="127.0.0.1",
        server_port=8080,
    )

    # Mock the subprocess manager so no real Claude Code is invoked
    with patch("flowstate.engine.subprocess_manager.SubprocessManager") as MockSubprocess:
        mock_proc = MockSubprocess.return_value
        # Simulate a successful task: process exits with code 0
        mock_proc.start.return_value = AsyncMock()
        mock_proc.wait.return_value = 0
        mock_proc.read_output.return_value = [
            {"type": "assistant", "content": "Working on it..."},
            {"type": "result", "content": "Done."},
        ]

        app = create_app(config=config)

        with TestClient(app) as client:
            yield client, watch_dir, config
```

#### Test: Full Flow from Discovery to Completion

```python
def test_end_to_end_flow_discovery_to_run_completion(integration_app):
    client, watch_dir, config = integration_app

    # Step 1: Write a .flow file to the watch directory
    flow_file = watch_dir / "test_flow.flow"
    flow_file.write_text(SIMPLE_FLOW)

    # Give the file watcher time to detect the change
    import time
    time.sleep(1)

    # Step 2: Verify the flow appears in GET /api/flows
    resp = client.get("/api/flows")
    assert resp.status_code == 200
    flows = resp.json()
    assert len(flows) >= 1
    test_flow = next((f for f in flows if f["id"] == "test_flow"), None)
    assert test_flow is not None
    assert test_flow["status"] == "valid"
    assert test_flow["name"] == "test_flow"

    # Step 3: Get flow details
    resp = client.get("/api/flows/test_flow")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["source_dsl"] == SIMPLE_FLOW
    assert detail["ast_json"] is not None

    # Step 4: Start a run
    resp = client.post("/api/flows/test_flow/runs", json={"params": {}})
    assert resp.status_code == 202
    run_id = resp.json()["flow_run_id"]
    assert run_id

    # Step 5: Subscribe via WebSocket and collect events
    events = []
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "flow_run_id": run_id})

        # Collect events until flow.completed or timeout
        deadline = time.time() + 30  # 30 second timeout
        while time.time() < deadline:
            try:
                event = ws.receive_json(mode="text")
                events.append(event)
                if event.get("type") == "flow.completed":
                    break
            except Exception:
                break

    # Step 6: Verify event sequence
    event_types = [e["type"] for e in events]
    assert "flow.started" in event_types
    assert "task.started" in event_types
    assert "task.completed" in event_types
    assert "flow.completed" in event_types

    # Verify ordering: flow.started comes before flow.completed
    started_idx = event_types.index("flow.started")
    completed_idx = event_types.index("flow.completed")
    assert started_idx < completed_idx

    # Step 7: Verify run details via REST
    resp = client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    run_detail = resp.json()
    assert run_detail["status"] in ("completed", "running")  # may complete before we check
    assert run_detail["flow_name"] == "test_flow"
    assert len(run_detail["tasks"]) >= 1
```

#### Test: Pause and Resume

```python
def test_pause_and_resume(integration_app):
    client, watch_dir, config = integration_app

    flow_file = watch_dir / "pausable.flow"
    flow_file.write_text(SIMPLE_FLOW)
    time.sleep(1)

    # Start a run
    resp = client.post("/api/flows/pausable/runs", json={"params": {}})
    assert resp.status_code == 202
    run_id = resp.json()["flow_run_id"]

    # Pause via REST
    resp = client.post(f"/api/runs/{run_id}/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"

    # Verify via WebSocket that a status_changed event was emitted
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "flow_run_id": run_id})
        # The subscribe may replay events including the pause
        # Just verify we can subscribe without error

    # Resume
    resp = client.post(f"/api/runs/{run_id}/resume")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
```

#### Test: File Watcher Error Detection

```python
def test_file_watcher_detects_errors(integration_app):
    client, watch_dir, config = integration_app

    # Write an invalid flow file
    bad_file = watch_dir / "broken.flow"
    bad_file.write_text("this is not valid flow syntax")
    time.sleep(1)

    # Verify it appears with error status
    resp = client.get("/api/flows")
    flows = resp.json()
    broken = next((f for f in flows if f["id"] == "broken"), None)
    assert broken is not None
    assert broken["status"] == "error"
    assert len(broken["errors"]) > 0
```

#### Test: WebSocket Reconnection Replay

```python
def test_websocket_reconnection_replay(integration_app):
    client, watch_dir, config = integration_app

    flow_file = watch_dir / "replay_test.flow"
    flow_file.write_text(SIMPLE_FLOW)
    time.sleep(1)

    # Start a run and let it produce some events
    resp = client.post("/api/flows/replay_test/runs", json={"params": {}})
    run_id = resp.json()["flow_run_id"]

    # Wait briefly for events to accumulate
    time.sleep(2)

    # Connect with last_event_timestamp to get replay
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "action": "subscribe",
            "flow_run_id": run_id,
            "payload": {"last_event_timestamp": "2000-01-01T00:00:00Z"},
        })

        # Should receive replayed events
        events = []
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                event = ws.receive_json(mode="text")
                events.append(event)
            except Exception:
                break

        assert len(events) > 0, "Expected replayed events"
```

#### Mock Strategy

The integration test mocks ONLY the Claude Code subprocess execution. Everything else runs for real:
- Real FastAPI app with real middleware and routes
- Real `FlowRegistry` watching a real temp directory
- Real `WebSocketHub` with real WebSocket connections
- Real SQLite database (in-memory via `:memory:`)
- Real `FlowExecutor` orchestration logic (with mocked subprocess)

The subprocess mock should simulate:
1. Process start (returns immediately)
2. Streaming output (a few JSON lines: assistant message, result)
3. Process exit with code 0 (success)

For the pause test, the mock subprocess should have a longer simulated execution time so there is time to pause between tasks.

### Edge Cases
- The file watcher may not detect changes instantly — use `time.sleep(1)` between writing files and checking the API. If tests are flaky, increase the sleep or poll the API in a loop with a timeout.
- The WebSocket `receive_json` may time out if the run completes before the subscribe — use the replay mechanism to catch up.
- In-memory SQLite database (`:memory:`) works differently with multiple connections — ensure the DB is shared properly. If the state module uses connection pooling, `:memory:` needs special handling (all operations on the same connection).
- The test may need to handle both sync and async code paths — `TestClient` handles this by running the ASGI app synchronously.
- Background tasks from `asyncio.create_task` inside the lifespan may not complete before `TestClient` shuts down — ensure proper cleanup in the lifespan `yield` block.

## Testing Strategy

The file IS the test. Run with:

```bash
uv run pytest tests/server/test_integration.py -v
```

Expected test functions:
1. `test_end_to_end_flow_discovery_to_run_completion` — full happy path
2. `test_pause_and_resume` — run control
3. `test_file_watcher_detects_errors` — error handling in discovery
4. `test_websocket_reconnection_replay` — reconnection support
5. `test_list_runs_after_completion` — verify `GET /api/runs` includes the completed run

Mark the entire file with `@pytest.mark.integration` so it can be skipped in fast test runs:

```python
pytestmark = pytest.mark.integration
```

These tests are inherently slower than unit tests due to file I/O waits and background task coordination. Target: each test completes in under 10 seconds.
