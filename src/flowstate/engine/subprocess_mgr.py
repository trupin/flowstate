"""Claude Code subprocess manager — launches and manages Claude Code subprocesses.

Handles spawning `claude` processes with the correct CLI flags, parsing their
streaming JSON output line by line, and yielding typed event objects to callers.
Three invocation patterns are supported: fresh task session (`run_task`),
resumed session (`run_task_resume`), and judge evaluation (`run_judge`).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class StreamEventType(StrEnum):
    """Categories for events emitted by a streaming Claude Code subprocess."""

    ASSISTANT = "assistant"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    RESULT = "result"
    ERROR = "error"
    SYSTEM = "system"  # internal events like process exit


@dataclass
class StreamEvent:
    """A single event parsed from Claude Code stream-json output."""

    type: StreamEventType
    content: dict  # type: ignore[type-arg]  # the full parsed JSON object from stdout
    raw: str  # the original line from stdout


@dataclass
class JudgeResult:
    """Parsed result from a judge subprocess invocation."""

    decision: str
    reasoning: str
    confidence: float
    raw_output: str


class SubprocessError(Exception):
    """Raised when a subprocess fails unexpectedly."""

    def __init__(self, message: str, exit_code: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


class JudgeError(SubprocessError):
    """Raised when a judge subprocess fails or returns unparseable output."""


_EVENT_TYPE_MAP: dict[str, StreamEventType] = {
    "assistant": StreamEventType.ASSISTANT,
    "tool_use": StreamEventType.TOOL_USE,
    "tool_result": StreamEventType.TOOL_RESULT,
    "result": StreamEventType.RESULT,
    "error": StreamEventType.ERROR,
}


class SubprocessManager:
    """Manages Claude Code subprocess lifecycle.

    Tracks running processes by session_id so they can be killed on demand.
    """

    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
        settings: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Launch a fresh Claude Code task session and stream events.

        Constructs: claude -p "<prompt>" --output-format stream-json --verbose

        The *settings* parameter is accepted for Harness protocol compatibility
        but ignored — the CLI subprocess discovers settings from cwd.
        """
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        async for event in self._run_streaming(cmd, workspace, session_id):
            yield event

    async def run_task_with_system_prompt(
        self,
        system_prompt: str,
        init_message: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
        model: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Launch a Claude Code session with a system prompt and short init message.

        Uses --system-prompt for context (not responded to at length) and -p for
        a short init message. This is faster than passing everything via -p because
        the system prompt is treated as context, not a prompt to respond to.

        Constructs: claude -p "<init>" --system-prompt "<system>" --output-format stream-json --verbose
        """
        cmd = [
            "claude",
            "-p",
            init_message,
            "--system-prompt",
            system_prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if model:
            cmd.extend(["--model", model])
        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        async for event in self._run_streaming(cmd, workspace, session_id):
            yield event

    async def run_task_resume(
        self,
        prompt: str,
        workspace: str,
        resume_session_id: str,
        *,
        skip_permissions: bool = False,
        settings: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Resume a previous Claude Code session and stream events.

        Constructs: claude -p "<prompt>" --output-format stream-json --verbose --resume <session_id>

        The *settings* parameter is accepted for Harness protocol compatibility
        but ignored — the CLI subprocess discovers settings from cwd.
        """
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--resume",
            resume_session_id,
        ]
        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        async for event in self._run_streaming(cmd, workspace, resume_session_id):
            yield event

    _JUDGE_SYSTEM_PROMPT = (
        "You are a routing judge for the Flowstate orchestration system. "
        "You evaluate task outcomes and decide which transition to take. "
        'Respond with ONLY a raw JSON object with keys: "decision" (target name or "__none__"), '
        '"reasoning" (brief explanation), "confidence" (0.0-1.0). '
        "No markdown, no code fences, no extra text."
    )

    async def run_judge(
        self, prompt: str, workspace: str, *, skip_permissions: bool = False
    ) -> JudgeResult:
        """Run a judge evaluation (non-streaming) and return the parsed result.

        Uses --system-prompt and --setting-sources user to isolate the judge
        from project CLAUDE.md and give it a clear routing role.
        Raises JudgeError on non-zero exit code or unparseable output.
        """
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--model",
            "sonnet",
            "--system-prompt",
            self._JUDGE_SYSTEM_PROMPT,
            "--setting-sources",
            "user",
        ]
        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd.extend(["--permission-mode", "plan"])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout_text = stdout_bytes.decode()
        stderr_text = stderr_bytes.decode()

        if proc.returncode != 0:
            raise JudgeError(
                f"Judge subprocess exited with code {proc.returncode}: {stderr_text}",
                exit_code=proc.returncode,
                stderr=stderr_text,
            )

        try:
            data = json.loads(stdout_text)
            # Claude CLI wraps output in a result envelope: {"type": "result", "result": "..."}
            # Unwrap to get the model's actual response text, then parse that as JSON.
            inner = json.loads(data["result"]) if "result" in data and "type" in data else data
            return JudgeResult(
                decision=inner["decision"],
                reasoning=inner["reasoning"],
                confidence=float(inner["confidence"]),
                raw_output=stdout_text,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise JudgeError(
                f"Failed to parse judge output: {e}",
                exit_code=proc.returncode,
                stderr=stderr_text,
            ) from e

    async def kill(self, session_id: str) -> None:
        """Terminate a running subprocess by session_id.

        No-op if the session_id is not tracked or the process already exited.
        Uses SIGTERM first, then SIGKILL after a 5s timeout.
        """
        proc = self._processes.pop(session_id, None)
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()

    async def start_session(self, workspace: str, session_id: str) -> None:
        """No-op for SubprocessManager -- each run_task spawns its own process."""

    async def prompt(self, session_id: str, message: str) -> AsyncGenerator[StreamEvent, None]:
        """Not supported by SubprocessManager -- use run_task() instead.

        Yields nothing. SubprocessManager does not support long-lived sessions.
        """
        return  # pragma: no cover
        yield  # type: ignore[misc]  # make this an async generator

    async def interrupt(self, session_id: str) -> None:
        """Interrupt by killing the subprocess (no graceful cancel support)."""
        await self.kill(session_id)

    async def _run_streaming(
        self, cmd: list[str], workspace: str, session_id: str
    ) -> AsyncGenerator[StreamEvent, None]:
        """Internal: launch a subprocess and yield StreamEvents from its stdout.

        Each line of stdout is expected to be a JSON object. Non-JSON lines are
        emitted as SYSTEM events. A final SYSTEM event with process exit info is
        always yielded.
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=10 * 1024 * 1024,  # 10 MB buffer for large streaming JSON lines
        )
        self._processes[session_id] = proc

        try:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                raw = line.decode().rstrip("\n")
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    event_type = self._classify_event(data.get("type", ""))
                    yield StreamEvent(type=event_type, content=data, raw=raw)
                except json.JSONDecodeError:
                    # Non-JSON line -- emit as system event
                    yield StreamEvent(
                        type=StreamEventType.SYSTEM,
                        content={"message": raw},
                        raw=raw,
                    )

            # Wait for process to finish
            await proc.wait()

            # Read stderr after process exit to avoid deadlocks
            stderr_text = ""
            if proc.stderr:
                stderr_bytes = await proc.stderr.read()
                stderr_text = stderr_bytes.decode()

            # Emit exit event
            yield StreamEvent(
                type=StreamEventType.SYSTEM,
                content={
                    "event": "process_exit",
                    "exit_code": proc.returncode,
                    "stderr": stderr_text,
                },
                raw=f"Process exited with code {proc.returncode}",
            )
        finally:
            self._processes.pop(session_id, None)

    @staticmethod
    def _classify_event(type_str: str) -> StreamEventType:
        """Map a JSON type field to the corresponding StreamEventType."""
        return _EVENT_TYPE_MAP.get(type_str, StreamEventType.SYSTEM)
