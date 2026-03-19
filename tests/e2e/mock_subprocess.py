"""Mock subprocess manager for E2E tests.

Replaces the real SubprocessManager with a deterministic mock that returns
configurable stream-json output, supports per-node behavior configuration,
controllable gates for timing-sensitive tests, and configurable judge decisions.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flowstate.engine.subprocess_mgr import JudgeResult, StreamEvent, StreamEventType

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@dataclass
class MockStreamLine:
    """A single line of simulated stream-json output."""

    type: str  # matches StreamEventType values: assistant, tool_use, tool_result, result, error
    content: dict[str, Any]


@dataclass
class NodeBehavior:
    """Configurable behavior for a mocked node execution."""

    stream_lines: list[MockStreamLine]
    exit_code: int = 0
    summary_content: str = "Task completed successfully."
    line_delay: float = 0.01  # seconds between lines

    @staticmethod
    def success(summary: str = "Task completed successfully.") -> NodeBehavior:
        """Create a behavior that simulates successful task completion."""
        return NodeBehavior(
            stream_lines=[
                MockStreamLine(
                    "assistant",
                    {"type": "assistant", "message": {"content": [{"text": "Working on it..."}]}},
                ),
                MockStreamLine(
                    "result",
                    {"type": "result", "result": "Done.", "duration_ms": 100, "cost_usd": 0.01},
                ),
            ],
            exit_code=0,
            summary_content=summary,
        )

    @staticmethod
    def failure(error_msg: str = "Task failed") -> NodeBehavior:
        """Create a behavior that simulates task failure."""
        return NodeBehavior(
            stream_lines=[
                MockStreamLine(
                    "assistant",
                    {"type": "assistant", "message": {"content": [{"text": "Starting..."}]}},
                ),
                MockStreamLine(
                    "error",
                    {"type": "error", "error": {"message": error_msg}},
                ),
            ],
            exit_code=1,
            summary_content="",
        )

    @staticmethod
    def slow(duration_lines: int = 20, summary: str = "Slow task done.") -> NodeBehavior:
        """Create a behavior that simulates a slow-running task."""
        lines = [
            MockStreamLine(
                "assistant",
                {"type": "assistant", "message": {"content": [{"text": f"Step {i}..."}]}},
            )
            for i in range(duration_lines)
        ]
        lines.append(
            MockStreamLine(
                "result",
                {"type": "result", "result": "Done.", "duration_ms": 5000, "cost_usd": 0.10},
            )
        )
        return NodeBehavior(
            stream_lines=lines,
            exit_code=0,
            summary_content=summary,
            line_delay=0.05,
        )

    @staticmethod
    def with_output(
        *messages: str, summary: str = "Task completed.", exit_code: int = 0
    ) -> NodeBehavior:
        """Create a behavior with custom assistant messages."""
        lines: list[MockStreamLine] = [
            MockStreamLine(
                "assistant",
                {"type": "assistant", "message": {"content": [{"text": msg}]}},
            )
            for msg in messages
        ]
        if exit_code == 0:
            lines.append(
                MockStreamLine(
                    "result",
                    {"type": "result", "result": "Done.", "duration_ms": 100, "cost_usd": 0.01},
                )
            )
        return NodeBehavior(
            stream_lines=lines,
            exit_code=exit_code,
            summary_content=summary,
        )


@dataclass
class JudgeDecision:
    """A configurable decision returned by the mock judge."""

    target: str
    reasoning: str = "Mock judge decision"
    confidence: float = 0.9


# Map MockStreamLine type strings to StreamEventType enum values.
_STREAM_TYPE_MAP: dict[str, StreamEventType] = {
    "assistant": StreamEventType.ASSISTANT,
    "tool_use": StreamEventType.TOOL_USE,
    "tool_result": StreamEventType.TOOL_RESULT,
    "result": StreamEventType.RESULT,
    "error": StreamEventType.ERROR,
    "system": StreamEventType.SYSTEM,
}


class MockSubprocessManager:
    """A mock subprocess manager that replaces the real SubprocessManager during E2E tests.

    Provides the same async interface as SubprocessManager but returns deterministic,
    configurable output without spawning real Claude Code processes.

    Yields real ``StreamEvent`` objects (with ``StreamEventType`` enum values) and
    returns real ``JudgeResult`` objects so the engine's executor can consume them
    identically to a real subprocess manager.

    Usage:
        mock = MockSubprocessManager()
        mock.configure_node("analyze", NodeBehavior.success("Analysis done."))
        mock.configure_judge("review", "ship", confidence=0.95)

        # The executor calls these methods as if they were the real thing:
        async for event in mock.run_task(prompt, workspace, session_id):
            ...
        result = await mock.run_judge(prompt, workspace)
    """

    def __init__(self) -> None:
        self._behaviors: dict[str, NodeBehavior] = {}
        self._default_behavior: NodeBehavior = NodeBehavior.success()
        self._judge_decisions: dict[str, list[JudgeDecision]] = {}
        self._judge_call_counts: dict[str, int] = {}
        self._gates: dict[str, threading.Event] = {}
        self._task_dirs: dict[str, Path] = {}
        self._call_history: list[dict[str, str]] = []

    def configure_node(self, node_name: str, behavior: NodeBehavior) -> None:
        """Set the behavior for a specific node.

        Args:
            node_name: The name of the node (e.g., "analyze", "implement").
            behavior: The mock behavior to return when this node executes.
        """
        self._behaviors[node_name] = behavior

    def configure_judge(
        self,
        from_node: str,
        decision: str,
        confidence: float = 0.9,
        reasoning: str = "Mock judge decision",
    ) -> None:
        """Set a single judge decision for a node.

        For sequential decisions (cycles), call this multiple times -- decisions
        are stored in a list and consumed in order.

        Args:
            from_node: The node whose outgoing conditional edges are being judged.
            decision: The target node name the judge should choose.
            confidence: The confidence score (0.0-1.0).
            reasoning: The reasoning text.
        """
        if from_node not in self._judge_decisions:
            self._judge_decisions[from_node] = []
        self._judge_decisions[from_node].append(
            JudgeDecision(target=decision, reasoning=reasoning, confidence=confidence)
        )

    def add_gate(self, node_name: str) -> threading.Event:
        """Add a gate that blocks a task until gate.set() is called.

        Gates use threading.Event (not asyncio.Event) for cross-thread
        synchronization between the test thread (Playwright) and the
        server thread (asyncio).

        Args:
            node_name: The node to block.

        Returns:
            A threading.Event. Call .set() from the test thread to release.
        """
        gate = threading.Event()
        self._gates[node_name] = gate
        return gate

    def reset(self) -> None:
        """Clear all configuration between tests."""
        self._behaviors.clear()
        self._judge_decisions.clear()
        self._judge_call_counts.clear()
        self._gates.clear()
        self._task_dirs.clear()
        self._call_history.clear()

    @property
    def call_history(self) -> list[dict[str, str]]:
        """Return the history of calls made to this mock."""
        return list(self._call_history)

    def _extract_node_name(self, prompt: str) -> str:
        """Extract the node name from the prompt.

        The engine's context assembly injects a marker line like:
            [flowstate:node=<name>]
        Orchestrator task instructions use:
            Execute task "<name>" (generation N).
        If neither is found, try to match against configured node names.
        """
        import re

        # Look for the flowstate marker
        for line in prompt.splitlines():
            stripped = line.strip()
            if stripped.startswith("[flowstate:node=") and stripped.endswith("]"):
                return stripped[len("[flowstate:node=") : -1]

        # Look for orchestrator task instruction format
        m = re.search(r'Execute task "(\w+)"', prompt)
        if m:
            return m.group(1)

        # Fallback: match against configured behavior keys
        for name in self._behaviors:
            if name in prompt.lower():
                return name

        # Last resort: return "unknown"
        return "unknown"

    def _get_behavior(self, node_name: str) -> NodeBehavior:
        """Get the behavior for a node, falling back to default."""
        return self._behaviors.get(node_name, self._default_behavior)

    def _write_summary(self, node_name: str, prompt: str, behavior: NodeBehavior) -> None:
        """Write SUMMARY.md to the task directory if summary_content is set."""
        import re

        if not behavior.summary_content:
            return

        # Look for SUMMARY.md write instruction in the prompt.
        # The engine includes a line like:
        #   "When you are done, you MUST write a SUMMARY.md to /path/tasks/node-gen/SUMMARY.md"
        # Or for orchestrator instructions:
        #   "Write SUMMARY.md to: /path/tasks/node-gen/SUMMARY.md"
        m = re.search(r"SUMMARY\.md to[: ]+(.+)/SUMMARY\.md", prompt)
        if m:
            task_dir = Path(m.group(1))
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "SUMMARY.md").write_text(behavior.summary_content)
            return

        # Fallback: scan for task directory paths (/.flowstate/.../tasks/...)
        for line in prompt.splitlines():
            stripped = line.strip()
            if "/.flowstate/" in stripped and "/tasks/" in stripped and "SUMMARY" not in stripped:
                parts = stripped.split()
                for part in parts:
                    if "/.flowstate/" in part and "/tasks/" in part:
                        # Only match directory paths, not file paths
                        candidate = Path(part.rstrip("/").rstrip("."))
                        if candidate.suffix:
                            continue  # Skip file paths like INPUT.md
                        if candidate.exists() or candidate.parent.exists():
                            candidate.mkdir(parents=True, exist_ok=True)
                            (candidate / "SUMMARY.md").write_text(behavior.summary_content)
                            return

    @staticmethod
    def _to_stream_event_type(type_str: str) -> StreamEventType:
        """Convert a string type to the real StreamEventType enum."""
        return _STREAM_TYPE_MAP.get(type_str, StreamEventType.SYSTEM)

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Simulate running a task subprocess.

        Yields real StreamEvent objects matching the SubprocessManager interface.
        """
        node_name = self._extract_node_name(prompt)
        behavior = self._get_behavior(node_name)

        self._call_history.append(
            {"method": "run_task", "node": node_name, "session_id": session_id}
        )

        # Wait at gate if one is configured
        gate = self._gates.get(node_name)
        if gate is not None:
            await asyncio.to_thread(gate.wait)

        # Stream the configured lines
        for line in behavior.stream_lines:
            raw = json.dumps(line.content)
            event_type = self._to_stream_event_type(line.type)
            yield StreamEvent(type=event_type, content=line.content, raw=raw)
            if behavior.line_delay > 0:
                await asyncio.sleep(behavior.line_delay)

        # Write SUMMARY.md
        self._write_summary(node_name, prompt, behavior)

        # Yield process exit event
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={
                "event": "process_exit",
                "exit_code": behavior.exit_code,
                "stderr": "",
            },
            raw=f"Process exited with code {behavior.exit_code}",
        )

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
        """Simulate running a task with a system prompt. Delegates to run_task."""
        self._call_history.append(
            {"method": "run_task_with_system_prompt", "session_id": session_id}
        )
        # Yield a system/init event with the session_id (mimics real Claude Code)
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"type": "system", "subtype": "init", "session_id": session_id},
            raw=json.dumps({"type": "system", "subtype": "init", "session_id": session_id}),
        )
        # Yield a quick result
        yield StreamEvent(
            type=StreamEventType.RESULT,
            content={"type": "result", "result": "Orchestrator ready."},
            raw=json.dumps({"type": "result", "result": "Orchestrator ready."}),
        )
        # Yield exit
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def run_task_resume(
        self,
        prompt: str,
        workspace: str,
        resume_session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Simulate resuming a task subprocess session.

        Behaves identically to run_task for mock purposes.
        """
        node_name = self._extract_node_name(prompt)

        self._call_history.append(
            {"method": "run_task_resume", "node": node_name, "session_id": resume_session_id}
        )

        async for event in self.run_task(prompt, workspace, resume_session_id):
            yield event

    async def run_judge(
        self, prompt: str, workspace: str, *, skip_permissions: bool = False
    ) -> JudgeResult:
        """Simulate running a judge subprocess.

        Returns a real JudgeResult matching the SubprocessManager interface.
        Supports sequential decisions for cycle tests.
        """
        node_name = self._extract_node_name(prompt)

        self._call_history.append({"method": "run_judge", "node": node_name})

        decisions = self._judge_decisions.get(node_name)
        if not decisions:
            raise RuntimeError(
                f"MockSubprocessManager: no judge decision configured for node '{node_name}'. "
                f"Call configure_judge('{node_name}', '<target>') before running the flow."
            )

        # Track call count for sequential decisions
        call_idx = self._judge_call_counts.get(node_name, 0)
        self._judge_call_counts[node_name] = call_idx + 1

        # Use the call_idx-th decision, or the last one if we've exhausted the list
        decision = decisions[min(call_idx, len(decisions) - 1)]

        raw = json.dumps(
            {
                "decision": decision.target,
                "reasoning": decision.reasoning,
                "confidence": decision.confidence,
            }
        )

        return JudgeResult(
            decision=decision.target,
            reasoning=decision.reasoning,
            confidence=decision.confidence,
            raw_output=raw,
        )

    async def kill(self, session_id: str) -> None:
        """Simulate killing a running subprocess. No-op for mock."""
        self._call_history.append({"method": "kill", "session_id": session_id})
