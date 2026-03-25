"""WebSocket hub for real-time event broadcasting and client actions.

Manages WebSocket connections, tracks subscriptions per flow_run_id, broadcasts
engine events to appropriate subscribers, handles client actions (subscribe,
unsubscribe, pause, cancel, retry, skip, abort), and supports reconnection
with event replay from the database.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from starlette.websockets import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from flowstate.engine.events import FlowEvent
    from flowstate.server.run_manager import RunManager
    from flowstate.state.repository import FlowstateDB

logger = logging.getLogger(__name__)


def _serialize_flow_event(event: FlowEvent) -> dict[str, Any]:
    """Convert an engine FlowEvent to a WebSocket-ready JSON dict."""
    return event.to_dict()


class WebSocketHub:
    """Manages WebSocket connections and broadcasts engine events to subscribers.

    The hub maintains two indices:
    - _subscriptions: flow_run_id -> set of WebSocket connections
    - _client_subs: WebSocket -> set of flow_run_ids (reverse index for cleanup)

    This enables efficient broadcasting (O(subscribers) per event) and cleanup
    on disconnect (O(subscriptions) per client).
    """

    def __init__(self) -> None:
        # flow_run_id -> set of WebSocket connections
        self._subscriptions: dict[str, set[WebSocket]] = {}
        # WebSocket -> set of flow_run_ids (reverse index for cleanup)
        self._client_subs: dict[WebSocket, set[str]] = {}
        # References set by app lifespan
        self._run_manager: RunManager | None = None
        self._db: FlowstateDB | None = None
        # Background tasks (prevent garbage collection)
        self._background_tasks: set[asyncio.Task[None]] = set()

    def set_run_manager(self, run_manager: RunManager) -> None:
        """Set the RunManager reference for control action delegation."""
        self._run_manager = run_manager

    def set_db(self, db: FlowstateDB) -> None:
        """Set the FlowstateDB reference for event replay on reconnection."""
        self._db = db

    @property
    def subscriptions(self) -> dict[str, set[WebSocket]]:
        """Expose subscriptions for testing."""
        return self._subscriptions

    @property
    def client_subs(self) -> dict[WebSocket, set[str]]:
        """Expose client subscriptions for testing."""
        return self._client_subs

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and handle messages until disconnect."""
        await websocket.accept()
        self._client_subs[websocket] = set()
        try:
            while True:
                try:
                    data = await websocket.receive_json()
                except json.JSONDecodeError:
                    await self._send_safe(
                        websocket,
                        {
                            "type": "error",
                            "payload": {"message": "Invalid JSON"},
                        },
                    )
                    continue
                await self._handle_message(websocket, data)
        except WebSocketDisconnect:
            self._cleanup(websocket)
        except Exception:
            logger.exception("WebSocket error")
            self._cleanup(websocket)

    async def broadcast_event(self, event: dict[str, Any]) -> None:
        """Send an event to all clients subscribed to the event's flow_run_id."""
        flow_run_id = event.get("flow_run_id")
        if not flow_run_id:
            return

        subscribers = self._subscriptions.get(flow_run_id, set()).copy()
        dead: list[WebSocket] = []

        for ws in subscribers:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._cleanup(ws)

    async def broadcast_global_event(self, event: dict[str, Any]) -> None:
        """Send an event to ALL connected clients (not scoped to a flow_run_id).

        Used for file watcher events (SERVER-006) that affect the global flow library.
        """
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

        This runs in the async event loop, so we schedule the broadcast
        via asyncio.create_task to avoid blocking the engine.
        """
        ws_event = _serialize_flow_event(event)
        self._schedule_task(self.broadcast_event(ws_event))

    def _schedule_task(self, coro: Any) -> None:
        """Schedule a coroutine as a background task, preventing GC."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _handle_message(self, websocket: WebSocket, data: dict[str, Any]) -> None:
        """Route incoming client messages to the appropriate handler."""
        action = data.get("action")
        flow_run_id = data.get("flow_run_id")
        payload = data.get("payload", {})

        if action == "subscribe":
            if not flow_run_id:
                await self._send_safe(
                    websocket,
                    {
                        "type": "error",
                        "payload": {"message": "flow_run_id is required"},
                    },
                )
                return
            await self._handle_subscribe(websocket, flow_run_id, payload)
        elif action == "unsubscribe":
            if not flow_run_id:
                await self._send_safe(
                    websocket,
                    {
                        "type": "error",
                        "payload": {"message": "flow_run_id is required"},
                    },
                )
                return
            self._handle_unsubscribe(websocket, flow_run_id)
        elif action in ("pause", "cancel", "abort"):
            if not flow_run_id:
                await self._send_safe(
                    websocket,
                    {
                        "type": "error",
                        "payload": {"message": "flow_run_id is required"},
                    },
                )
                return
            await self._handle_control(action, flow_run_id)
        elif action in ("retry_task", "skip_task"):
            if not flow_run_id:
                await self._send_safe(
                    websocket,
                    {
                        "type": "error",
                        "payload": {"message": "flow_run_id is required"},
                    },
                )
                return
            task_id = payload.get("task_execution_id")
            await self._handle_task_control(action, flow_run_id, task_id)
        else:
            await self._send_safe(
                websocket,
                {
                    "type": "error",
                    "payload": {"message": f"Unknown action: {action}"},
                },
            )

    async def _handle_subscribe(
        self, websocket: WebSocket, flow_run_id: str, payload: dict[str, Any]
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
            await executor.pause(flow_run_id)
        elif action == "cancel":
            await executor.cancel(flow_run_id)
        elif action == "abort":
            # abort maps to cancel -- there is no separate abort method on FlowExecutor
            await executor.cancel(flow_run_id)

    async def _handle_task_control(
        self, action: str, flow_run_id: str, task_id: str | None
    ) -> None:
        """Delegate retry_task/skip_task to the FlowExecutor."""
        if not self._run_manager or not task_id:
            return
        executor = self._run_manager.get_executor(flow_run_id)
        if not executor:
            return
        try:
            if action == "retry_task":
                await executor.retry_task(flow_run_id, task_id)
            elif action == "skip_task":
                await executor.skip_task(flow_run_id, task_id)
        except (ValueError, RuntimeError) as e:
            logger.warning("Task control failed: %s", e)

    async def _replay_events(
        self, websocket: WebSocket, flow_run_id: str, after_timestamp: str
    ) -> None:
        """Replay missed events from the database for reconnection support.

        Queries task logs for the given flow run after the specified timestamp
        and sends them as task.log events to the reconnecting client.
        """
        if not self._db:
            return

        # Get all task executions for this run, then query logs for each
        tasks = self._db.list_task_executions(flow_run_id)
        for task in tasks:
            logs = self._db.get_task_logs(
                task_execution_id=task.id,
                after_timestamp=after_timestamp,
            )
            for log in logs:
                event = {
                    "type": "task.log",
                    "flow_run_id": flow_run_id,
                    "timestamp": log.timestamp,
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

    async def _send_safe(self, websocket: WebSocket, data: dict[str, Any]) -> None:
        """Send a message to a websocket, ignoring errors on disconnected clients."""
        try:
            await websocket.send_json(data)
        except Exception:
            self._cleanup(websocket)
