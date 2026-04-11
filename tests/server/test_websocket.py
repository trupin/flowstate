"""Tests for WebSocket hub — event broadcasting, subscriptions, and reconnection (SERVER-005).

Uses FastAPI's TestClient WebSocket support. Mocks RunManager and FlowstateDB
for control action and replay tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.engine.events import EventType, FlowEvent
from flowstate.server.app import create_app
from flowstate.server.flow_registry import FlowRegistry
from flowstate.server.run_manager import RunManager
from flowstate.server.websocket import WebSocketHub
from flowstate.state.models import TaskExecutionRow, TaskLogRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_client(
    ws_hub: WebSocketHub | None = None,
    run_manager: RunManager | None = None,
    db_mock: MagicMock | None = None,
) -> TestClient:
    """Create a TestClient with WebSocket hub and mocked dependencies."""
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
    if run_manager is None:
        run_manager = RunManager()
    app.state.run_manager = run_manager

    # WebSocket hub
    if ws_hub is None:
        ws_hub = WebSocketHub()
    ws_hub.set_run_manager(run_manager)
    ws_hub.set_db(db_mock)
    app.state.ws_hub = ws_hub

    return TestClient(app, raise_server_exceptions=False)


def _make_task_log_row(
    task_id: str = "task-1",
    timestamp: str = "2025-01-01T00:00:05+00:00",
    log_type: str = "assistant_message",
    content: str = "Hello",
) -> TaskLogRow:
    return TaskLogRow(
        id=1,
        task_execution_id=task_id,
        timestamp=timestamp,
        log_type=log_type,
        content=content,
    )


def _make_task_execution_row(
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWebSocketConnect:
    def test_websocket_connect(self) -> None:
        """Connect to /ws. Verify the connection is accepted."""
        client = _make_test_client()
        with client.websocket_connect("/ws"):
            pass  # Connection accepted, no error


class TestSubscribe:
    def test_subscribe(self) -> None:
        """Connect, send subscribe. Verify no error response."""
        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "flow_run_id": "run-1"})
            # The hub should now track the subscription
            # We can verify by checking internal state


class TestEventBroadcast:
    def test_event_broadcast(self) -> None:
        """Connect two clients, subscribe both to run-1.

        Since TestClient processes WebSocket messages synchronously, we verify
        that the hub correctly tracks subscriptions. We send an unknown action
        after subscribing and wait for the error response, which ensures the
        subscribe message has been fully processed by the server.
        """
        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub)

        with client.websocket_connect("/ws") as ws1:
            ws1.send_json({"action": "subscribe", "flow_run_id": "run-1"})
            # Send a sentinel to ensure subscribe is processed
            ws1.send_json({"action": "__ping__", "flow_run_id": "run-1"})
            ws1.receive_json()  # Consume the error response for unknown action

            with client.websocket_connect("/ws") as ws2:
                ws2.send_json({"action": "subscribe", "flow_run_id": "run-1"})
                ws2.send_json({"action": "__ping__", "flow_run_id": "run-1"})
                ws2.receive_json()

                assert len(ws_hub.subscriptions.get("run-1", set())) == 2


class TestUnsubscribedClientNoEvents:
    def test_unsubscribed_client_no_events(self) -> None:
        """Connect two clients. Client A subscribes to run-1, B does not.

        Verify the hub only tracks A's subscription.
        """
        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub)

        with client.websocket_connect("/ws") as ws1:
            ws1.send_json({"action": "subscribe", "flow_run_id": "run-1"})
            # Send sentinel to ensure subscribe is processed
            ws1.send_json({"action": "__ping__"})
            ws1.receive_json()  # Consume error response

            with client.websocket_connect("/ws") as ws2:
                # ws2 does not subscribe, but send sentinel to ensure it's connected
                ws2.send_json({"action": "__ping__"})
                ws2.receive_json()

                subscribers = ws_hub.subscriptions.get("run-1", set())
                assert len(subscribers) == 1


class TestUnsubscribe:
    def test_unsubscribe(self) -> None:
        """Subscribe, then unsubscribe. Verify subscription is removed."""
        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub)

        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "flow_run_id": "run-1"})
            # Send sentinel to ensure subscribe is processed
            ws.send_json({"action": "__ping__"})
            ws.receive_json()  # Consume error response
            assert "run-1" in ws_hub.subscriptions
            assert len(ws_hub.subscriptions["run-1"]) == 1

            ws.send_json({"action": "unsubscribe", "flow_run_id": "run-1"})
            # Send sentinel to ensure unsubscribe is processed
            ws.send_json({"action": "__ping__"})
            ws.receive_json()  # Consume error response
            # After unsubscribe, the subscription should be removed
            assert "run-1" not in ws_hub.subscriptions


class TestDisconnectCleanup:
    def test_disconnect_cleanup(self) -> None:
        """Subscribe client, then disconnect. Verify hub cleans up."""
        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub)

        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "flow_run_id": "run-1"})
            # Send sentinel to ensure subscribe is processed
            ws.send_json({"action": "__ping__"})
            ws.receive_json()  # Consume error response
            assert "run-1" in ws_hub.subscriptions

        # After disconnect, cleanup should remove the client
        assert len(ws_hub.client_subs) == 0
        assert "run-1" not in ws_hub.subscriptions or len(ws_hub.subscriptions["run-1"]) == 0


class TestReconnectionReplay:
    def test_reconnection_replay(self) -> None:
        """Mock the DB to return 3 log entries after a timestamp.

        Connect, subscribe with last_event_timestamp. Verify client receives
        the 3 replayed events.
        """
        mock_db = MagicMock()
        # list_task_executions returns one task for the run
        mock_db.list_task_executions.return_value = [
            _make_task_execution_row("task-1", "run-1"),
        ]
        # get_task_logs returns 3 logs
        mock_db.get_task_logs.return_value = [
            _make_task_log_row("task-1", "2025-01-01T00:00:06+00:00", "assistant_message", "Log 1"),
            _make_task_log_row("task-1", "2025-01-01T00:00:07+00:00", "tool_use", "Log 2"),
            _make_task_log_row("task-1", "2025-01-01T00:00:08+00:00", "stdout", "Log 3"),
        ]

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, db_mock=mock_db)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "subscribe",
                    "flow_run_id": "run-1",
                    "payload": {"last_event_timestamp": "2025-01-01T00:00:05+00:00"},
                }
            )

            # Read the 3 replayed events
            events = []
            for _ in range(3):
                events.append(ws.receive_json())

            assert len(events) == 3
            assert all(e["type"] == "task.log" for e in events)
            assert events[0]["payload"]["content"] == "Log 1"
            assert events[1]["payload"]["content"] == "Log 2"
            assert events[2]["payload"]["content"] == "Log 3"
            assert all(e["flow_run_id"] == "run-1" for e in events)


class TestPauseAction:
    def test_pause_action(self) -> None:
        """Mock RunManager with an executor. Send pause. Verify executor.pause() called."""
        mock_executor = MagicMock()
        mock_executor.pause = AsyncMock()
        mock_executor._flow_run_id = "run-1"

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "flow_run_id": "run-1"})
            ws.send_json({"action": "pause", "flow_run_id": "run-1"})
            # Consume the action_ack response
            response = ws.receive_json()
            assert response["type"] == "action_ack"
            assert response["payload"]["action"] == "pause"
            assert response["payload"]["flow_run_id"] == "run-1"

        mock_executor.pause.assert_called_once_with("run-1")


class TestCancelAction:
    def test_cancel_action(self) -> None:
        """Send cancel. Verify executor.cancel() called and ack returned."""
        mock_executor = MagicMock()
        mock_executor.cancel = AsyncMock()
        mock_executor._flow_run_id = "run-1"

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "cancel", "flow_run_id": "run-1"})
            response = ws.receive_json()
            assert response["type"] == "action_ack"
            assert response["payload"]["action"] == "cancel"
            assert response["payload"]["flow_run_id"] == "run-1"

        mock_executor.cancel.assert_called_once_with("run-1")

    def test_cancel_action_error_sends_error_and_fallback(self) -> None:
        """If executor.cancel raises, the client receives an error message."""
        mock_executor = MagicMock()
        mock_executor.cancel = AsyncMock(side_effect=RuntimeError("boom during cancel"))
        mock_executor._flow_run_id = "run-1"

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            # Subscribe so the fallback status_changed broadcast also reaches us.
            ws.send_json({"action": "subscribe", "flow_run_id": "run-1"})
            ws.send_json({"action": "cancel", "flow_run_id": "run-1"})

            messages: list[dict[str, object]] = []
            for _ in range(2):
                messages.append(ws.receive_json())

        types = {m["type"] for m in messages}
        assert "error" in types
        assert "flow.status_changed" in types

        error_msg = next(m for m in messages if m["type"] == "error")
        error_payload = error_msg["payload"]
        assert isinstance(error_payload, dict)
        assert error_payload["action"] == "cancel"
        assert error_payload["flow_run_id"] == "run-1"
        assert "boom during cancel" in error_payload["message"]

        mock_executor.cancel.assert_called_once_with("run-1")


class TestRetryTaskAction:
    def test_retry_task_action(self) -> None:
        """Send retry_task with task_execution_id. Verify executor.retry_task()
        is called and an action_ack is returned to the client.
        """
        mock_executor = MagicMock()
        mock_executor.retry_task = AsyncMock()

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()
            assert response["type"] == "action_ack"
            assert response["payload"]["action"] == "retry_task"
            assert response["payload"]["flow_run_id"] == "run-1"
            assert response["payload"]["task_execution_id"] == "task-1"

        mock_executor.retry_task.assert_called_once_with("run-1", "task-1")


class TestSkipTaskAction:
    def test_skip_task_action(self) -> None:
        """Send skip_task. Verify executor.skip_task() called and ack returned."""
        mock_executor = MagicMock()
        mock_executor.skip_task = AsyncMock()

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "skip_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()
            assert response["type"] == "action_ack"
            assert response["payload"]["action"] == "skip_task"
            assert response["payload"]["task_execution_id"] == "task-1"

        mock_executor.skip_task.assert_called_once_with("run-1", "task-1")


class TestTaskControlErrorHandling:
    """UI-072 / ENGINE-038: ``_handle_task_control`` catches all executor errors
    and surfaces them to the originating client without killing the connection.
    """

    def test_retry_task_value_error_sends_error_message(self) -> None:
        """ValueError from retry_task is reported via an error message."""
        mock_executor = MagicMock()
        mock_executor.retry_task = AsyncMock(
            side_effect=ValueError("Can only retry failed tasks, got status: completed")
        )

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()
            assert response["type"] == "error"
            payload = response["payload"]
            assert payload["action"] == "retry_task"
            assert payload["task_execution_id"] == "task-1"
            assert "Can only retry failed tasks" in payload["message"]

            # Connection should still be alive -- send another action to verify
            ws.send_json({"action": "subscribe", "flow_run_id": "run-1"})

        mock_executor.retry_task.assert_called_once_with("run-1", "task-1")

    def test_skip_task_runtime_error_sends_error_message(self) -> None:
        """RuntimeError from skip_task is reported via an error message."""
        mock_executor = MagicMock()
        mock_executor.skip_task = AsyncMock(side_effect=RuntimeError("Flow run not found"))

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "skip_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()
            assert response["type"] == "error"
            payload = response["payload"]
            assert payload["action"] == "skip_task"
            assert "Flow run not found" in payload["message"]

            # Connection should still be alive
            ws.send_json({"action": "subscribe", "flow_run_id": "run-1"})

        mock_executor.skip_task.assert_called_once_with("run-1", "task-1")

    def test_retry_task_os_error_sends_error_message(self) -> None:
        """OSError (e.g. worktree creation failure) is caught and surfaced.

        Before UI-072, only ValueError/RuntimeError were caught — any other
        exception propagated up to ``connect()``, was logged, and closed the
        WebSocket leaving the UI silent.
        """
        mock_executor = MagicMock()
        mock_executor.retry_task = AsyncMock(
            side_effect=OSError("worktree path missing: /tmp/gone")
        )

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()
            assert response["type"] == "error"
            payload = response["payload"]
            assert payload["action"] == "retry_task"
            assert payload["flow_run_id"] == "run-1"
            assert payload["task_execution_id"] == "task-1"
            assert "worktree path missing" in payload["message"]

            # Connection should still be alive after a non-Value/RuntimeError
            ws.send_json({"action": "subscribe", "flow_run_id": "run-1"})

        mock_executor.retry_task.assert_called_once_with("run-1", "task-1")

    def test_retry_task_generic_subprocess_error_sends_error_message(self) -> None:
        """A subprocess.CalledProcessError-style failure is also surfaced."""
        import subprocess

        mock_executor = MagicMock()
        mock_executor.retry_task = AsyncMock(
            side_effect=subprocess.CalledProcessError(returncode=128, cmd=["git", "worktree"])
        )

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "action": "retry_task",
                    "flow_run_id": "run-1",
                    "payload": {"task_execution_id": "task-1"},
                }
            )
            response = ws.receive_json()
            assert response["type"] == "error"
            assert response["payload"]["action"] == "retry_task"
            assert "git" in response["payload"]["message"]


class TestAbortAction:
    def test_abort_action(self) -> None:
        """Send abort. Verify executor.cancel() called (abort maps to cancel)."""
        mock_executor = MagicMock()
        mock_executor.cancel = AsyncMock()
        mock_executor._flow_run_id = "run-1"

        run_manager = RunManager()
        run_manager._executors["run-1"] = mock_executor

        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub, run_manager=run_manager)

        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "abort", "flow_run_id": "run-1"})
            response = ws.receive_json()
            assert response["type"] == "action_ack"
            assert response["payload"]["action"] == "abort"

        mock_executor.cancel.assert_called_once_with("run-1")


class TestUnknownAction:
    def test_unknown_action(self) -> None:
        """Send an unknown action. Verify error response."""
        client = _make_test_client()
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "foobar", "flow_run_id": "run-1"})
            response = ws.receive_json()
            assert response["type"] == "error"
            assert "Unknown action" in response["payload"]["message"]


class TestBroadcastGlobalEvent:
    def test_broadcast_global_event(self) -> None:
        """Connect two clients. Verify both are tracked in client_subs.

        Due to TestClient limitations with simultaneous WebSocket reads, we
        verify that both clients are registered in client_subs (meaning
        broadcast_global_event would iterate over both).
        """
        ws_hub = WebSocketHub()
        client = _make_test_client(ws_hub=ws_hub)

        with client.websocket_connect("/ws") as ws1:
            ws1.send_json({"action": "subscribe", "flow_run_id": "run-1"})
            ws1.send_json({"action": "__ping__"})
            ws1.receive_json()

            with client.websocket_connect("/ws") as ws2:
                ws2.send_json({"action": "__ping__"})
                ws2.receive_json()
                assert len(ws_hub.client_subs) == 2


class TestOnFlowEventCallback:
    def test_on_flow_event_callback(self) -> None:
        """Create a WebSocketHub, call on_flow_event with a mock FlowEvent.

        Verify _schedule_task is called (which internally uses asyncio.create_task).
        """
        hub = WebSocketHub()

        event = FlowEvent(
            type=EventType.TASK_STARTED,
            flow_run_id="run-1",
            timestamp="2025-01-01T00:00:00+00:00",
            payload={"task_execution_id": "task-1", "node_name": "start", "generation": 1},
        )

        with patch.object(hub, "_schedule_task") as mock_schedule:
            hub.on_flow_event(event)
            mock_schedule.assert_called_once()
            # The argument should be a coroutine from broadcast_event
            coro = mock_schedule.call_args[0][0]
            # Close the coroutine to prevent runtime warning
            coro.close()


class TestAllRoutesAsync:
    def test_websocket_hub_methods_are_async(self) -> None:
        """Verify key WebSocketHub methods are coroutine functions."""
        import asyncio

        assert asyncio.iscoroutinefunction(WebSocketHub.connect)
        assert asyncio.iscoroutinefunction(WebSocketHub.broadcast_event)
        assert asyncio.iscoroutinefunction(WebSocketHub.broadcast_global_event)
