# [SERVER-005] WebSocket Hub (event broadcasting + reconnection)

## Domain
server

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SERVER-001, ENGINE-009
- Blocks: SERVER-006, SERVER-009

## Spec References
- specs.md Section 10.3 — "WebSocket Protocol" (full event/action table)
- agents/04-server.md — "WebSocket Hub" (architecture, reconnection, client actions)

## Summary
Implement the WebSocket hub that bridges the execution engine's event system to connected browser clients. The hub manages WebSocket connections, tracks which clients are subscribed to which flow runs, broadcasts engine events to the appropriate subscribers, handles client actions (subscribe, unsubscribe, pause, cancel, retry, skip, abort), and supports reconnection with event replay from the database. This is the real-time backbone of the Flowstate UI.

## Acceptance Criteria
- [ ] `src/flowstate/server/websocket.py` exists with `WebSocketHub` class
- [ ] WebSocket endpoint at `ws://localhost:<port>/ws` accepts connections
- [ ] **Connection lifecycle**: accept, handle messages in a loop, clean up on disconnect
- [ ] **Subscribe action**: client sends `{"action": "subscribe", "flow_run_id": "<id>"}` — hub tracks the subscription
- [ ] **Subscribe with replay**: client sends `{"action": "subscribe", "flow_run_id": "<id>", "payload": {"last_event_timestamp": "<iso8601>"}}` — hub replays all events after that timestamp from the DB, then switches to live streaming
- [ ] **Unsubscribe action**: client sends `{"action": "unsubscribe", "flow_run_id": "<id>"}` — hub removes the subscription
- [ ] **Control actions**: `pause`, `cancel`, `retry_task`, `skip_task`, `abort` — hub delegates to the `FlowExecutor` via `RunManager`
- [ ] **Event broadcasting**: when the engine emits a `FlowEvent`, the hub sends it to all clients subscribed to that `flow_run_id`
- [ ] **Event format** (server → client):
  ```json
  {
    "type": "<event_type>",
    "flow_run_id": "<uuid>",
    "timestamp": "<iso8601>",
    "payload": { ... }
  }
  ```
- [ ] **All event types from the spec are supported**: `flow.started`, `flow.status_changed`, `flow.completed`, `flow.budget_warning`, `task.started`, `task.log`, `task.completed`, `task.failed`, `edge.transition`, `fork.started`, `fork.joined`, `judge.started`, `judge.decided`, `task.waiting`, `task.wait_elapsed`, `schedule.triggered`, `schedule.skipped`
- [ ] Clients that disconnect are automatically cleaned up (no memory leak)
- [ ] Sending to a disconnected client does not crash the hub (graceful error handling)
- [ ] All tests pass: `uv run pytest tests/server/test_websocket.py`

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/websocket.py` — `WebSocketHub` class
- `src/flowstate/server/app.py` — modify lifespan to initialize hub, add WebSocket route
- `src/flowstate/server/routes.py` — add WebSocket endpoint (or put it in `websocket.py` and include)
- `tests/server/test_websocket.py` — all tests

### Key Implementation Details

#### WebSocketHub Class

```python
import asyncio
import json
import logging
from datetime import datetime

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketHub:
    """Manages WebSocket connections and broadcasts engine events to subscribers."""

    def __init__(self) -> None:
        # flow_run_id -> set of WebSocket connections
        self._subscriptions: dict[str, set[WebSocket]] = {}
        # WebSocket -> set of flow_run_ids (reverse index for cleanup)
        self._client_subs: dict[WebSocket, set[str]] = {}
        # References set by app lifespan
        self._run_manager: RunManager | None = None
        self._db: FlowstateDB | None = None

    def set_run_manager(self, run_manager: RunManager) -> None:
        self._run_manager = run_manager

    def set_db(self, db: FlowstateDB) -> None:
        self._db = db

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and handle messages until disconnect."""
        await websocket.accept()
        self._client_subs[websocket] = set()
        try:
            while True:
                data = await websocket.receive_json()
                await self._handle_message(websocket, data)
        except WebSocketDisconnect:
            self._cleanup(websocket)
        except Exception:
            logger.exception("WebSocket error")
            self._cleanup(websocket)

    async def broadcast_event(self, event: dict) -> None:
        """Send an event to all clients subscribed to the event's flow_run_id."""
        flow_run_id = event.get("flow_run_id")
        if not flow_run_id:
            return

        subscribers = self._subscriptions.get(flow_run_id, set())
        dead: list[WebSocket] = []

        for ws in subscribers:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)

        # Clean up dead connections
        for ws in dead:
            self._cleanup(ws)

    async def broadcast_global_event(self, event: dict) -> None:
        """Send an event to ALL connected clients (not scoped to a flow_run_id).
        Used for file watcher events (SERVER-006)."""
        all_clients = list(self._client_subs.keys())
        dead: list[WebSocket] = []
        for ws in all_clients:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._cleanup(ws)

    def on_flow_event(self, event: FlowEvent) -> None:
        """Callback passed to FlowExecutor. Bridges engine events to WebSocket.
        This runs in the async event loop, so we schedule the broadcast."""
        ws_event = _serialize_flow_event(event)
        asyncio.create_task(self.broadcast_event(ws_event))

    async def _handle_message(self, websocket: WebSocket, data: dict) -> None:
        """Route incoming client messages to the appropriate handler."""
        action = data.get("action")
        flow_run_id = data.get("flow_run_id")
        payload = data.get("payload", {})

        if action == "subscribe":
            await self._handle_subscribe(websocket, flow_run_id, payload)
        elif action == "unsubscribe":
            self._handle_unsubscribe(websocket, flow_run_id)
        elif action in ("pause", "cancel", "abort"):
            await self._handle_control(action, flow_run_id)
        elif action in ("retry_task", "skip_task"):
            task_id = payload.get("task_execution_id")
            await self._handle_task_control(action, flow_run_id, task_id)
        else:
            await websocket.send_json({
                "type": "error",
                "payload": {"message": f"Unknown action: {action}"},
            })

    async def _handle_subscribe(
        self, websocket: WebSocket, flow_run_id: str, payload: dict
    ) -> None:
        """Subscribe client to a flow run, with optional event replay."""
        if flow_run_id not in self._subscriptions:
            self._subscriptions[flow_run_id] = set()
        self._subscriptions[flow_run_id].add(websocket)
        self._client_subs[websocket].add(flow_run_id)

        # Replay missed events if last_event_timestamp is provided
        last_ts = payload.get("last_event_timestamp")
        if last_ts and self._db:
            await self._replay_events(websocket, flow_run_id, last_ts)

    def _handle_unsubscribe(self, websocket: WebSocket, flow_run_id: str) -> None:
        """Remove client subscription for a specific flow run."""
        if flow_run_id in self._subscriptions:
            self._subscriptions[flow_run_id].discard(websocket)
            if not self._subscriptions[flow_run_id]:
                del self._subscriptions[flow_run_id]
        if websocket in self._client_subs:
            self._client_subs[websocket].discard(flow_run_id)

    async def _handle_control(self, action: str, flow_run_id: str) -> None:
        """Delegate pause/cancel/abort to the FlowExecutor."""
        if not self._run_manager:
            return
        executor = self._run_manager.get_executor(flow_run_id)
        if not executor:
            return
        if action == "pause":
            await executor.pause()
        elif action == "cancel":
            await executor.cancel()
        elif action == "abort":
            await executor.abort()

    async def _handle_task_control(
        self, action: str, flow_run_id: str, task_id: str | None
    ) -> None:
        """Delegate retry_task/skip_task to the FlowExecutor."""
        if not self._run_manager or not task_id:
            return
        executor = self._run_manager.get_executor(flow_run_id)
        if not executor:
            return
        if action == "retry_task":
            await executor.retry_task(task_id)
        elif action == "skip_task":
            await executor.skip_task(task_id)

    async def _replay_events(
        self, websocket: WebSocket, flow_run_id: str, after_timestamp: str
    ) -> None:
        """Replay missed events from the database for reconnection support."""
        if not self._db:
            return
        # Query task_logs for all events after the timestamp
        logs = self._db.get_task_logs_for_run(flow_run_id, after_timestamp=after_timestamp)
        for log in logs:
            event = {
                "type": "task.log",
                "flow_run_id": flow_run_id,
                "timestamp": log.timestamp.isoformat(),
                "payload": {
                    "task_execution_id": log.task_execution_id,
                    "log_type": log.log_type,
                    "content": log.content,
                },
            }
            try:
                await websocket.send_json(event)
            except Exception:
                break  # Client disconnected during replay

    def _cleanup(self, websocket: WebSocket) -> None:
        """Remove a disconnected client from all subscriptions."""
        run_ids = self._client_subs.pop(websocket, set())
        for run_id in run_ids:
            if run_id in self._subscriptions:
                self._subscriptions[run_id].discard(websocket)
                if not self._subscriptions[run_id]:
                    del self._subscriptions[run_id]
```

#### FlowEvent Serialization

```python
def _serialize_flow_event(event: FlowEvent) -> dict:
    """Convert an engine FlowEvent to a WebSocket-ready JSON dict."""
    return {
        "type": event.event_type.value,  # e.g., "task.started"
        "flow_run_id": event.flow_run_id,
        "timestamp": event.timestamp.isoformat(),
        "payload": event.payload,  # already a dict from the engine
    }
```

#### WebSocket Route

Add to `app.py` or `routes.py`:

```python
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    hub: WebSocketHub = websocket.app.state.ws_hub
    await hub.connect(websocket)
```

#### Lifespan Integration

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    config = app.state.config
    # ... existing setup ...
    ws_hub = WebSocketHub()
    ws_hub.set_run_manager(app.state.run_manager)
    ws_hub.set_db(app.state.db)
    app.state.ws_hub = ws_hub
    yield
    # ... existing cleanup ...
```

### Edge Cases
- Client subscribes to a non-existent flow_run_id: allow it (the run may start later, or the client may be reconnecting to a completed run).
- Client sends malformed JSON: `receive_json()` raises an exception, which triggers cleanup. The hub should catch `json.JSONDecodeError` and send an error message instead of disconnecting.
- Client sends an action without `flow_run_id`: send an error response `{"type": "error", "payload": {"message": "flow_run_id is required"}}`.
- Multiple clients subscribe to the same run: all receive events. The hub uses a set per flow_run_id, so duplicate subscriptions from the same WebSocket are idempotent.
- Client subscribes to multiple runs simultaneously: supported via the reverse index (`_client_subs`).
- Event broadcast to 100+ clients: iterate and send sequentially. If this becomes a bottleneck, use `asyncio.gather` with exception handling. For MVP, sequential is fine.
- `on_flow_event` is called from the engine's async context: use `asyncio.create_task` to avoid blocking the engine's execution loop.
- Replay with a very old timestamp: could return thousands of events. The replay reads from the DB and sends them one by one. No pagination in replay — the client is expected to handle the burst.
- Executor not found for control action (run completed): silently ignore. The client will learn about the completion via the `flow.completed` event.

## Testing Strategy

Create `tests/server/test_websocket.py`. Use FastAPI's `TestClient` WebSocket support.

1. **test_websocket_connect** — Connect to `/ws`. Verify the connection is accepted (no error).

2. **test_subscribe** — Connect, send `{"action": "subscribe", "flow_run_id": "run-1"}`. Verify no error response.

3. **test_event_broadcast** — Connect two clients, subscribe both to "run-1". Call `hub.broadcast_event(...)` with a `flow.started` event. Verify both clients receive the event.

4. **test_unsubscribed_client_no_events** — Connect two clients. Client A subscribes to "run-1", client B does not. Broadcast event for "run-1". Verify only client A receives it.

5. **test_unsubscribe** — Subscribe, then send `{"action": "unsubscribe", "flow_run_id": "run-1"}`. Broadcast event. Verify client does NOT receive it.

6. **test_disconnect_cleanup** — Subscribe client to "run-1", then disconnect. Verify the hub's internal state has no references to the disconnected client.

7. **test_reconnection_replay** — Mock the DB to return 3 log entries after a timestamp. Connect, subscribe with `{"action": "subscribe", "flow_run_id": "run-1", "payload": {"last_event_timestamp": "2024-01-01T00:00:00Z"}}`. Verify the client receives the 3 replayed events before any live events.

8. **test_pause_action** — Mock `RunManager` with an executor. Connect, subscribe, send `{"action": "pause", "flow_run_id": "run-1"}`. Verify `executor.pause()` was called.

9. **test_cancel_action** — Same pattern for cancel.

10. **test_retry_task_action** — Send `{"action": "retry_task", "flow_run_id": "run-1", "payload": {"task_execution_id": "task-1"}}`. Verify `executor.retry_task("task-1")` was called.

11. **test_skip_task_action** — Same pattern for skip_task.

12. **test_abort_action** — Same pattern for abort.

13. **test_unknown_action** — Send `{"action": "foobar"}`. Verify error response.

14. **test_broadcast_global_event** — Connect two clients (one subscribed to "run-1", one with no subscriptions). Call `broadcast_global_event(...)`. Verify both clients receive the event.

15. **test_on_flow_event_callback** — Create a `WebSocketHub`, call `on_flow_event` with a mock `FlowEvent`. Verify `broadcast_event` is invoked (use a spy/mock).

For WebSocket tests, use `TestClient`'s `with client.websocket_connect("/ws") as ws:` pattern. For tests that need multiple simultaneous WebSocket connections, use multiple `websocket_connect` contexts.
