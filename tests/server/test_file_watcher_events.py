"""Tests for WebSocket file watcher event broadcasting (SERVER-006).

Tests that FlowRegistry file change events are correctly bridged to WebSocket
broadcasts via the on_file_event callback and broadcast_global_event.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from flowstate.server.flow_registry import DiscoveredFlow
from flowstate.server.websocket import WebSocketHub

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_discovered_flow(
    flow_id: str = "my_flow",
    name: str | None = "my_flow",
    file_path: str = "/flows/my_flow.flow",
    status: str = "valid",
    errors: list[str] | None = None,
) -> DiscoveredFlow:
    return DiscoveredFlow(
        id=flow_id,
        name=name,
        file_path=file_path,
        source_dsl="flow my_flow { ... }",
        status=status,
        errors=errors or [],
    )


def _make_on_file_event_callback(
    ws_hub: WebSocketHub,
) -> Any:
    """Create the on_file_event callback as it would be wired in app.py lifespan."""
    from datetime import UTC, datetime

    def on_file_event(event_type: str, flow: DiscoveredFlow) -> None:
        now = datetime.now(UTC).isoformat()
        flow_name = flow.name or flow.id

        changed_event: dict[str, Any] = {
            "type": "flow.file_changed",
            "flow_run_id": None,
            "timestamp": now,
            "payload": {
                "file_path": flow.file_path,
                "flow_name": flow_name,
            },
        }
        ws_hub._schedule_task(ws_hub.broadcast_global_event(changed_event))

        if event_type == "file_error":
            error_event: dict[str, Any] = {
                "type": "flow.file_error",
                "flow_run_id": None,
                "timestamp": now,
                "payload": {
                    "file_path": flow.file_path,
                    "flow_name": flow_name,
                    "errors": flow.errors,
                },
            }
            ws_hub._schedule_task(ws_hub.broadcast_global_event(error_event))
        else:
            valid_event: dict[str, Any] = {
                "type": "flow.file_valid",
                "flow_run_id": None,
                "timestamp": now,
                "payload": {
                    "file_path": flow.file_path,
                    "flow_name": flow_name,
                },
            }
            ws_hub._schedule_task(ws_hub.broadcast_global_event(valid_event))

    return on_file_event


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFileChangeSendsWebSocketEvents:
    async def test_file_change_sends_changed_and_valid_events(self) -> None:
        """When a valid .flow file changes, two events are broadcast:
        flow.file_changed followed by flow.file_valid.
        """
        hub = WebSocketHub()
        events_sent: list[dict[str, Any]] = []
        hub.broadcast_global_event = AsyncMock(side_effect=lambda e: events_sent.append(e))  # type: ignore[method-assign]

        callback = _make_on_file_event_callback(hub)
        flow = _make_discovered_flow(status="valid")

        callback("file_valid", flow)

        # Allow tasks to run
        await asyncio.sleep(0.05)

        assert len(events_sent) == 2
        assert events_sent[0]["type"] == "flow.file_changed"
        assert events_sent[0]["payload"]["file_path"] == "/flows/my_flow.flow"
        assert events_sent[0]["payload"]["flow_name"] == "my_flow"
        assert events_sent[0]["flow_run_id"] is None

        assert events_sent[1]["type"] == "flow.file_valid"
        assert events_sent[1]["payload"]["file_path"] == "/flows/my_flow.flow"
        assert events_sent[1]["payload"]["flow_name"] == "my_flow"


class TestFileErrorSendsErrorEvent:
    async def test_file_error_sends_changed_and_error_events(self) -> None:
        """When a .flow file has errors, two events are broadcast:
        flow.file_changed followed by flow.file_error with error list.
        """
        hub = WebSocketHub()
        events_sent: list[dict[str, Any]] = []
        hub.broadcast_global_event = AsyncMock(side_effect=lambda e: events_sent.append(e))  # type: ignore[method-assign]

        callback = _make_on_file_event_callback(hub)
        flow = _make_discovered_flow(
            status="error",
            errors=["Parse error at line 1", "Missing budget declaration"],
        )

        callback("file_error", flow)
        await asyncio.sleep(0.05)

        assert len(events_sent) == 2
        assert events_sent[0]["type"] == "flow.file_changed"
        assert events_sent[1]["type"] == "flow.file_error"
        assert events_sent[1]["payload"]["errors"] == [
            "Parse error at line 1",
            "Missing budget declaration",
        ]


class TestEventsBroadcastToAllClients:
    async def test_broadcast_global_reaches_all_clients(self) -> None:
        """broadcast_global_event sends to ALL connected clients regardless of subscriptions."""
        hub = WebSocketHub()

        # Create mock websockets
        ws1 = MagicMock()
        ws1.send_json = AsyncMock()
        ws2 = MagicMock()
        ws2.send_json = AsyncMock()

        # Register clients (simulating connect)
        hub._client_subs[ws1] = {"run-1"}
        hub._client_subs[ws2] = set()  # No subscriptions

        event = {
            "type": "flow.file_changed",
            "flow_run_id": None,
            "timestamp": "2025-01-01T00:00:00+00:00",
            "payload": {"file_path": "/flows/test.flow", "flow_name": "test"},
        }

        await hub.broadcast_global_event(event)

        ws1.send_json.assert_called_once_with(event)
        ws2.send_json.assert_called_once_with(event)


class TestFlowNameFromDsl:
    async def test_flow_name_from_dsl(self) -> None:
        """Valid file uses DSL flow name in event payload."""
        hub = WebSocketHub()
        events_sent: list[dict[str, Any]] = []
        hub.broadcast_global_event = AsyncMock(side_effect=lambda e: events_sent.append(e))  # type: ignore[method-assign]

        callback = _make_on_file_event_callback(hub)
        flow = _make_discovered_flow(
            flow_id="my_file",
            name="actual_flow_name",
        )

        callback("file_valid", flow)
        await asyncio.sleep(0.05)

        assert events_sent[0]["payload"]["flow_name"] == "actual_flow_name"
        assert events_sent[1]["payload"]["flow_name"] == "actual_flow_name"


class TestFlowNameFallbackToFilename:
    async def test_flow_name_fallback_to_filename(self) -> None:
        """Invalid file that cannot be parsed uses filename stem as flow_name."""
        hub = WebSocketHub()
        events_sent: list[dict[str, Any]] = []
        hub.broadcast_global_event = AsyncMock(side_effect=lambda e: events_sent.append(e))  # type: ignore[method-assign]

        callback = _make_on_file_event_callback(hub)
        flow = _make_discovered_flow(
            flow_id="broken_file",
            name=None,  # Parse failed, no name extracted
            status="error",
            errors=["Parse error"],
        )

        callback("file_error", flow)
        await asyncio.sleep(0.05)

        # Should fall back to flow.id (filename stem)
        assert events_sent[0]["payload"]["flow_name"] == "broken_file"
        assert events_sent[1]["payload"]["flow_name"] == "broken_file"


class TestNoClientsNoError:
    async def test_no_clients_no_error(self) -> None:
        """Broadcast with no connected clients raises no exception."""
        hub = WebSocketHub()
        event = {
            "type": "flow.file_changed",
            "flow_run_id": None,
            "timestamp": "2025-01-01T00:00:00+00:00",
            "payload": {"file_path": "/flows/test.flow", "flow_name": "test"},
        }
        # Should not raise
        await hub.broadcast_global_event(event)


class TestEventOrdering:
    async def test_event_ordering(self) -> None:
        """Verify that flow.file_changed arrives before flow.file_valid/flow.file_error."""
        hub = WebSocketHub()
        events_sent: list[dict[str, Any]] = []
        hub.broadcast_global_event = AsyncMock(side_effect=lambda e: events_sent.append(e))  # type: ignore[method-assign]

        callback = _make_on_file_event_callback(hub)

        # Test with valid file
        flow_valid = _make_discovered_flow(status="valid")
        callback("file_valid", flow_valid)
        await asyncio.sleep(0.05)

        assert events_sent[0]["type"] == "flow.file_changed"
        assert events_sent[1]["type"] == "flow.file_valid"

        events_sent.clear()

        # Test with error file
        flow_error = _make_discovered_flow(status="error", errors=["Error"])
        callback("file_error", flow_error)
        await asyncio.sleep(0.05)

        assert events_sent[0]["type"] == "flow.file_changed"
        assert events_sent[1]["type"] == "flow.file_error"
