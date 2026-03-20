"""SDK-based task runner — replaces SubprocessManager with claude-agent-sdk.

Uses the claude-agent-sdk's query() function to execute tasks instead of
spawning Claude Code CLI subprocesses directly. Converts SDK Message objects
to the existing StreamEvent types so the executor's event processing is unchanged.

All claude_agent_sdk imports are lazy (inside methods or TYPE_CHECKING) to
avoid slowing down module collection — the SDK probes for the CLI binary
at import time.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

# Re-export types that the rest of the engine imports from this module.
from flowstate.engine.subprocess_mgr import (
    JudgeError,
    JudgeResult,
    StreamEvent,
    StreamEventType,
    SubprocessError,  # noqa: F401 — re-exported for downstream consumers
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from claude_agent_sdk import ClaudeAgentOptions


def _message_to_events(message: object) -> list[StreamEvent]:
    """Convert a single SDK message to one or more StreamEvents.

    Maps SDK message types to the StreamEvent types expected by the executor:
    - AssistantMessage with TextBlock -> ASSISTANT
    - AssistantMessage with ToolUseBlock -> TOOL_USE
    - AssistantMessage with ToolResultBlock -> TOOL_RESULT
    - ResultMessage -> RESULT
    - Everything else -> SYSTEM
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    events: list[StreamEvent] = []

    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                raw = json.dumps(
                    {"type": "assistant", "message": {"content": [{"text": block.text}]}}
                )
                events.append(
                    StreamEvent(
                        type=StreamEventType.ASSISTANT,
                        content={
                            "type": "assistant",
                            "message": {"content": [{"text": block.text}]},
                        },
                        raw=raw,
                    )
                )
            elif isinstance(block, ToolUseBlock):
                raw = json.dumps(
                    {
                        "type": "tool_use",
                        "tool_use_id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
                events.append(
                    StreamEvent(
                        type=StreamEventType.TOOL_USE,
                        content={
                            "type": "tool_use",
                            "tool_use_id": block.id,
                            "name": block.name,
                            "input": block.input,
                        },
                        raw=raw,
                    )
                )
            elif isinstance(block, ToolResultBlock):
                content_str = (
                    block.content if isinstance(block.content, str) else json.dumps(block.content)
                )
                raw = json.dumps(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": content_str,
                    }
                )
                events.append(
                    StreamEvent(
                        type=StreamEventType.TOOL_RESULT,
                        content={
                            "type": "tool_result",
                            "tool_use_id": block.tool_use_id,
                            "content": content_str,
                        },
                        raw=raw,
                    )
                )

    elif isinstance(message, ResultMessage):
        result_text = message.result or ""
        raw = json.dumps({"type": "result", "result": result_text})
        events.append(
            StreamEvent(
                type=StreamEventType.RESULT,
                content={"type": "result", "result": result_text},
                raw=raw,
            )
        )

    else:
        raw = str(message)
        events.append(
            StreamEvent(
                type=StreamEventType.SYSTEM,
                content={"message": raw},
                raw=raw,
            )
        )

    return events


class SDKRunner:
    """SDK-based task runner that replaces SubprocessManager.

    Uses claude-agent-sdk's query() function internally. Converts SDK
    Message objects to StreamEvent types for backward compatibility with
    the executor's event processing loop.
    """

    _JUDGE_SYSTEM_PROMPT = (
        "You are a routing judge for the Flowstate orchestration system. "
        "You evaluate task outcomes and decide which transition to take. "
        'Respond with ONLY a raw JSON object with keys: "decision" (target name or "__none__"), '
        '"reasoning" (brief explanation), "confidence" (0.0-1.0). '
        "No markdown, no code fences, no extra text."
    )

    async def _run_query(
        self, prompt: str, options: ClaudeAgentOptions
    ) -> AsyncGenerator[StreamEvent, None]:
        """Shared streaming logic for run_task and run_task_resume."""
        from claude_agent_sdk import ProcessError, query

        try:
            async for message in query(prompt=prompt, options=options):
                for event in _message_to_events(message):
                    yield event
        except ProcessError as e:
            yield StreamEvent(
                type=StreamEventType.ERROR,
                content={"type": "error", "error": {"message": str(e)}},
                raw=json.dumps({"type": "error", "error": {"message": str(e)}}),
            )
            yield StreamEvent(
                type=StreamEventType.SYSTEM,
                content={
                    "event": "process_exit",
                    "exit_code": e.exit_code or 1,
                    "stderr": e.stderr or "",
                },
                raw=f"Process exited with code {e.exit_code or 1}",
            )
            return

        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Launch a fresh task session via SDK and stream events."""
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions(cwd=workspace)
        if skip_permissions:
            options.permission_mode = "acceptEdits"

        async for event in self._run_query(prompt, options):
            yield event

    async def run_task_resume(
        self,
        prompt: str,
        workspace: str,
        resume_session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Resume a previous session via SDK and stream events."""
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions(cwd=workspace, resume=resume_session_id)
        if skip_permissions:
            options.permission_mode = "acceptEdits"

        async for event in self._run_query(prompt, options):
            yield event

    async def run_judge(
        self, prompt: str, workspace: str, *, skip_permissions: bool = False
    ) -> JudgeResult:
        """Run a judge evaluation via SDK and return parsed result."""
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ProcessError,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            cwd=workspace,
            model="sonnet",
            system_prompt=self._JUDGE_SYSTEM_PROMPT,
            permission_mode="acceptEdits" if skip_permissions else "plan",
        )

        result_text = ""
        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    if message.result:
                        result_text += message.result
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            result_text += block.text
        except ProcessError as e:
            raise JudgeError(
                f"Judge SDK call failed: {e}",
                exit_code=e.exit_code or 1,
                stderr=e.stderr or "",
            ) from e

        try:
            data = json.loads(result_text)
            return JudgeResult(
                decision=data["decision"],
                reasoning=data["reasoning"],
                confidence=float(data["confidence"]),
                raw_output=result_text,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise JudgeError(
                f"Failed to parse judge output: {e}",
                exit_code=0,
                stderr="",
            ) from e

    async def kill(self, session_id: str) -> None:
        """No-op — SDK manages session lifecycle via async generator."""
