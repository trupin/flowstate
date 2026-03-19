"""Unit tests for the MockSubprocessManager itself.

Validates that the mock correctly simulates subprocess behavior,
respects configuration, supports gates, and resets cleanly.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.mock_subprocess import (
    MockSubprocessManager,
    NodeBehavior,
)


@pytest.fixture()
def mock():
    return MockSubprocessManager()


class TestNodeBehavior:
    def test_success_defaults(self):
        b = NodeBehavior.success()
        assert b.exit_code == 0
        assert b.summary_content == "Task completed successfully."
        assert len(b.stream_lines) == 2
        assert b.stream_lines[0].type == "assistant"
        assert b.stream_lines[1].type == "result"

    def test_success_custom_summary(self):
        b = NodeBehavior.success("Custom summary")
        assert b.summary_content == "Custom summary"

    def test_failure_defaults(self):
        b = NodeBehavior.failure()
        assert b.exit_code == 1
        assert b.summary_content == ""
        assert len(b.stream_lines) == 2

    def test_failure_custom_message(self):
        b = NodeBehavior.failure("Disk full")
        assert b.stream_lines[1].content["error"]["message"] == "Disk full"

    def test_slow_creates_many_lines(self):
        b = NodeBehavior.slow(duration_lines=10)
        assert len(b.stream_lines) == 11  # 10 assistant + 1 result
        assert b.line_delay == 0.05
        assert b.exit_code == 0

    def test_with_output(self):
        b = NodeBehavior.with_output("msg1", "msg2", summary="done")
        assert len(b.stream_lines) == 3  # 2 assistant + 1 result
        assert b.exit_code == 0
        assert b.summary_content == "done"


class TestMockSubprocessManager:
    @pytest.mark.asyncio
    async def test_unconfigured_node_uses_default(self, mock):
        """Unconfigured nodes use the default success behavior."""
        events = []
        # Inject node name via flowstate marker
        prompt = "[flowstate:node=unknown_node]\nDo something."
        async for event in mock.run_task(prompt, "/tmp", "sess-1"):
            events.append(event)

        # Should have assistant + result + process_exit
        assert len(events) == 3
        assert events[-1].content["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_configured_node_returns_behavior(self, mock):
        """Configured nodes return their specified behavior."""
        mock.configure_node("analyze", NodeBehavior.failure("Crashed"))

        events = []
        prompt = "[flowstate:node=analyze]\nAnalyze the code."
        async for event in mock.run_task(prompt, "/tmp", "sess-2"):
            events.append(event)

        # Last event is process_exit with exit_code=1
        assert events[-1].content["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_stream_lines_order(self, mock):
        """Stream lines are yielded in order."""
        mock.configure_node(
            "test",
            NodeBehavior.with_output("first", "second", "third"),
        )

        events = []
        prompt = "[flowstate:node=test]\nRun tests."
        async for event in mock.run_task(prompt, "/tmp", "sess-3"):
            events.append(event)

        # 3 assistant + 1 result + 1 process_exit = 5
        assert len(events) == 5
        texts = [
            e.content["message"]["content"][0]["text"] for e in events if e.type == "assistant"
        ]
        assert texts == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_judge_returns_configured_decision(self, mock):
        """Judge returns the configured decision."""
        mock.configure_judge("review", "ship", confidence=0.95, reasoning="All good")

        prompt = "[flowstate:node=review]\nJudge this."
        result = await mock.run_judge(prompt, "/tmp")

        assert result.decision == "ship"
        assert result.confidence == 0.95
        assert result.reasoning == "All good"

    @pytest.mark.asyncio
    async def test_judge_sequential_decisions(self, mock):
        """Judge returns sequential decisions for cycle tests."""
        mock.configure_judge("review", "implement", confidence=0.8)
        mock.configure_judge("review", "ship", confidence=0.95)

        prompt = "[flowstate:node=review]\nJudge this."

        result1 = await mock.run_judge(prompt, "/tmp")
        assert result1.decision == "implement"

        result2 = await mock.run_judge(prompt, "/tmp")
        assert result2.decision == "ship"

        # Third call should repeat the last decision
        result3 = await mock.run_judge(prompt, "/tmp")
        assert result3.decision == "ship"

    @pytest.mark.asyncio
    async def test_judge_unconfigured_raises(self, mock):
        """Judge raises error when no decision is configured."""
        prompt = "[flowstate:node=unknown]\nJudge this."
        with pytest.raises(RuntimeError, match="no judge decision configured"):
            await mock.run_judge(prompt, "/tmp")

    @pytest.mark.asyncio
    async def test_gate_blocks_until_set(self, mock):
        """Gate blocks task execution until gate.set() is called."""
        gate = mock.add_gate("slow_node")
        mock.configure_node("slow_node", NodeBehavior.success())

        prompt = "[flowstate:node=slow_node]\nDo work."

        completed = False

        async def run_task():
            nonlocal completed
            async for _ in mock.run_task(prompt, "/tmp", "sess-4"):
                pass
            completed = True

        task = asyncio.create_task(run_task())

        # Give it time — should NOT complete
        await asyncio.sleep(0.1)
        assert not completed

        # Release gate
        gate.set()

        # Now it should complete
        await asyncio.wait_for(task, timeout=5.0)
        assert completed

    @pytest.mark.asyncio
    async def test_gate_already_set(self, mock):
        """Gate that is already set does not block."""
        gate = mock.add_gate("fast_node")
        gate.set()
        mock.configure_node("fast_node", NodeBehavior.success())

        prompt = "[flowstate:node=fast_node]\nDo work."
        events = []
        async for event in mock.run_task(prompt, "/tmp", "sess-5"):
            events.append(event)

        assert len(events) > 0
        assert events[-1].content["exit_code"] == 0

    def test_reset_clears_all(self, mock):
        """Reset clears all configuration."""
        mock.configure_node("test", NodeBehavior.failure())
        mock.configure_judge("review", "ship")
        mock.add_gate("blocked")

        mock.reset()

        assert mock._behaviors == {}
        assert mock._judge_decisions == {}
        assert mock._gates == {}
        assert mock._call_history == []

    @pytest.mark.asyncio
    async def test_call_history_tracked(self, mock):
        """Call history is recorded."""
        prompt = "[flowstate:node=test]\nDo work."
        async for _ in mock.run_task(prompt, "/tmp", "sess-1"):
            pass

        assert len(mock.call_history) == 1
        assert mock.call_history[0]["method"] == "run_task"
        assert mock.call_history[0]["node"] == "test"

    @pytest.mark.asyncio
    async def test_kill_is_noop(self, mock):
        """Kill is a no-op but recorded in history."""
        await mock.kill("sess-1")
        assert len(mock.call_history) == 1
        assert mock.call_history[0]["method"] == "kill"

    @pytest.mark.asyncio
    async def test_run_task_resume_delegates(self, mock):
        """run_task_resume delegates to run_task."""
        mock.configure_node("test", NodeBehavior.success())
        prompt = "[flowstate:node=test]\nContinue."

        events = []
        async for event in mock.run_task_resume(prompt, "/tmp", "sess-1"):
            events.append(event)

        assert len(events) > 0
        # Should have both run_task_resume and run_task in history
        assert any(h["method"] == "run_task_resume" for h in mock.call_history)
