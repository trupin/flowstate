"""Tests for SubprocessManager — Claude Code subprocess lifecycle."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from flowstate.engine.subprocess_mgr import (
    JudgeError,
    JudgeResult,
    StreamEventType,
    SubprocessManager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_process(
    stdout_lines: list[str] | None = None,
    stderr_text: str = "",
    returncode: int = 0,
) -> MagicMock:
    """Build a mock asyncio.subprocess.Process with predetermined output."""
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.returncode = None  # not yet finished

    # -- stdout (line-by-line reading for streaming) --
    if stdout_lines is not None:
        encoded_lines = [line.encode() + b"\n" for line in stdout_lines]
        line_iter = iter([*encoded_lines, b""])  # b"" signals EOF

        async def readline() -> bytes:
            return next(line_iter)

        stdout_mock = MagicMock()
        stdout_mock.readline = readline
        proc.stdout = stdout_mock
    else:
        proc.stdout = None

    # -- stderr (read after process exit) --
    async def read_stderr() -> bytes:
        return stderr_text.encode()

    stderr_mock = MagicMock()
    stderr_mock.read = read_stderr
    proc.stderr = stderr_mock

    # -- wait() sets returncode --
    async def wait() -> int:
        proc.returncode = returncode
        return returncode

    proc.wait = wait

    # -- communicate() for judge (non-streaming) --
    async def communicate() -> tuple[bytes, bytes]:
        proc.returncode = returncode
        all_stdout = "\n".join(stdout_lines) if stdout_lines else ""
        return (all_stdout.encode(), stderr_text.encode())

    proc.communicate = communicate

    # -- terminate / kill --
    proc.terminate = MagicMock()
    proc.kill = MagicMock()

    return proc


# ---------------------------------------------------------------------------
# Tests: Command Construction
# ---------------------------------------------------------------------------


class TestRunTaskCommandConstruction:
    @pytest.mark.asyncio
    async def test_run_task_command_construction(self) -> None:
        """run_task passes correct CLI flags and cwd to create_subprocess_exec."""
        mock_proc = _make_mock_process(stdout_lines=[], returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            events = [e async for e in mgr.run_task("do stuff", "/workspace", "sess-1")]

            mock_exec.assert_called_once_with(
                "claude",
                "-p",
                "do stuff",
                "--output-format",
                "stream-json",
                cwd="/workspace",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Should have at least the exit event
            assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_run_task_resume_command_construction(self) -> None:
        """run_task_resume includes --resume <session_id> in the command."""
        mock_proc = _make_mock_process(stdout_lines=[], returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            events = [e async for e in mgr.run_task_resume("continue", "/workspace", "prev-sess")]

            mock_exec.assert_called_once_with(
                "claude",
                "-p",
                "continue",
                "--output-format",
                "stream-json",
                "--resume",
                "prev-sess",
                cwd="/workspace",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_run_judge_command_construction(self) -> None:
        """run_judge includes --output-format json, --permission-mode plan, --model sonnet."""
        judge_output = json.dumps(
            {"decision": "approve", "reasoning": "looks good", "confidence": 0.95}
        )
        mock_proc = _make_mock_process(stdout_lines=[judge_output], returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            await mgr.run_judge("judge this", "/workspace")

            mock_exec.assert_called_once_with(
                "claude",
                "-p",
                "judge this",
                "--output-format",
                "json",
                "--permission-mode",
                "plan",
                "--model",
                "sonnet",
                cwd="/workspace",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )


# ---------------------------------------------------------------------------
# Tests: Stream Event Parsing
# ---------------------------------------------------------------------------


class TestStreamEventParsing:
    @pytest.mark.asyncio
    async def test_stream_event_parsing(self) -> None:
        """Multiple JSON lines with different types are parsed and classified correctly."""
        lines = [
            json.dumps({"type": "assistant", "content": "hello"}),
            json.dumps({"type": "tool_use", "tool": "bash", "input": "ls"}),
            json.dumps({"type": "tool_result", "output": "file.txt"}),
            json.dumps({"type": "result", "result": "done"}),
        ]
        mock_proc = _make_mock_process(stdout_lines=lines, returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            events = [e async for e in mgr.run_task("test", "/w", "s1")]

        # 4 content events + 1 exit event
        assert len(events) == 5
        assert events[0].type == StreamEventType.ASSISTANT
        assert events[0].content["content"] == "hello"
        assert events[1].type == StreamEventType.TOOL_USE
        assert events[2].type == StreamEventType.TOOL_RESULT
        assert events[3].type == StreamEventType.RESULT

    @pytest.mark.asyncio
    async def test_stream_error_event(self) -> None:
        """An error-type JSON line becomes StreamEventType.ERROR."""
        lines = [json.dumps({"type": "error", "message": "something broke"})]
        mock_proc = _make_mock_process(stdout_lines=lines, returncode=1)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            events = [e async for e in mgr.run_task("test", "/w", "s1")]

        assert events[0].type == StreamEventType.ERROR
        assert events[0].content["message"] == "something broke"

    @pytest.mark.asyncio
    async def test_stream_non_json_line(self) -> None:
        """A non-JSON stdout line becomes StreamEventType.SYSTEM."""
        lines = ["Not valid JSON at all"]
        mock_proc = _make_mock_process(stdout_lines=lines, returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            events = [e async for e in mgr.run_task("test", "/w", "s1")]

        assert events[0].type == StreamEventType.SYSTEM
        assert events[0].content["message"] == "Not valid JSON at all"
        assert events[0].raw == "Not valid JSON at all"

    @pytest.mark.asyncio
    async def test_stream_exit_event(self) -> None:
        """The final event after process exit has event=process_exit and the correct exit code."""
        mock_proc = _make_mock_process(stdout_lines=[], returncode=42, stderr_text="err msg")
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            events = [e async for e in mgr.run_task("test", "/w", "s1")]

        assert len(events) == 1
        exit_event = events[0]
        assert exit_event.type == StreamEventType.SYSTEM
        assert exit_event.content["event"] == "process_exit"
        assert exit_event.content["exit_code"] == 42
        assert exit_event.content["stderr"] == "err msg"

    @pytest.mark.asyncio
    async def test_stream_unknown_type_becomes_system(self) -> None:
        """A JSON line with an unknown type field becomes SYSTEM."""
        lines = [json.dumps({"type": "unknown_type", "data": "something"})]
        mock_proc = _make_mock_process(stdout_lines=lines, returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            events = [e async for e in mgr.run_task("test", "/w", "s1")]

        assert events[0].type == StreamEventType.SYSTEM

    @pytest.mark.asyncio
    async def test_stream_raw_preserved(self) -> None:
        """The raw field contains the original stdout line."""
        line = json.dumps({"type": "assistant", "text": "hi"})
        mock_proc = _make_mock_process(stdout_lines=[line], returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            events = [e async for e in mgr.run_task("test", "/w", "s1")]

        assert events[0].raw == line


# ---------------------------------------------------------------------------
# Tests: Judge
# ---------------------------------------------------------------------------


class TestRunJudge:
    @pytest.mark.asyncio
    async def test_run_judge_success(self) -> None:
        """Judge returns valid JSON with decision/reasoning/confidence -> JudgeResult."""
        judge_data = {"decision": "implement", "reasoning": "code looks ready", "confidence": 0.85}
        judge_output = json.dumps(judge_data)
        mock_proc = _make_mock_process(stdout_lines=[judge_output], returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            result = await mgr.run_judge("judge prompt", "/workspace")

        assert isinstance(result, JudgeResult)
        assert result.decision == "implement"
        assert result.reasoning == "code looks ready"
        assert result.confidence == 0.85
        assert result.raw_output == judge_output

    @pytest.mark.asyncio
    async def test_run_judge_non_zero_exit(self) -> None:
        """Judge exits with code 1 -> JudgeError with exit code and stderr."""
        mock_proc = _make_mock_process(stdout_lines=[""], returncode=1, stderr_text="judge crashed")
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            with pytest.raises(JudgeError) as exc_info:
                await mgr.run_judge("judge prompt", "/workspace")

        assert exc_info.value.exit_code == 1
        assert exc_info.value.stderr == "judge crashed"

    @pytest.mark.asyncio
    async def test_run_judge_invalid_json(self) -> None:
        """Judge returns non-JSON output -> JudgeError."""
        mock_proc = _make_mock_process(stdout_lines=["not json!!!"], returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            with pytest.raises(JudgeError, match="Failed to parse judge output"):
                await mgr.run_judge("judge prompt", "/workspace")

    @pytest.mark.asyncio
    async def test_run_judge_missing_fields(self) -> None:
        """Judge returns JSON missing required fields -> JudgeError."""
        incomplete = json.dumps({"decision": "ok"})  # missing reasoning and confidence
        mock_proc = _make_mock_process(stdout_lines=[incomplete], returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            with pytest.raises(JudgeError, match="Failed to parse judge output"):
                await mgr.run_judge("judge prompt", "/workspace")

    @pytest.mark.asyncio
    async def test_run_judge_confidence_as_string(self) -> None:
        """Judge returns confidence as string that can be converted to float."""
        judge_data = {"decision": "ok", "reasoning": "fine", "confidence": "0.9"}
        judge_output = json.dumps(judge_data)
        mock_proc = _make_mock_process(stdout_lines=[judge_output], returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            result = await mgr.run_judge("judge prompt", "/workspace")

        assert result.confidence == 0.9


# ---------------------------------------------------------------------------
# Tests: Kill
# ---------------------------------------------------------------------------


class TestKill:
    @pytest.mark.asyncio
    async def test_kill_running_process(self) -> None:
        """Kill a tracked running process -> terminate() is called."""
        mock_proc = _make_mock_process(stdout_lines=[], returncode=0)
        mock_proc.returncode = None  # still running

        async def wait() -> int:
            mock_proc.returncode = -15
            return -15

        mock_proc.wait = wait

        mgr = SubprocessManager()
        mgr._processes["sess-1"] = mock_proc

        await mgr.kill("sess-1")

        mock_proc.terminate.assert_called_once()
        assert "sess-1" not in mgr._processes

    @pytest.mark.asyncio
    async def test_kill_nonexistent_session(self) -> None:
        """Kill with unknown session_id -> no error (no-op)."""
        mgr = SubprocessManager()
        # Should not raise
        await mgr.kill("nonexistent")

    @pytest.mark.asyncio
    async def test_kill_already_exited(self) -> None:
        """Kill a process that already exited -> no-op (returncode is set)."""
        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.returncode = 0  # already exited
        mock_proc.terminate = MagicMock()

        mgr = SubprocessManager()
        mgr._processes["sess-done"] = mock_proc

        await mgr.kill("sess-done")
        mock_proc.terminate.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Process tracking cleanup
# ---------------------------------------------------------------------------


class TestProcessTracking:
    @pytest.mark.asyncio
    async def test_process_removed_after_streaming(self) -> None:
        """After streaming completes, the session is removed from _processes."""
        mock_proc = _make_mock_process(stdout_lines=[], returncode=0)
        mgr = SubprocessManager()

        with patch("flowstate.engine.subprocess_mgr.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            events = [e async for e in mgr.run_task("test", "/w", "s1")]

        assert "s1" not in mgr._processes
        assert len(events) >= 1
