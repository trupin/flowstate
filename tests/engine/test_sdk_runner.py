"""Tests for SDKRunner — message-to-StreamEvent conversion and interface compliance.

SDK type imports are lazy to avoid slow CLI probing at test collection time.
"""

from __future__ import annotations

import json

import pytest

from flowstate.engine.sdk_runner import (
    JudgeError,
    JudgeResult,
    SDKRunner,
    StreamEvent,
    StreamEventType,
    SubprocessError,
    _message_to_events,
)


def _sdk_types():
    """Lazy import of SDK types — avoids slow CLI probe at collection time."""
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    return AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolResultBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# Tests: Message-to-StreamEvent Conversion
# ---------------------------------------------------------------------------


class TestMessageConversion:
    """Test _message_to_events converts SDK messages to StreamEvents correctly."""

    def test_text_block_produces_assistant_event(self) -> None:
        """An AssistantMessage with a TextBlock produces an ASSISTANT StreamEvent."""
        AssistantMessage, _, _, TextBlock, _, _ = _sdk_types()
        msg = AssistantMessage(
            content=[TextBlock(text="Hello world")],
            model="claude-sonnet-4-20250514",
        )
        events = _message_to_events(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.ASSISTANT
        assert events[0].content["type"] == "assistant"
        assert events[0].content["message"]["content"][0]["text"] == "Hello world"
        parsed_raw = json.loads(events[0].raw)
        assert parsed_raw["type"] == "assistant"

    def test_tool_use_block_produces_tool_use_event(self) -> None:
        """An AssistantMessage with a ToolUseBlock produces a TOOL_USE StreamEvent."""
        AssistantMessage, _, _, _, _, ToolUseBlock = _sdk_types()
        msg = AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu_123",
                    name="Bash",
                    input={"command": "ls -la"},
                )
            ],
            model="claude-sonnet-4-20250514",
        )
        events = _message_to_events(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.TOOL_USE
        assert events[0].content["tool_use_id"] == "tu_123"
        assert events[0].content["name"] == "Bash"
        assert events[0].content["input"] == {"command": "ls -la"}

    def test_tool_result_block_string_content(self) -> None:
        """An AssistantMessage with a ToolResultBlock (string content) produces TOOL_RESULT."""
        AssistantMessage, _, _, _, ToolResultBlock, _ = _sdk_types()
        msg = AssistantMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tu_123",
                    content="file.txt\ndir/",
                )
            ],
            model="claude-sonnet-4-20250514",
        )
        events = _message_to_events(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.TOOL_RESULT
        assert events[0].content["tool_use_id"] == "tu_123"
        assert events[0].content["content"] == "file.txt\ndir/"

    def test_tool_result_block_list_content(self) -> None:
        """A ToolResultBlock with list content is JSON-serialized."""
        AssistantMessage, _, _, _, ToolResultBlock, _ = _sdk_types()
        msg = AssistantMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tu_456",
                    content=[{"type": "text", "text": "result"}],
                )
            ],
            model="claude-sonnet-4-20250514",
        )
        events = _message_to_events(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.TOOL_RESULT
        content_str = events[0].content["content"]
        parsed = json.loads(content_str)
        assert parsed == [{"type": "text", "text": "result"}]

    def test_result_message_produces_result_event(self) -> None:
        """A ResultMessage produces a RESULT StreamEvent."""
        _, ResultMessage, _, _, _, _ = _sdk_types()
        msg = ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=900,
            is_error=False,
            num_turns=3,
            session_id="sess-abc",
            result="Task completed successfully",
        )
        events = _message_to_events(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.RESULT
        assert events[0].content["result"] == "Task completed successfully"
        parsed_raw = json.loads(events[0].raw)
        assert parsed_raw["result"] == "Task completed successfully"

    def test_result_message_with_none_result(self) -> None:
        """A ResultMessage with result=None uses empty string."""
        _, ResultMessage, _, _, _, _ = _sdk_types()
        msg = ResultMessage(
            subtype="result",
            duration_ms=500,
            duration_api_ms=400,
            is_error=False,
            num_turns=1,
            session_id="sess-xyz",
            result=None,
        )
        events = _message_to_events(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.RESULT
        assert events[0].content["result"] == ""

    def test_system_message_produces_system_event(self) -> None:
        """A SystemMessage produces a SYSTEM StreamEvent."""
        _, _, SystemMessage, _, _, _ = _sdk_types()
        msg = SystemMessage(subtype="init", data={"info": "starting"})
        events = _message_to_events(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.SYSTEM

    def test_multiple_blocks_produce_multiple_events(self) -> None:
        """An AssistantMessage with multiple blocks produces one event per block."""
        AssistantMessage, _, _, TextBlock, _, ToolUseBlock = _sdk_types()
        msg = AssistantMessage(
            content=[
                TextBlock(text="Let me check that."),
                ToolUseBlock(id="tu_1", name="Read", input={"path": "/foo"}),
            ],
            model="claude-sonnet-4-20250514",
        )
        events = _message_to_events(msg)

        assert len(events) == 2
        assert events[0].type == StreamEventType.ASSISTANT
        assert events[1].type == StreamEventType.TOOL_USE

    def test_unknown_message_type_produces_system_event(self) -> None:
        """An unknown message type falls through to SYSTEM."""

        class FutureMessageType:
            pass

        events = _message_to_events(FutureMessageType())
        assert len(events) == 1
        assert events[0].type == StreamEventType.SYSTEM


# ---------------------------------------------------------------------------
# Tests: SDKRunner Interface Compliance
# ---------------------------------------------------------------------------


class TestSDKRunnerInterface:
    """SDKRunner exposes the same methods as SubprocessManager."""

    def test_has_required_methods(self) -> None:
        """SDKRunner exposes run_task, run_task_resume, run_judge, kill."""
        runner = SDKRunner()
        assert hasattr(runner, "run_task")
        assert hasattr(runner, "run_task_resume")
        assert hasattr(runner, "run_judge")
        assert hasattr(runner, "kill")

    def test_has_judge_system_prompt(self) -> None:
        """SDKRunner has the same judge system prompt as SubprocessManager."""
        from flowstate.engine.subprocess_mgr import SubprocessManager

        assert SDKRunner._JUDGE_SYSTEM_PROMPT == SubprocessManager._JUDGE_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_kill_is_noop(self) -> None:
        """kill() completes without error (best-effort no-op)."""
        runner = SDKRunner()
        await runner.kill("any-session-id")


# ---------------------------------------------------------------------------
# Tests: Re-exports
# ---------------------------------------------------------------------------


class TestReExports:
    """Module re-exports all necessary types from subprocess_mgr."""

    def test_stream_event_type_values(self) -> None:
        """StreamEventType has all expected values."""
        assert StreamEventType.ASSISTANT == "assistant"
        assert StreamEventType.TOOL_USE == "tool_use"
        assert StreamEventType.TOOL_RESULT == "tool_result"
        assert StreamEventType.RESULT == "result"
        assert StreamEventType.ERROR == "error"
        assert StreamEventType.SYSTEM == "system"

    def test_stream_event_is_importable(self) -> None:
        """StreamEvent can be constructed."""
        event = StreamEvent(
            type=StreamEventType.ASSISTANT,
            content={"text": "hi"},
            raw='{"text": "hi"}',
        )
        assert event.type == StreamEventType.ASSISTANT

    def test_judge_result_is_importable(self) -> None:
        """JudgeResult can be constructed."""
        result = JudgeResult(
            decision="approve",
            reasoning="looks good",
            confidence=0.95,
            raw_output="{}",
        )
        assert result.decision == "approve"

    def test_subprocess_error_is_importable(self) -> None:
        """SubprocessError can be constructed."""
        err = SubprocessError("failed", exit_code=1, stderr="oops")
        assert err.exit_code == 1

    def test_judge_error_is_subprocess_error_subclass(self) -> None:
        """JudgeError is a subclass of SubprocessError."""
        assert issubclass(JudgeError, SubprocessError)
        err = JudgeError("bad output", exit_code=0, stderr="")
        assert isinstance(err, SubprocessError)
