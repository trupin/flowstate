"""Tests for AcpHarness -- ACP client harness for generic agent communication.

Tests use mocks for the ACP SDK to avoid needing a real ACP agent subprocess.
Validates:
- Event mapping (ACP update -> StreamEvent)
- run_task yields correct events
- run_task_resume falls back to new session when load_session is unsupported
- run_judge parses JSON result from collected text
- kill dispatches cancel and terminates subprocess
- Error handling (agent crash -> exit event)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flowstate.engine.acp_client import (
    AcpHarness,
    _AcpBridgeClient,
    _extract_tool_call_content_text,
    _map_acp_update_to_stream_event,
    _serialize_raw_io,
)
from flowstate.engine.subprocess_mgr import (
    JudgeError,
    StreamEvent,
    StreamEventType,
)

# ---------------------------------------------------------------------------
# Helpers: Mock ACP types
# ---------------------------------------------------------------------------


def _make_text_content(text: str) -> MagicMock:
    """Create a mock TextContentBlock."""
    content = MagicMock()
    content.text = text
    content.type = "text"
    return content


def _make_agent_message_chunk(text: str) -> MagicMock:
    """Create a mock AgentMessageChunk with text content."""
    from acp.schema import AgentMessageChunk

    chunk = MagicMock(spec=AgentMessageChunk)
    chunk.content = _make_text_content(text)
    chunk.session_update = "agent_message_chunk"
    # Ensure isinstance checks work
    chunk.__class__ = AgentMessageChunk
    return chunk


def _make_agent_thought_chunk(text: str) -> MagicMock:
    """Create a mock AgentThoughtChunk with text content."""
    from acp.schema import AgentThoughtChunk

    chunk = MagicMock(spec=AgentThoughtChunk)
    chunk.content = _make_text_content(text)
    chunk.session_update = "agent_thought_chunk"
    chunk.__class__ = AgentThoughtChunk
    return chunk


def _make_tool_call_start(
    tool_call_id: str,
    title: str,
    *,
    kind: str | None = None,
    raw_input: object = None,
    content: list[Any] | None = None,
) -> MagicMock:
    """Create a mock ToolCallStart."""
    from acp.schema import ToolCallStart

    update = MagicMock(spec=ToolCallStart)
    update.tool_call_id = tool_call_id
    update.title = title
    update.status = "in_progress"
    update.session_update = "tool_call"
    update.kind = kind
    update.raw_input = raw_input
    update.raw_output = None
    update.content = content
    update.__class__ = ToolCallStart
    return update


def _make_tool_call_progress(
    tool_call_id: str,
    title: str | None = None,
    status: str = "completed",
    *,
    kind: str | None = None,
    raw_output: object = None,
    content: list[Any] | None = None,
) -> MagicMock:
    """Create a mock ToolCallProgress (tool_call_update)."""
    from acp.schema import ToolCallProgress

    update = MagicMock(spec=ToolCallProgress)
    update.tool_call_id = tool_call_id
    update.title = title
    update.status = status
    update.session_update = "tool_call_update"
    update.kind = kind
    update.raw_input = None
    update.raw_output = raw_output
    update.content = content
    update.__class__ = ToolCallProgress
    return update


def _make_plan_update(entries: list[dict[str, str]]) -> MagicMock:
    """Create a mock AgentPlanUpdate."""
    from acp.schema import AgentPlanUpdate

    update = MagicMock(spec=AgentPlanUpdate)
    mock_entries = []
    for entry in entries:
        e = MagicMock()
        e.title = entry["title"]
        e.status = entry["status"]
        mock_entries.append(e)
    update.entries = mock_entries
    update.session_update = "plan"
    update.__class__ = AgentPlanUpdate
    return update


def _make_prompt_response(stop_reason: str = "end_turn") -> MagicMock:
    """Create a mock PromptResponse."""
    resp = MagicMock()
    resp.stop_reason = stop_reason
    return resp


def _make_new_session_response(session_id: str = "acp-sess-1") -> MagicMock:
    """Create a mock NewSessionResponse."""
    resp = MagicMock()
    resp.session_id = session_id
    return resp


# ---------------------------------------------------------------------------
# Tests: Helper functions
# ---------------------------------------------------------------------------


class TestExtractToolCallContentText:
    """Test _extract_tool_call_content_text for various content types."""

    def test_none_returns_none(self) -> None:
        assert _extract_tool_call_content_text(None) is None

    def test_empty_list_returns_none(self) -> None:
        assert _extract_tool_call_content_text([]) is None

    def test_content_with_text(self) -> None:
        item = MagicMock()
        item.content = MagicMock()
        item.content.text = "Hello world"
        assert _extract_tool_call_content_text([item]) == "Hello world"

    def test_multiple_content_items_joined(self) -> None:
        item1 = MagicMock()
        item1.content = MagicMock()
        item1.content.text = "Line 1"
        item2 = MagicMock()
        item2.content = MagicMock()
        item2.content.text = "Line 2"
        assert _extract_tool_call_content_text([item1, item2]) == "Line 1\nLine 2"

    def test_diff_item_uses_new_text(self) -> None:
        item = MagicMock(spec=[])
        item.new_text = "new content"
        assert _extract_tool_call_content_text([item]) == "new content"

    def test_terminal_item(self) -> None:
        item = MagicMock(spec=[])
        item.terminal_id = "term-123"
        assert _extract_tool_call_content_text([item]) == "[terminal:term-123]"


class TestSerializeRawIo:
    """Test _serialize_raw_io for various input types."""

    def test_none_returns_none(self) -> None:
        assert _serialize_raw_io(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _serialize_raw_io("") is None

    def test_nonempty_string_returns_string(self) -> None:
        assert _serialize_raw_io("hello") == "hello"

    def test_dict_returns_json(self) -> None:
        result = _serialize_raw_io({"key": "value"})
        assert result == '{"key": "value"}'

    def test_empty_dict_returns_none(self) -> None:
        assert _serialize_raw_io({}) is None

    def test_list_returns_json(self) -> None:
        result = _serialize_raw_io([1, 2, 3])
        assert result == "[1, 2, 3]"


# ---------------------------------------------------------------------------
# Tests: Event mapping
# ---------------------------------------------------------------------------


class TestAcpEventMapping:
    """Test _map_acp_update_to_stream_event for each ACP update type."""

    def test_agent_message_chunk_maps_to_assistant(self) -> None:
        update = _make_agent_message_chunk("Hello world")
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert event.type == StreamEventType.ASSISTANT
        assert event.content["type"] == "assistant"
        assert event.content["message"]["content"][0]["text"] == "Hello world"

    def test_agent_thought_chunk_maps_to_assistant_with_thinking(self) -> None:
        update = _make_agent_thought_chunk("Let me think...")
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert event.type == StreamEventType.ASSISTANT
        assert event.content["thinking"] is True
        assert event.content["message"]["content"][0]["text"] == "Let me think..."

    def test_tool_call_start_maps_to_tool_use(self) -> None:
        update = _make_tool_call_start("tc-1", "Reading file.py")
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert event.type == StreamEventType.TOOL_USE
        assert event.content["tool_call_id"] == "tc-1"
        assert event.content["title"] == "Reading file.py"
        assert event.content["status"] == "in_progress"

    def test_tool_call_progress_maps_to_tool_result(self) -> None:
        update = _make_tool_call_progress("tc-1", "Read file.py", "completed")
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert event.type == StreamEventType.TOOL_RESULT
        assert event.content["tool_call_id"] == "tc-1"
        assert event.content["status"] == "completed"

    def test_plan_update_maps_to_system(self) -> None:
        update = _make_plan_update(
            [
                {"title": "Parse code", "status": "completed"},
                {"title": "Write tests", "status": "in_progress"},
            ]
        )
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert event.type == StreamEventType.SYSTEM
        assert event.content["type"] == "plan"
        assert len(event.content["entries"]) == 2
        assert event.content["entries"][0]["title"] == "Parse code"
        assert event.content["entries"][1]["status"] == "in_progress"

    def test_unknown_update_returns_none(self) -> None:
        """Unknown update types return None (logged and skipped)."""
        unknown = MagicMock()
        unknown.__class__ = type("SomeNewUpdate", (), {})
        event = _map_acp_update_to_stream_event(unknown)
        assert event is None

    def test_raw_field_is_valid_json(self) -> None:
        """All mapped events have parseable JSON in the raw field."""
        update = _make_agent_message_chunk("test")
        event = _map_acp_update_to_stream_event(update)
        assert event is not None
        parsed = json.loads(event.raw)
        assert parsed["type"] == "assistant"

    def test_plan_with_empty_entries(self) -> None:
        update = _make_plan_update([])
        event = _map_acp_update_to_stream_event(update)
        assert event is not None
        assert event.content["entries"] == []

    def test_tool_call_start_includes_kind_and_raw_input(self) -> None:
        """ToolCallStart includes kind and raw_input when present."""
        update = _make_tool_call_start(
            "tc-2",
            "Bash: ls -la",
            kind="execute",
            raw_input={"command": "ls -la"},
        )
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert event.type == StreamEventType.TOOL_USE
        assert event.content["kind"] == "execute"
        assert event.content["raw_input"] == '{"command": "ls -la"}'

    def test_tool_call_start_omits_none_optional_fields(self) -> None:
        """ToolCallStart without kind/raw_input does not include those keys."""
        update = _make_tool_call_start("tc-3", "Read file")
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert "kind" not in event.content
        assert "raw_input" not in event.content
        assert "content" not in event.content

    def test_tool_call_start_with_content(self) -> None:
        """ToolCallStart includes text content when content blocks are present."""
        content_item = MagicMock()
        content_item.content = MagicMock()
        content_item.content.text = "file contents here"
        update = _make_tool_call_start("tc-4", "Read foo.py", content=[content_item])
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert event.content["content"] == "file contents here"

    def test_tool_call_progress_includes_content_text(self) -> None:
        """ToolCallProgress includes extracted content text when present."""
        content_item = MagicMock()
        content_item.content = MagicMock()
        content_item.content.text = "total 42\ndrwxr-xr-x 5 user staff 160 Jan 1 00:00 ."
        update = _make_tool_call_progress(
            "tc-1",
            "Bash: ls -la",
            "completed",
            content=[content_item],
        )
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert event.type == StreamEventType.TOOL_RESULT
        assert "total 42" in event.content["content"]

    def test_tool_call_progress_includes_raw_output(self) -> None:
        """ToolCallProgress includes raw_output when present."""
        update = _make_tool_call_progress(
            "tc-5",
            "Bash: echo hello",
            "completed",
            raw_output="hello\n",
        )
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert event.content["raw_output"] == "hello\n"

    def test_tool_call_progress_includes_kind(self) -> None:
        """ToolCallProgress includes kind when present."""
        update = _make_tool_call_progress(
            "tc-6",
            "Read main.py",
            "completed",
            kind="read",
        )
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert event.content["kind"] == "read"

    def test_tool_call_progress_omits_none_optional_fields(self) -> None:
        """ToolCallProgress without optional fields does not include them."""
        update = _make_tool_call_progress("tc-7", "Edit file", "completed")
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert "kind" not in event.content
        assert "raw_output" not in event.content
        assert "content" not in event.content

    def test_tool_call_progress_with_diff_content(self) -> None:
        """ToolCallProgress with FileEditToolCallContent extracts new_text."""
        diff_item = MagicMock()
        # Simulate a diff item that has new_text but NOT content.text
        diff_item.content = None  # No nested text content
        del diff_item.content  # Remove .content entirely
        diff_item.new_text = "def hello():\n    return 'world'\n"
        update = _make_tool_call_progress(
            "tc-8",
            "Edit main.py",
            "completed",
            content=[diff_item],
        )
        event = _map_acp_update_to_stream_event(update)

        assert event is not None
        assert "def hello():" in event.content["content"]


# ---------------------------------------------------------------------------
# Tests: _AcpBridgeClient
# ---------------------------------------------------------------------------


class TestAcpBridgeClient:
    """Test the callback-to-queue bridge."""

    @pytest.mark.asyncio
    async def test_session_update_enqueues_events(self) -> None:
        """session_update maps update to StreamEvent and enqueues it."""
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        bridge = _AcpBridgeClient(queue)

        update = _make_agent_message_chunk("Hello")
        await bridge.session_update("sess-1", update)

        assert not queue.empty()
        event = queue.get_nowait()
        assert event is not None
        assert event.type == StreamEventType.ASSISTANT

    @pytest.mark.asyncio
    async def test_session_update_skips_unmapped(self) -> None:
        """session_update with unknown types does not enqueue."""
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        bridge = _AcpBridgeClient(queue)

        unknown = MagicMock()
        unknown.__class__ = type("FutureUpdate", (), {})
        await bridge.session_update("sess-1", unknown)

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_request_permission_auto_approves(self) -> None:
        """request_permission returns a selected outcome with the first allow option."""
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        bridge = _AcpBridgeClient(queue)

        allow_opt = MagicMock()
        allow_opt.option_id = "opt-allow"
        allow_opt.kind = "allow_once"

        reject_opt = MagicMock()
        reject_opt.option_id = "opt-reject"
        reject_opt.kind = "reject_once"

        result = await bridge.request_permission(
            options=[reject_opt, allow_opt],
            session_id="sess-1",
            tool_call=MagicMock(),
        )

        assert result.outcome.option_id == "opt-allow"
        assert result.outcome.outcome == "selected"

    @pytest.mark.asyncio
    async def test_request_permission_uses_first_if_no_allow(self) -> None:
        """request_permission uses first option if no allow option exists."""
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        bridge = _AcpBridgeClient(queue)

        opt = MagicMock()
        opt.option_id = "opt-1"
        opt.kind = "reject_once"

        result = await bridge.request_permission(
            options=[opt],
            session_id="sess-1",
            tool_call=MagicMock(),
        )

        assert result.outcome.option_id == "opt-1"

    @pytest.mark.asyncio
    async def test_on_connect_stores_conn(self) -> None:
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        bridge = _AcpBridgeClient(queue)
        mock_conn = MagicMock()
        bridge.on_connect(mock_conn)
        assert bridge._conn is mock_conn


# ---------------------------------------------------------------------------
# Tests: AcpHarness.run_task
# ---------------------------------------------------------------------------


def _make_mock_spawn_context(
    session_id: str = "acp-sess-1",
    stop_reason: str = "end_turn",
    updates: list[Any] | None = None,
) -> Any:
    """Create a mock spawn_agent_process context manager.

    The mock captures the bridge client and fires session_update callbacks
    for each update before the prompt() call returns.
    """
    conn = AsyncMock()
    conn.initialize = AsyncMock()
    conn.new_session = AsyncMock(return_value=_make_new_session_response(session_id))
    conn.load_session = AsyncMock(return_value=MagicMock(session_id=session_id))

    process = MagicMock()
    process.terminate = MagicMock()
    process.kill = MagicMock()
    process.wait = AsyncMock()
    process.returncode = None  # Process is alive (not yet exited)

    # The prompt() method will trigger session_update callbacks on the bridge
    # before returning.
    async def prompt_side_effect(prompt: Any, session_id: str, **kwargs: Any) -> MagicMock:
        # The bridge is the first arg passed to spawn_agent_process
        bridge = _captured_bridge[0]
        if updates:
            for update in updates:
                await bridge.session_update(session_id, update)
        return _make_prompt_response(stop_reason)

    conn.prompt = AsyncMock(side_effect=prompt_side_effect)
    conn.cancel = AsyncMock()

    # We need to capture the bridge client from spawn_agent_process call
    _captured_bridge: list[Any] = [None]

    class _MockContextManager:
        def __init__(self, to_client: Any, *args: Any, **kwargs: Any) -> None:
            if callable(to_client):
                _captured_bridge[0] = to_client(conn)
            else:
                _captured_bridge[0] = to_client

        async def __aenter__(self) -> tuple[Any, Any]:
            return (conn, process)

        async def __aexit__(self, *args: Any) -> None:
            pass

    return _MockContextManager, conn, process


class TestAcpHarnessRunTask:
    """Test AcpHarness.run_task with mocked ACP subprocess."""

    @pytest.mark.asyncio
    async def test_yields_assistant_and_exit_events(self) -> None:
        """run_task yields ASSISTANT events from agent and RESULT + SYSTEM exit."""
        updates = [_make_agent_message_chunk("Hello from agent")]
        mock_ctx, _conn, _process = _make_mock_spawn_context(updates=updates)

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task("Do something", "/tmp/work", "sess-1"):
                events.append(event)

        # Should have: ASSISTANT, RESULT, SYSTEM(exit)
        assert len(events) == 3
        assert events[0].type == StreamEventType.ASSISTANT
        assert events[0].content["message"]["content"][0]["text"] == "Hello from agent"
        assert events[1].type == StreamEventType.RESULT
        assert events[1].content["stop_reason"] == "end_turn"
        assert events[2].type == StreamEventType.SYSTEM
        assert events[2].content["event"] == "process_exit"
        assert events[2].content["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_multiple_event_types(self) -> None:
        """run_task yields tool use and tool result events."""
        updates = [
            _make_agent_message_chunk("Let me check"),
            _make_tool_call_start("tc-1", "Bash: ls"),
            _make_tool_call_progress("tc-1", "Bash: ls", "completed"),
        ]
        mock_ctx, _conn, _process = _make_mock_spawn_context(updates=updates)

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task("List files", "/tmp", "sess-2"):
                events.append(event)

        # ASSISTANT, TOOL_USE, TOOL_RESULT, RESULT, SYSTEM(exit)
        assert len(events) == 5
        assert events[0].type == StreamEventType.ASSISTANT
        assert events[1].type == StreamEventType.TOOL_USE
        assert events[2].type == StreamEventType.TOOL_RESULT
        assert events[3].type == StreamEventType.RESULT
        assert events[4].type == StreamEventType.SYSTEM

    @pytest.mark.asyncio
    async def test_cancelled_stop_reason(self) -> None:
        """Agent returning 'cancelled' stop reason emits SYSTEM exit with -1."""
        mock_ctx, _conn, _process = _make_mock_spawn_context(stop_reason="cancelled")

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task("Do thing", "/tmp", "sess-3"):
                events.append(event)

        # Should have just the SYSTEM(exit) with exit_code=-1
        assert len(events) == 1
        assert events[0].type == StreamEventType.SYSTEM
        assert events[0].content["exit_code"] == -1

    @pytest.mark.asyncio
    async def test_plan_events(self) -> None:
        """Plan updates are yielded as SYSTEM events."""
        updates = [
            _make_plan_update(
                [
                    {"title": "Step 1", "status": "completed"},
                    {"title": "Step 2", "status": "in_progress"},
                ]
            )
        ]
        mock_ctx, _conn, _process = _make_mock_spawn_context(updates=updates)

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task("Plan", "/tmp", "sess-4"):
                events.append(event)

        # SYSTEM(plan), RESULT, SYSTEM(exit)
        assert events[0].type == StreamEventType.SYSTEM
        assert events[0].content["type"] == "plan"

    @pytest.mark.asyncio
    async def test_calls_initialize_and_new_session(self) -> None:
        """run_task calls initialize() and new_session() on the connection."""
        mock_ctx, conn, _process = _make_mock_spawn_context()

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            async for _ in harness.run_task("Test", "/workspace", "sess-5"):
                pass

        conn.initialize.assert_called_once()
        conn.new_session.assert_called_once_with(cwd="/workspace")

    @pytest.mark.asyncio
    async def test_no_events_still_yields_exit(self) -> None:
        """run_task with no updates still yields RESULT + SYSTEM exit."""
        mock_ctx, _conn, _process = _make_mock_spawn_context(updates=[])

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task("Quiet", "/tmp", "sess-6"):
                events.append(event)

        assert len(events) == 2
        assert events[0].type == StreamEventType.RESULT
        assert events[1].type == StreamEventType.SYSTEM


# ---------------------------------------------------------------------------
# Tests: AcpHarness.run_task_resume
# ---------------------------------------------------------------------------


class TestAcpHarnessRunTaskResume:
    """Test AcpHarness.run_task_resume with mocked ACP subprocess."""

    @pytest.mark.asyncio
    async def test_resume_calls_load_session(self) -> None:
        """run_task_resume calls load_session() instead of new_session()."""
        mock_ctx, conn, _process = _make_mock_spawn_context()

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            async for _ in harness.run_task_resume("Continue", "/workspace", "sess-7"):
                pass

        conn.load_session.assert_called_once_with(cwd="/workspace", session_id="sess-7")
        conn.new_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_falls_back_on_method_not_found(self) -> None:
        """When load_session raises method_not_found, falls back to new_session."""
        from acp import RequestError

        mock_ctx, conn, _process = _make_mock_spawn_context()
        conn.load_session = AsyncMock(
            side_effect=RequestError(code=-32601, message="Method not found")
        )

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task_resume("Resume", "/workspace", "sess-8"):
                events.append(event)

        conn.new_session.assert_called_once_with(cwd="/workspace")
        # Should still complete successfully
        assert any(e.type == StreamEventType.RESULT for e in events)

    @pytest.mark.asyncio
    async def test_resume_falls_back_on_none_response(self) -> None:
        """When load_session returns None, falls back to new_session."""
        mock_ctx, conn, _process = _make_mock_spawn_context()
        conn.load_session = AsyncMock(return_value=None)

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task_resume("Resume", "/workspace", "sess-9"):
                events.append(event)

        conn.new_session.assert_called_once_with(cwd="/workspace")
        assert any(e.type == StreamEventType.RESULT for e in events)


# ---------------------------------------------------------------------------
# Tests: AcpHarness.run_judge
# ---------------------------------------------------------------------------


class TestAcpHarnessRunJudge:
    """Test AcpHarness.run_judge with mocked ACP subprocess."""

    @pytest.mark.asyncio
    async def test_parses_json_from_assistant_text(self) -> None:
        """run_judge collects assistant text and parses as JSON JudgeResult."""
        judge_json = json.dumps(
            {
                "decision": "approve",
                "reasoning": "Code looks good",
                "confidence": 0.95,
            }
        )
        updates = [_make_agent_message_chunk(judge_json)]
        mock_ctx, _conn, _process = _make_mock_spawn_context(updates=updates)

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            result = await harness.run_judge("Evaluate this", "/workspace")

        assert result.decision == "approve"
        assert result.reasoning == "Code looks good"
        assert result.confidence == 0.95
        assert result.raw_output == judge_json

    @pytest.mark.asyncio
    async def test_raises_judge_error_on_invalid_json(self) -> None:
        """run_judge raises JudgeError when agent output is not valid JSON."""
        updates = [_make_agent_message_chunk("This is not JSON")]
        mock_ctx, _conn, _process = _make_mock_spawn_context(updates=updates)

        harness = AcpHarness(command=["test-agent"])

        with (
            patch("acp.spawn_agent_process", mock_ctx),
            pytest.raises(JudgeError, match="Failed to parse judge output"),
        ):
            await harness.run_judge("Evaluate", "/workspace")

    @pytest.mark.asyncio
    async def test_raises_judge_error_on_missing_keys(self) -> None:
        """run_judge raises JudgeError when JSON is missing required keys."""
        updates = [_make_agent_message_chunk('{"partial": true}')]
        mock_ctx, _conn, _process = _make_mock_spawn_context(updates=updates)

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx), pytest.raises(JudgeError):
            await harness.run_judge("Evaluate", "/workspace")

    @pytest.mark.asyncio
    async def test_concatenates_multiple_assistant_chunks(self) -> None:
        """run_judge concatenates text from multiple assistant messages."""
        updates = [
            _make_agent_message_chunk('{"decision": "approve",'),
            _make_agent_message_chunk(' "reasoning": "ok",'),
            _make_agent_message_chunk(' "confidence": 0.8}'),
        ]
        mock_ctx, _conn, _process = _make_mock_spawn_context(updates=updates)

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            result = await harness.run_judge("Multi-chunk", "/workspace")

        assert result.decision == "approve"
        assert result.confidence == 0.8


# ---------------------------------------------------------------------------
# Tests: AcpHarness.kill
# ---------------------------------------------------------------------------


class TestAcpHarnessKill:
    """Test AcpHarness.kill."""

    @pytest.mark.asyncio
    async def test_kill_cancels_and_terminates(self) -> None:
        """kill() sends cancel and terminates the subprocess."""
        mock_ctx, _conn, _process = _make_mock_spawn_context()

        harness = AcpHarness(command=["test-agent"])

        # We need to start a session first so there's something to kill.
        # Start run_task but capture the internal session before completing.
        with patch("acp.spawn_agent_process", mock_ctx):
            async for _ in harness.run_task("Start", "/tmp", "kill-sess"):
                pass

        # After run_task completes, session is cleaned up.
        # Kill on a non-existent session should be a no-op.
        await harness.kill("kill-sess")

    @pytest.mark.asyncio
    async def test_kill_nonexistent_session_is_noop(self) -> None:
        """kill() with unknown session_id does nothing."""
        harness = AcpHarness(command=["test-agent"])
        # Should not raise
        await harness.kill("nonexistent-session")


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


class TestAcpHarnessErrorHandling:
    """Test error handling for agent crashes and connection failures."""

    @pytest.mark.asyncio
    async def test_spawn_failure_yields_error_and_exit(self) -> None:
        """If spawn_agent_process raises, yields ERROR + SYSTEM exit events."""

        class _FailingContext:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> None:
                raise ConnectionError("Agent failed to start")

            async def __aexit__(self, *args: Any) -> None:
                pass

        harness = AcpHarness(command=["broken-agent"])

        with patch("acp.spawn_agent_process", _FailingContext):
            events = []
            async for event in harness.run_task("Test", "/tmp", "err-sess"):
                events.append(event)

        assert len(events) == 2
        assert events[0].type == StreamEventType.ERROR
        assert "Agent failed to start" in events[0].content["error"]["message"]
        assert events[1].type == StreamEventType.SYSTEM
        assert events[1].content["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_prompt_failure_yields_error_events(self) -> None:
        """If prompt() raises, yields ERROR + SYSTEM exit events."""
        mock_ctx, conn, _process = _make_mock_spawn_context()
        conn.prompt = AsyncMock(side_effect=RuntimeError("Prompt failed"))

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task("Fail", "/tmp", "err-sess-2"):
                events.append(event)

        assert any(e.type == StreamEventType.ERROR for e in events)
        assert any(
            e.type == StreamEventType.SYSTEM and e.content.get("exit_code") == 1 for e in events
        )

    @pytest.mark.asyncio
    async def test_initialize_failure_yields_error(self) -> None:
        """If initialize() raises, error events are emitted."""
        mock_ctx, conn, _process = _make_mock_spawn_context()
        conn.initialize = AsyncMock(side_effect=RuntimeError("Init failed"))

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task("Fail", "/tmp", "err-sess-3"):
                events.append(event)

        assert any(e.type == StreamEventType.ERROR for e in events)


# ---------------------------------------------------------------------------
# Tests: HarnessManager lazy instantiation
# ---------------------------------------------------------------------------


class TestHarnessManagerAcpIntegration:
    """Test that HarnessManager lazily creates AcpHarness from configs."""

    def test_get_creates_acp_harness_from_config(self) -> None:
        """get() with a matching config lazily creates an AcpHarness."""
        from flowstate.engine.harness import HarnessConfig, HarnessManager

        fake_default = MagicMock()
        configs = {
            "gemini": HarnessConfig(command=["gemini-cli"], env={"KEY": "val"}),
        }
        mgr = HarnessManager(default_harness=fake_default, configs=configs)

        harness = mgr.get("gemini")
        assert isinstance(harness, AcpHarness)
        assert harness._command == ["gemini-cli"]
        assert harness._env == {"KEY": "val"}

    def test_get_caches_lazily_created_harness(self) -> None:
        """get() returns the same AcpHarness on subsequent calls (cached)."""
        from flowstate.engine.harness import HarnessConfig, HarnessManager

        fake_default = MagicMock()
        configs = {
            "custom": HarnessConfig(command=["custom-agent"]),
        }
        mgr = HarnessManager(default_harness=fake_default, configs=configs)

        first = mgr.get("custom")
        second = mgr.get("custom")
        assert first is second

    def test_get_still_raises_for_unknown(self) -> None:
        """get() raises HarnessNotFoundError for names not in registry or configs."""
        from flowstate.engine.harness import HarnessConfig, HarnessManager, HarnessNotFoundError

        fake_default = MagicMock()
        configs = {
            "gemini": HarnessConfig(command=["gemini-cli"]),
        }
        mgr = HarnessManager(default_harness=fake_default, configs=configs)

        with pytest.raises(HarnessNotFoundError):
            mgr.get("nonexistent")

    def test_get_default_still_works(self) -> None:
        """get('claude') returns the default harness, not an AcpHarness."""
        from flowstate.engine.harness import HarnessConfig, HarnessManager

        fake_default = MagicMock()
        configs = {
            "gemini": HarnessConfig(command=["gemini-cli"]),
        }
        mgr = HarnessManager(default_harness=fake_default, configs=configs)

        assert mgr.get("claude") is fake_default


# ---------------------------------------------------------------------------
# Tests: AcpHarness protocol satisfaction
# ---------------------------------------------------------------------------


class TestAcpHarnessProtocolSatisfaction:
    """Verify AcpHarness has all methods required by the Harness Protocol."""

    def test_has_all_protocol_methods(self) -> None:
        harness = AcpHarness(command=["test"])
        assert callable(getattr(harness, "run_task", None))
        assert callable(getattr(harness, "run_task_resume", None))
        assert callable(getattr(harness, "run_judge", None))
        assert callable(getattr(harness, "kill", None))
        assert callable(getattr(harness, "start_session", None))
        assert callable(getattr(harness, "prompt", None))
        assert callable(getattr(harness, "interrupt", None))

    def test_harness_manager_accepts_acp_harness(self) -> None:
        """HarnessManager accepts AcpHarness via register."""
        from flowstate.engine.harness import HarnessManager

        harness = AcpHarness(command=["test"])
        mgr = HarnessManager(default_harness=MagicMock())
        mgr.register("custom", harness)
        assert mgr.get("custom") is harness


# ---------------------------------------------------------------------------
# Tests: AcpHarness long-lived session API
# ---------------------------------------------------------------------------


def _make_mock_spawn_for_session(
    session_id: str = "acp-sess-1",
    stop_reason: str = "end_turn",
    updates: list[Any] | None = None,
) -> tuple[Any, Any, Any, list[Any]]:
    """Create mocks for the long-lived session API tests.

    Returns (mock_spawn_ctx_class, conn, process, captured_bridge).
    The mock spawn context is designed to be used with manual __aenter__/__aexit__.
    """
    conn = AsyncMock()
    conn.initialize = AsyncMock()
    conn.new_session = AsyncMock(return_value=_make_new_session_response(session_id))
    conn.load_session = AsyncMock(return_value=MagicMock(session_id=session_id))
    conn.cancel = AsyncMock()

    process = MagicMock()
    process.terminate = MagicMock()
    process.kill = MagicMock()
    process.wait = AsyncMock()
    process.returncode = None  # Process is alive

    # The prompt() method will trigger session_update callbacks on the bridge
    _captured_bridge: list[Any] = [None]

    async def prompt_side_effect(prompt: Any, session_id: str, **kwargs: Any) -> MagicMock:
        bridge = _captured_bridge[0]
        if updates:
            for update in updates:
                await bridge.session_update(session_id, update)
        return _make_prompt_response(stop_reason)

    conn.prompt = AsyncMock(side_effect=prompt_side_effect)

    class _MockContextManager:
        def __init__(self, to_client: Any, *args: Any, **kwargs: Any) -> None:
            if callable(to_client):
                _captured_bridge[0] = to_client(conn)
            else:
                _captured_bridge[0] = to_client

        async def __aenter__(self) -> tuple[Any, Any]:
            return (conn, process)

        async def __aexit__(self, *args: Any) -> None:
            pass

    return _MockContextManager, conn, process, _captured_bridge


class TestAcpHarnessStartSession:
    """Test AcpHarness.start_session for long-lived session creation."""

    @pytest.mark.asyncio
    async def test_start_session_creates_session(self) -> None:
        """start_session spawns subprocess and initializes ACP."""
        mock_ctx, conn, _process, _bridge = _make_mock_spawn_for_session()

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            await harness.start_session("/workspace", "sess-1")

        conn.initialize.assert_called_once()
        conn.new_session.assert_called_once_with(cwd="/workspace")
        assert "sess-1" in harness._sessions

    @pytest.mark.asyncio
    async def test_start_session_kills_existing(self) -> None:
        """start_session kills existing session with same ID before creating new one."""
        mock_ctx, _conn, process, _bridge = _make_mock_spawn_for_session()
        process.returncode = None

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            # Start first session
            await harness.start_session("/workspace", "sess-dup")
            first_session = harness._sessions.get("sess-dup")
            assert first_session is not None

            # Start another with same ID -- should kill the first
            await harness.start_session("/workspace", "sess-dup")
            # A new session should exist
            assert "sess-dup" in harness._sessions


class TestAcpHarnessPrompt:
    """Test AcpHarness.prompt for sending messages to existing sessions."""

    @pytest.mark.asyncio
    async def test_prompt_yields_events(self) -> None:
        """prompt() sends message and yields ASSISTANT + RESULT + SYSTEM events."""
        updates = [_make_agent_message_chunk("Response text")]
        mock_ctx, _conn, _process, _bridge = _make_mock_spawn_for_session(updates=updates)

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            await harness.start_session("/workspace", "sess-p1")
            events = []
            async for event in harness.prompt("sess-p1", "Do something"):
                events.append(event)

        assert len(events) == 3
        assert events[0].type == StreamEventType.ASSISTANT
        assert events[0].content["message"]["content"][0]["text"] == "Response text"
        assert events[1].type == StreamEventType.RESULT
        assert events[2].type == StreamEventType.SYSTEM
        assert events[2].content["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_prompt_nonexistent_session_raises(self) -> None:
        """prompt() raises AcpSessionError for unknown session ID."""
        from flowstate.engine.acp_client import AcpSessionError

        harness = AcpHarness(command=["test-agent"])

        with pytest.raises(AcpSessionError, match="No active session"):
            async for _ in harness.prompt("nonexistent", "hello"):
                pass

    @pytest.mark.asyncio
    async def test_prompt_dead_process_raises(self) -> None:
        """prompt() raises AcpSessionError if subprocess has exited."""
        from flowstate.engine.acp_client import AcpSessionError

        mock_ctx, _conn, process, _bridge = _make_mock_spawn_for_session()

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            await harness.start_session("/workspace", "sess-dead")
            # Simulate process death
            process.returncode = 1

            with pytest.raises(AcpSessionError, match="subprocess has exited"):
                async for _ in harness.prompt("sess-dead", "hello"):
                    pass

    @pytest.mark.asyncio
    async def test_prompt_multiple_turns(self) -> None:
        """Multiple prompt() calls work against the same session."""
        updates1 = [_make_agent_message_chunk("Turn 1")]
        updates2 = [_make_agent_message_chunk("Turn 2")]

        mock_ctx, conn, _process, captured_bridge = _make_mock_spawn_for_session(
            updates=updates1,
        )

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            await harness.start_session("/workspace", "sess-multi")

            # First turn
            events1 = []
            async for event in harness.prompt("sess-multi", "First prompt"):
                events1.append(event)
            assert any(
                e.type == StreamEventType.ASSISTANT
                and e.content["message"]["content"][0]["text"] == "Turn 1"
                for e in events1
            )

            # Set up for second turn -- change the side effect
            async def second_prompt(prompt: Any, session_id: str, **kwargs: Any) -> MagicMock:
                bridge = captured_bridge[0]
                for update in updates2:
                    await bridge.session_update(session_id, update)
                return _make_prompt_response("end_turn")

            conn.prompt = AsyncMock(side_effect=second_prompt)

            # Second turn on same session
            events2 = []
            async for event in harness.prompt("sess-multi", "Second prompt"):
                events2.append(event)
            assert any(
                e.type == StreamEventType.ASSISTANT
                and e.content["message"]["content"][0]["text"] == "Turn 2"
                for e in events2
            )


class TestAcpHarnessInterrupt:
    """Test AcpHarness.interrupt for cancelling without killing."""

    @pytest.mark.asyncio
    async def test_interrupt_calls_cancel(self) -> None:
        """interrupt() calls conn.cancel() without terminating subprocess."""
        mock_ctx, conn, process, _bridge = _make_mock_spawn_for_session()

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            await harness.start_session("/workspace", "sess-int")
            await harness.interrupt("sess-int")

        conn.cancel.assert_called_once()
        # Process should NOT be terminated
        process.terminate.assert_not_called()
        # Session should still be alive
        assert "sess-int" in harness._sessions

    @pytest.mark.asyncio
    async def test_interrupt_nonexistent_is_noop(self) -> None:
        """interrupt() with unknown session_id does nothing."""
        harness = AcpHarness(command=["test-agent"])
        # Should not raise
        await harness.interrupt("nonexistent")

    @pytest.mark.asyncio
    async def test_prompt_after_interrupt(self) -> None:
        """prompt() works after interrupt() (re-invocation)."""
        updates = [_make_agent_message_chunk("After interrupt")]
        mock_ctx, _conn, _process, _bridge = _make_mock_spawn_for_session(updates=updates)

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            await harness.start_session("/workspace", "sess-reuse")
            # Interrupt
            await harness.interrupt("sess-reuse")
            # Then prompt again
            events = []
            async for event in harness.prompt("sess-reuse", "Continue work"):
                events.append(event)

        assert any(e.type == StreamEventType.ASSISTANT for e in events)
        assert any(e.type == StreamEventType.RESULT for e in events)


class TestAcpHarnessKillSession:
    """Test AcpHarness.kill for long-lived sessions."""

    @pytest.mark.asyncio
    async def test_kill_terminates_and_removes_session(self) -> None:
        """kill() terminates subprocess and removes session from tracking."""
        mock_ctx, _conn, process, _bridge = _make_mock_spawn_for_session()

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            await harness.start_session("/workspace", "sess-kill")
            assert "sess-kill" in harness._sessions

            await harness.kill("sess-kill")

        assert "sess-kill" not in harness._sessions
        process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_kill_nonexistent_is_noop(self) -> None:
        """kill() with unknown session_id does nothing."""
        harness = AcpHarness(command=["test-agent"])
        await harness.kill("nonexistent")


class TestAcpHarnessRunTaskConvenience:
    """Test that run_task() still works as a convenience wrapper."""

    @pytest.mark.asyncio
    async def test_run_task_convenience(self) -> None:
        """run_task() works end-to-end (backward compat)."""
        updates = [_make_agent_message_chunk("Hello")]
        mock_ctx, _conn, _process = _make_mock_spawn_context(updates=updates)

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task("Test", "/workspace", "sess-conv"):
                events.append(event)

        assert len(events) == 3
        assert events[0].type == StreamEventType.ASSISTANT
        assert events[1].type == StreamEventType.RESULT
        assert events[2].type == StreamEventType.SYSTEM


# ---------------------------------------------------------------------------
# ENGINE-044: Environment, timeouts, and health check tests
# ---------------------------------------------------------------------------


class TestAcpHarnessEnvironment:
    """Verify subprocess environment includes critical variables."""

    @pytest.mark.asyncio
    async def test_env_includes_api_key(self) -> None:
        """_build_subprocess_env includes ANTHROPIC_API_KEY from os.environ."""
        from flowstate.engine.acp_client import _build_subprocess_env

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test-key"}, clear=False):
            env = _build_subprocess_env(None)
        assert env is not None
        assert env["ANTHROPIC_API_KEY"] == "sk-test-key"

    @pytest.mark.asyncio
    async def test_env_merges_with_harness_env(self) -> None:
        """_build_subprocess_env merges harness-provided env with required vars."""
        from flowstate.engine.acp_client import _build_subprocess_env

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test-key"}, clear=False):
            env = _build_subprocess_env({"CUSTOM_VAR": "custom"})
        assert env is not None
        assert env["ANTHROPIC_API_KEY"] == "sk-test-key"
        assert env["CUSTOM_VAR"] == "custom"

    @pytest.mark.asyncio
    async def test_env_returns_none_when_no_vars(self) -> None:
        """_build_subprocess_env returns None when no vars are present."""
        from flowstate.engine.acp_client import _build_subprocess_env

        with patch.dict(
            "os.environ",
            {},
            clear=True,
        ):
            # Restore PATH to avoid breaking things
            env = _build_subprocess_env(None)
        assert env is None


class TestAcpHarnessHealthCheck:
    """Verify subprocess health checks detect immediate exits."""

    @pytest.mark.asyncio
    async def test_start_session_immediate_exit(self) -> None:
        """start_session raises AcpSessionError if subprocess exits immediately."""
        from flowstate.engine.acp_client import AcpSessionError

        mock_ctx, _conn, process, _bridge = _make_mock_spawn_for_session()
        # Simulate immediate exit
        process.returncode = 1

        harness = AcpHarness(command=["bad-agent"])

        with (
            patch("acp.spawn_agent_process", mock_ctx),
            pytest.raises(AcpSessionError, match="exited immediately"),
        ):
            await harness.start_session("/workspace", "sess-dead")

    @pytest.mark.asyncio
    async def test_run_task_immediate_exit(self) -> None:
        """run_task yields error events if subprocess exits immediately."""
        mock_ctx, _conn, process = _make_mock_spawn_context()
        # Simulate immediate exit
        process.returncode = 1

        harness = AcpHarness(command=["bad-agent"])

        with patch("acp.spawn_agent_process", mock_ctx):
            events = []
            async for event in harness.run_task("Test", "/workspace", "sess-dead"):
                events.append(event)

        # Should get an ERROR event and a SYSTEM exit event
        assert any(e.type == StreamEventType.ERROR for e in events)


class TestAcpHarnessTimeouts:
    """Verify timeouts prevent infinite hangs on ACP RPC calls."""

    @pytest.mark.asyncio
    async def test_start_session_initialize_timeout(self) -> None:
        """start_session raises AcpSessionError when initialize() hangs."""
        from flowstate.engine.acp_client import AcpSessionError

        mock_ctx, conn, _process, _bridge = _make_mock_spawn_for_session()

        # Make initialize() hang forever
        async def hang_forever(**kw: Any) -> None:
            await asyncio.Event().wait()

        conn.initialize = AsyncMock(side_effect=hang_forever)

        harness = AcpHarness(command=["slow-agent"])

        with (
            patch("acp.spawn_agent_process", mock_ctx),
            patch("flowstate.engine.acp_client._ACP_INIT_TIMEOUT", 0.1),
            pytest.raises(AcpSessionError, match="initialize timed out"),
        ):
            await harness.start_session("/workspace", "sess-timeout")

    @pytest.mark.asyncio
    async def test_run_task_initialize_timeout(self) -> None:
        """run_task yields error when initialize() hangs."""
        mock_ctx, conn, _process = _make_mock_spawn_context()

        # Make initialize() hang forever
        async def hang_forever(**kw: Any) -> None:
            await asyncio.Event().wait()

        conn.initialize = AsyncMock(side_effect=hang_forever)

        harness = AcpHarness(command=["slow-agent"])

        with (
            patch("acp.spawn_agent_process", mock_ctx),
            patch("flowstate.engine.acp_client._ACP_INIT_TIMEOUT", 0.1),
        ):
            events = []
            async for event in harness.run_task("Test", "/workspace", "sess-timeout"):
                events.append(event)

        # Should get an ERROR event
        assert any(e.type == StreamEventType.ERROR for e in events)


# ---------------------------------------------------------------------------
# ENGINE-050: Real-time streaming tests
# ---------------------------------------------------------------------------


class TestAcpHarnessRealTimeStreaming:
    """Verify events are yielded DURING prompt execution, not batched after."""

    @pytest.mark.asyncio
    async def test_events_streamed_during_prompt_run_task(self) -> None:
        """Events are yielded while conn.prompt() is still running (run_task)."""
        prompt_can_finish = asyncio.Event()
        _captured_bridge: list[Any] = [None]

        async def slow_prompt(prompt: Any, session_id: str, **kw: Any) -> MagicMock:
            bridge = _captured_bridge[0]
            await bridge.session_update(session_id, _make_agent_message_chunk("Hello"))
            await bridge.session_update(session_id, _make_agent_message_chunk("World"))
            await prompt_can_finish.wait()  # block until test says continue
            return _make_prompt_response("end_turn")

        conn = AsyncMock()
        conn.initialize = AsyncMock()
        conn.new_session = AsyncMock(return_value=_make_new_session_response("acp-sess-1"))
        conn.prompt = AsyncMock(side_effect=slow_prompt)
        conn.cancel = AsyncMock()

        process = MagicMock()
        process.terminate = MagicMock()
        process.kill = MagicMock()
        process.wait = AsyncMock()
        process.returncode = None

        class _MockCtx:
            def __init__(self, to_client: Any, *args: Any, **kwargs: Any) -> None:
                if callable(to_client):
                    _captured_bridge[0] = to_client(conn)
                else:
                    _captured_bridge[0] = to_client

            async def __aenter__(self) -> tuple[Any, Any]:
                return (conn, process)

            async def __aexit__(self, *args: Any) -> None:
                pass

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", _MockCtx):
            events_received_before_finish: list[StreamEvent] = []
            all_events: list[StreamEvent] = []

            async for event in harness.run_task("Do something", "/tmp/work", "stream-sess"):
                all_events.append(event)
                # If prompt hasn't finished yet, record this event
                if not prompt_can_finish.is_set():
                    events_received_before_finish.append(event)
                    # After receiving first two events, let prompt finish
                    if len(events_received_before_finish) >= 2:
                        prompt_can_finish.set()

        # We should have received ASSISTANT events BEFORE the prompt finished
        assert len(events_received_before_finish) >= 2
        assert events_received_before_finish[0].type == StreamEventType.ASSISTANT
        assert events_received_before_finish[0].content["message"]["content"][0]["text"] == "Hello"
        assert events_received_before_finish[1].type == StreamEventType.ASSISTANT
        assert events_received_before_finish[1].content["message"]["content"][0]["text"] == "World"

        # Total events: 2 ASSISTANT + RESULT + SYSTEM(exit) = 4
        assert len(all_events) == 4
        assert all_events[2].type == StreamEventType.RESULT
        assert all_events[3].type == StreamEventType.SYSTEM

    @pytest.mark.asyncio
    async def test_events_streamed_during_prompt_session_api(self) -> None:
        """Events are yielded while conn.prompt() is still running (session API)."""
        prompt_can_finish = asyncio.Event()
        _captured_bridge: list[Any] = [None]

        async def slow_prompt(prompt: Any, session_id: str, **kw: Any) -> MagicMock:
            bridge = _captured_bridge[0]
            await bridge.session_update(session_id, _make_agent_message_chunk("Streaming"))
            await prompt_can_finish.wait()
            return _make_prompt_response("end_turn")

        conn = AsyncMock()
        conn.initialize = AsyncMock()
        conn.new_session = AsyncMock(return_value=_make_new_session_response("acp-sess-1"))
        conn.prompt = AsyncMock(side_effect=slow_prompt)
        conn.cancel = AsyncMock()

        process = MagicMock()
        process.terminate = MagicMock()
        process.kill = MagicMock()
        process.wait = AsyncMock()
        process.returncode = None

        class _MockCtx:
            def __init__(self, to_client: Any, *args: Any, **kwargs: Any) -> None:
                if callable(to_client):
                    _captured_bridge[0] = to_client(conn)
                else:
                    _captured_bridge[0] = to_client

            async def __aenter__(self) -> tuple[Any, Any]:
                return (conn, process)

            async def __aexit__(self, *args: Any) -> None:
                pass

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", _MockCtx):
            await harness.start_session("/workspace", "stream-sess-2")

            events_received_before_finish: list[StreamEvent] = []
            all_events: list[StreamEvent] = []

            async for event in harness.prompt("stream-sess-2", "Do something"):
                all_events.append(event)
                if not prompt_can_finish.is_set():
                    events_received_before_finish.append(event)
                    if len(events_received_before_finish) >= 1:
                        prompt_can_finish.set()

        # Got at least one event BEFORE prompt finished
        assert len(events_received_before_finish) >= 1
        assert events_received_before_finish[0].type == StreamEventType.ASSISTANT
        assert (
            events_received_before_finish[0].content["message"]["content"][0]["text"] == "Streaming"
        )

        # Total: 1 ASSISTANT + RESULT + SYSTEM = 3
        assert len(all_events) == 3

    @pytest.mark.asyncio
    async def test_prompt_error_during_streaming(self) -> None:
        """Errors from conn.prompt() are still propagated correctly with streaming."""
        _captured_bridge: list[Any] = [None]

        async def failing_prompt(prompt: Any, session_id: str, **kw: Any) -> MagicMock:
            bridge = _captured_bridge[0]
            await bridge.session_update(session_id, _make_agent_message_chunk("Before error"))
            raise RuntimeError("Prompt exploded")

        conn = AsyncMock()
        conn.initialize = AsyncMock()
        conn.new_session = AsyncMock(return_value=_make_new_session_response("acp-sess-1"))
        conn.prompt = AsyncMock(side_effect=failing_prompt)
        conn.cancel = AsyncMock()

        process = MagicMock()
        process.terminate = MagicMock()
        process.kill = MagicMock()
        process.wait = AsyncMock()
        process.returncode = None

        class _MockCtx:
            def __init__(self, to_client: Any, *args: Any, **kwargs: Any) -> None:
                if callable(to_client):
                    _captured_bridge[0] = to_client(conn)
                else:
                    _captured_bridge[0] = to_client

            async def __aenter__(self) -> tuple[Any, Any]:
                return (conn, process)

            async def __aexit__(self, *args: Any) -> None:
                pass

        harness = AcpHarness(command=["test-agent"])

        with patch("acp.spawn_agent_process", _MockCtx):
            events: list[StreamEvent] = []
            async for event in harness.run_task("Fail", "/tmp", "err-stream"):
                events.append(event)

        # Should have ASSISTANT (streamed before error), then ERROR + SYSTEM(exit)
        assert events[0].type == StreamEventType.ASSISTANT
        assert events[0].content["message"]["content"][0]["text"] == "Before error"
        assert any(e.type == StreamEventType.ERROR for e in events)
        assert any(
            e.type == StreamEventType.SYSTEM and e.content.get("exit_code") == 1 for e in events
        )
