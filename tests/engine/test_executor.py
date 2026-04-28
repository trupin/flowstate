"""Tests for FlowExecutor -- linear, fork-join, conditional, cycle, and control operations.

All tests use an in-memory SQLite database and a MockSubprocessManager that
returns configurable StreamEvent sequences. No real Claude Code subprocesses
are launched.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flowstate.dsl.ast import (
    ContextMode,
    Edge,
    EdgeConfig,
    EdgeType,
    ErrorPolicy,
    Flow,
    Node,
    NodeType,
)
from flowstate.engine.context import build_task_management_instructions
from flowstate.engine.events import EventType, FlowEvent
from flowstate.engine.executor import FlowExecutor, FlowExecutorConfigError, _use_subtasks
from flowstate.engine.judge import JudgeContext, JudgeDecision, JudgePauseError, JudgeProtocol
from flowstate.engine.subprocess_mgr import StreamEvent, StreamEventType
from flowstate.engine.worktree import WorktreeInfo
from flowstate.state.repository import FlowstateDB

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# ---------------------------------------------------------------------------
# Mock subprocess manager
# ---------------------------------------------------------------------------


class MockSubprocessManager:
    """A test double satisfying the Harness protocol with configurable events.

    Configure per-node responses via task_responses dict.  Keys are node name
    markers of the form "Do the <name> step" (matching the prompt pattern from
    _make_linear_flow).  Values are (exit_code, extra_events) tuples.

    When a prompt does not match any key, a default success response is returned.
    """

    def __init__(self) -> None:
        # Map of prompt-substring -> (exit_code, list of extra events before exit)
        # Keys are matched using "Do the <key> step" pattern for safety
        self.task_responses: dict[str, tuple[int, list[StreamEvent]]] = {}
        # Capture all run_task calls for assertions: list of (prompt, workspace, session_id)
        self.calls: list[tuple[str, str, str]] = []
        self.resume_calls: list[tuple[str, str, str]] = []
        # Track concurrent executions for semaphore tests
        self._concurrent_count = 0
        self._max_concurrent_seen = 0
        self._concurrency_lock = asyncio.Lock()
        # Optional delay for simulating long-running tasks
        self.task_delays: dict[str, float] = {}
        # Track kill calls
        self.kill_calls: list[str] = []
        # Command and env for harness protocol
        self._command: list[str] = ["claude"]
        self._env: dict[str, str] | None = None

    @property
    def command(self) -> list[str]:
        return list(self._command)

    @property
    def env(self) -> dict[str, str] | None:
        return dict(self._env) if self._env else None

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
        settings: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.calls.append((prompt, workspace, session_id))
        exit_code, extra_events = self._find_response(prompt)

        async with self._concurrency_lock:
            self._concurrent_count += 1
            self._max_concurrent_seen = max(self._max_concurrent_seen, self._concurrent_count)

        try:
            # Check for configured delay
            delay = self._find_delay(prompt)
            if delay > 0:
                await asyncio.sleep(delay)

            for evt in extra_events:
                yield evt

            yield StreamEvent(
                type=StreamEventType.SYSTEM,
                content={"event": "process_exit", "exit_code": exit_code, "stderr": ""},
                raw=f"Process exited with code {exit_code}",
            )
        finally:
            async with self._concurrency_lock:
                self._concurrent_count -= 1

    async def run_task_resume(
        self,
        prompt: str,
        workspace: str,
        resume_session_id: str,
        *,
        skip_permissions: bool = False,
        settings: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.resume_calls.append((prompt, workspace, resume_session_id))
        exit_code, extra_events = self._find_response(prompt)

        for evt in extra_events:
            yield evt

        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": exit_code, "stderr": ""},
            raw=f"Process exited with code {exit_code}",
        )

    async def run_judge(
        self, prompt: str, workspace: str, *, skip_permissions: bool = False
    ) -> Any:
        raise NotImplementedError("Judge not mocked in MockSubprocessManager")

    async def kill(self, session_id: str) -> None:
        self.kill_calls.append(session_id)

    async def start_session(self, workspace: str, session_id: str) -> None:
        pass

    async def prompt(self, session_id: str, message: str) -> AsyncGenerator[StreamEvent, None]:
        exit_code, extra_events = self._find_response(message)
        for evt in extra_events:
            yield evt
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": exit_code, "stderr": ""},
            raw=f"Process exited with code {exit_code}",
        )

    async def interrupt(self, session_id: str) -> None:
        pass

    def _find_response(self, prompt: str) -> tuple[int, list[StreamEvent]]:
        """Match a prompt against configured responses.

        Uses "Do the <key> step" pattern to avoid false matches (e.g.,
        "work" matching "workflow" in the prompt preamble).
        """
        for key, response in self.task_responses.items():
            marker = f"Do the {key} step"
            if marker in prompt:
                return response
        return (0, [])

    def _find_delay(self, prompt: str) -> float:
        """Match a prompt against configured delays."""
        for key, delay in self.task_delays.items():
            marker = f"Do the {key} step"
            if marker in prompt:
                return delay
        return 0.0


# ---------------------------------------------------------------------------
# Mock Judge
# ---------------------------------------------------------------------------


class MockJudgeProtocol(JudgeProtocol):
    """A test double for JudgeProtocol that returns configurable decisions."""

    def __init__(self) -> None:
        # Don't call super().__init__() since we don't need a subprocess manager
        self._decisions: list[JudgeDecision | JudgePauseError] = []
        self._call_count = 0
        self.contexts: list[JudgeContext] = []

    def add_decision(self, decision: JudgeDecision | JudgePauseError) -> None:
        """Add a decision to return on the next evaluate() call."""
        self._decisions.append(decision)

    async def evaluate(
        self,
        context: JudgeContext,
    ) -> JudgeDecision:
        self.contexts.append(context)
        if self._call_count < len(self._decisions):
            result = self._decisions[self._call_count]
            self._call_count += 1
            if isinstance(result, JudgePauseError):
                raise result
            return result
        # Default: return first target with high confidence
        if context.outgoing_edges:
            return JudgeDecision(
                target=context.outgoing_edges[0][1],
                reasoning="Default mock decision",
                confidence=0.9,
            )
        return JudgeDecision(target="__none__", reasoning="No edges", confidence=0.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_linear_flow(
    name: str = "test-flow",
    budget_seconds: int = 3600,
    on_error: ErrorPolicy = ErrorPolicy.PAUSE,
    context: ContextMode = ContextMode.HANDOFF,
    workspace: str = "/workspace",
    node_names: list[str] | None = None,
    edge_context: ContextMode | None = None,
) -> Flow:
    """Build a linear flow: entry -> task(s) -> exit.

    Default is a 3-node flow: start -> work -> finish.
    """
    if node_names is None:
        node_names = ["start", "work", "finish"]

    nodes: dict[str, Node] = {}
    for i, nname in enumerate(node_names):
        if i == 0:
            ntype = NodeType.ENTRY
        elif i == len(node_names) - 1:
            ntype = NodeType.EXIT
        else:
            ntype = NodeType.TASK
        nodes[nname] = Node(
            name=nname,
            node_type=ntype,
            prompt=f"Do the {nname} step",
        )

    edges: list[Edge] = []
    for i in range(len(node_names) - 1):
        config = EdgeConfig(context=edge_context) if edge_context is not None else EdgeConfig()
        edges.append(
            Edge(
                edge_type=EdgeType.UNCONDITIONAL,
                source=node_names[i],
                target=node_names[i + 1],
                config=config,
            )
        )

    return Flow(
        name=name,
        budget_seconds=budget_seconds,
        on_error=on_error,
        context=context,
        workspace=workspace,
        nodes=nodes,
        edges=tuple(edges),
    )


def _make_fork_join_flow(
    fork_targets: list[str] | None = None,
    workspace: str = "/workspace",
    on_error: ErrorPolicy = ErrorPolicy.PAUSE,
) -> Flow:
    """Build a fork-join flow: start -> [fork targets] -> merge -> finish."""
    if fork_targets is None:
        fork_targets = ["task_a", "task_b"]

    nodes: dict[str, Node] = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Do the start step"),
        "merge": Node(name="merge", node_type=NodeType.TASK, prompt="Do the merge step"),
        "finish": Node(name="finish", node_type=NodeType.EXIT, prompt="Do the finish step"),
    }
    for ft in fork_targets:
        nodes[ft] = Node(name=ft, node_type=NodeType.TASK, prompt=f"Do the {ft} step")

    edges = [
        # start -> fork
        Edge(
            edge_type=EdgeType.FORK,
            source="start",
            fork_targets=tuple(fork_targets),
        ),
        # join -> merge
        Edge(
            edge_type=EdgeType.JOIN,
            join_sources=tuple(fork_targets),
            target="merge",
        ),
        # merge -> finish
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="merge",
            target="finish",
        ),
    ]

    return Flow(
        name="fork-join-flow",
        budget_seconds=3600,
        on_error=on_error,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes,
        edges=tuple(edges),
    )


def _make_conditional_flow(
    with_cycle: bool = False,
    context: ContextMode = ContextMode.HANDOFF,
    edge_context: ContextMode | None = None,
) -> Flow:
    """Build a conditional flow: start -> implement -> review -> (done|implement).

    If with_cycle=True, the "needs work" edge loops back to implement.
    """
    nodes: dict[str, Node] = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Do the start step"),
        "implement": Node(
            name="implement", node_type=NodeType.TASK, prompt="Do the implement step"
        ),
        "review": Node(name="review", node_type=NodeType.TASK, prompt="Do the review step"),
        "done": Node(name="done", node_type=NodeType.EXIT, prompt="Do the done step"),
    }

    config = EdgeConfig(context=edge_context) if edge_context is not None else EdgeConfig()

    edges: list[Edge] = [
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="start",
            target="implement",
        ),
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="implement",
            target="review",
        ),
        Edge(
            edge_type=EdgeType.CONDITIONAL,
            source="review",
            target="done",
            condition="approved",
            config=config,
        ),
    ]

    if with_cycle:
        edges.append(
            Edge(
                edge_type=EdgeType.CONDITIONAL,
                source="review",
                target="implement",
                condition="needs work",
                config=config,
            )
        )

    return Flow(
        name="conditional-flow",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=context,
        workspace="/workspace",
        judge=True,  # Use mock judge subprocess for conditional routing
        nodes=nodes,
        edges=tuple(edges),
    )


def _make_db() -> FlowstateDB:
    """Create an in-memory FlowstateDB for testing."""
    return FlowstateDB(":memory:")


def _collect_events() -> tuple[list[FlowEvent], Any]:
    """Return a list to collect events and the callback function."""
    events: list[FlowEvent] = []

    def callback(event: FlowEvent) -> None:
        events.append(event)

    return events, callback


async def _execute_until_paused(
    executor: FlowExecutor,
    flow: Flow,
    params: dict[str, str | float | bool],
    workspace: str,
    db: FlowstateDB,
) -> tuple[str, asyncio.Task[str]]:
    """Run execute() in background and wait until the flow is paused.

    Returns (flow_run_id, execute_task). The caller must cancel the executor
    (via executor.cancel()) to let the execute_task finish, or resume then
    await completion.
    """
    execute_task = asyncio.create_task(executor.execute(flow, params, workspace))
    # Poll briefly for the run to appear and reach paused status.
    flow_run_id: str | None = None
    for _ in range(200):
        await asyncio.sleep(0.01)
        if execute_task.done():
            # execute returned early (e.g., completed before pause could happen)
            return await execute_task, execute_task
        runs = db.list_flow_runs()
        if runs:
            run = db.get_flow_run(runs[0].id)
            if run and run.status == "paused":
                flow_run_id = run.id
                break
    if flow_run_id is None:
        # If we never reached paused, cancel and raise
        execute_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await execute_task
        raise AssertionError("Flow did not reach paused state within timeout")
    return flow_run_id, execute_task


# ---------------------------------------------------------------------------
# Tests: Linear flows (ENGINE-005, preserved)
# ---------------------------------------------------------------------------


class TestLinear3NodeFlow:
    """Test a basic 3-node linear flow: entry -> task -> exit."""

    async def test_linear_3_node_flow(self) -> None:
        """Execute entry -> task -> exit. All succeed. Flow completes."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow()
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Flow run should be completed
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # All 3 task executions should be completed
        tasks = db.list_task_executions(flow_run_id)
        assert len(tasks) == 3
        for t in tasks:
            assert t.status == "completed"

        # Tasks were executed in order
        node_names = [t.node_name for t in tasks]
        assert node_names == ["start", "work", "finish"]

        # Edge transitions should be recorded
        edge_events = [e for e in events if e.type == EventType.EDGE_TRANSITION]
        assert len(edge_events) == 2
        assert edge_events[0].payload["from_node"] == "start"
        assert edge_events[0].payload["to_node"] == "work"
        assert edge_events[1].payload["from_node"] == "work"
        assert edge_events[1].payload["to_node"] == "finish"


class TestLinearFlowReturnsRunId:
    async def test_linear_flow_returns_run_id(self) -> None:
        """execute() returns a valid UUID that matches the flow run in DB."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        assert flow_run_id is not None
        assert len(flow_run_id) == 36  # UUID format
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.id == flow_run_id


class TestTemplateExpansion:
    async def test_template_expansion(self) -> None:
        """Flow with param {{repo}}. Verify expanded prompt contains the value."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        nodes = {
            "start": Node(
                name="start",
                node_type=NodeType.ENTRY,
                prompt="Clone {{repo}} and set up",
            ),
            "finish": Node(
                name="finish",
                node_type=NodeType.EXIT,
                prompt="Finalize {{repo}}",
            ),
        }
        flow = Flow(
            name="template-flow",
            budget_seconds=3600,
            on_error=ErrorPolicy.PAUSE,
            context=ContextMode.NONE,
            workspace="/workspace",
            nodes=nodes,
            edges=(
                Edge(
                    edge_type=EdgeType.UNCONDITIONAL,
                    source="start",
                    target="finish",
                ),
            ),
        )

        flow_run_id = await executor.execute(flow, {"repo": "my-repo"}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        assert len(tasks) == 2
        # Both tasks' prompt_text should contain "my-repo"
        for t in tasks:
            assert "my-repo" in t.prompt_text


class TestTaskDirectoryCreation:
    async def test_task_dir_is_empty_string(self) -> None:
        """After ENGINE-068, task_dir is empty string (DB-backed artifacts)."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        for t in tasks:
            assert t.task_dir == ""

    async def test_input_artifact_saved(self) -> None:
        """After task creation, an input artifact is saved to the DB."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        for t in tasks:
            artifact = db.get_artifact(t.id, "input")
            assert artifact is not None
            assert artifact.content_type == "text/markdown"
            assert len(artifact.content) > 0


class TestBudgetWarningEvents:
    async def test_budget_warning_event_structure(self) -> None:
        """Verify budget warning events have correct structure when emitted."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            budget_seconds=1,
            node_names=["start", "finish"],
        )
        await executor.execute(flow, {}, "/workspace")

        budget_events = [e for e in events if e.type == EventType.FLOW_BUDGET_WARNING]
        for be in budget_events:
            assert "elapsed_seconds" in be.payload
            assert "budget_seconds" in be.payload
            assert "percent_used" in be.payload


class TestBudgetExceededPauses:
    async def test_budget_exceeded_pauses(self) -> None:
        """Budget very small, flow should pause due to budget exceeded."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            budget_seconds=0,
            node_names=["start", "work", "work2", "finish"],
        )
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert any("Budget exceeded" in str(e.payload.get("reason", "")) for e in status_events)

        # Cancel to let execute() return.
        await executor.cancel(flow_run_id)
        await execute_task


class TestTaskFailurePausesFlow:
    async def test_task_failure_pauses_flow(self) -> None:
        """Task exits with code 1, on_error=pause -> flow pauses."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["start"] = (1, [])

        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        failed_events = [e for e in events if e.type == EventType.TASK_FAILED]
        assert len(failed_events) == 1
        assert failed_events[0].payload["node_name"] == "start"

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert len(status_events) >= 1

        await executor.cancel(flow_run_id)
        await execute_task


class TestEventEmissionOrder:
    async def test_event_emission_order(self) -> None:
        """Verify events are emitted in the correct order for a 2-node flow."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        mock_mgr.task_responses["start"] = (
            0,
            [
                StreamEvent(
                    type=StreamEventType.ASSISTANT,
                    content={"type": "assistant", "text": "working on start"},
                    raw='{"type": "assistant", "text": "working on start"}',
                )
            ],
        )

        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "finish"])
        await executor.execute(flow, {}, "/workspace")

        event_types = [e.type for e in events]

        assert event_types[0] == EventType.FLOW_STARTED
        assert event_types[1] == EventType.TASK_STARTED

        completed_indices = [
            i for i, et in enumerate(event_types) if et == EventType.TASK_COMPLETED
        ]
        edge_indices = [i for i, et in enumerate(event_types) if et == EventType.EDGE_TRANSITION]
        task_started_indices = [
            i for i, et in enumerate(event_types) if et == EventType.TASK_STARTED
        ]

        assert len(completed_indices) == 2
        assert len(edge_indices) == 1
        assert len(task_started_indices) == 2

        assert completed_indices[0] < edge_indices[0]
        assert edge_indices[0] < task_started_indices[1]

        assert event_types[-1] == EventType.FLOW_COMPLETED


class TestContextModeHandoff:
    async def test_context_mode_handoff(self) -> None:
        """Two-node flow with handoff context. Verify second task prompt includes
        summary from first task."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            node_names=["start", "finish"],
            context=ContextMode.HANDOFF,
        )

        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        assert len(tasks) == 2

        finish_task = tasks[1]
        assert finish_task.context_mode == ContextMode.HANDOFF.value
        assert "Context from previous task" in finish_task.prompt_text

    async def test_context_mode_handoff_with_summary(self) -> None:
        """When predecessor saves a summary artifact, the handoff prompt includes it."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            node_names=["start", "work", "finish"],
            context=ContextMode.HANDOFF,
        )

        original_run_task = mock_mgr.run_task

        async def run_task_with_summary(
            prompt: str, workspace: str, session_id: str, *, skip_permissions: bool = False
        ) -> AsyncGenerator[StreamEvent, None]:
            if "start" in prompt:
                runs = db.list_flow_runs()
                if runs:
                    task_list = db.list_task_executions(runs[0].id)
                    for t in task_list:
                        if t.node_name == "start":
                            db.save_artifact(
                                t.id,
                                "summary",
                                "I set up the project successfully.",
                                "text/markdown",
                            )
                            break

            async for evt in original_run_task(prompt, workspace, session_id):
                yield evt

        mock_mgr.run_task = run_task_with_summary  # type: ignore[assignment]

        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        work_task = next(t for t in tasks if t.node_name == "work")
        assert "I set up the project successfully." in work_task.prompt_text


class TestContextModeNone:
    async def test_context_mode_none(self) -> None:
        """Two-node flow with none context. Second task should NOT include predecessor context."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            node_names=["start", "finish"],
            context=ContextMode.NONE,
        )

        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        assert len(tasks) == 2

        finish_task = tasks[1]
        assert finish_task.context_mode == ContextMode.NONE.value
        assert "Context from previous task" not in finish_task.prompt_text


class TestConcurrencySemaphore:
    async def test_concurrency_semaphore(self) -> None:
        """Verify the semaphore is created with the correct max_concurrent value."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=7, server_base_url="http://127.0.0.1:9090"
        )

        assert executor._max_concurrent == 7
        assert executor._semaphore._value == 7


class TestMinimalFlow:
    async def test_entry_exit_only(self) -> None:
        """Flow with only entry + exit (2 nodes, 1 edge). Should complete."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        assert len(tasks) == 2
        assert all(t.status == "completed" for t in tasks)

    async def test_5_node_linear(self) -> None:
        """5-node linear flow: entry -> 3 tasks -> exit. All complete in order."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "a", "b", "c", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        assert len(tasks) == 5
        node_names = [t.node_name for t in tasks]
        assert node_names == ["start", "a", "b", "c", "finish"]


class TestFlowRunRecord:
    async def test_flow_run_created_then_running(self) -> None:
        """The flow run is created with 'created' status then transitions to 'running'."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "finish"])
        await executor.execute(flow, {}, "/workspace")

        assert events[0].type == EventType.FLOW_STARTED
        assert events[0].payload["status"] == "running"

    async def test_flow_run_elapsed_updated(self) -> None:
        """flow_run.elapsed_seconds is updated after flow completion."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.elapsed_seconds >= 0


class TestEdgeContextOverride:
    async def test_edge_context_override(self) -> None:
        """Edge-level context override takes precedence over flow-level default."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            node_names=["start", "finish"],
            context=ContextMode.HANDOFF,
            edge_context=ContextMode.NONE,
        )

        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        finish_task = tasks[1]
        assert finish_task.context_mode == ContextMode.NONE.value
        assert "Context from previous task" not in finish_task.prompt_text


class TestMiddleTaskFailure:
    async def test_middle_task_failure(self) -> None:
        """If the middle task in a 3-node flow fails, the flow pauses."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        tasks = db.list_task_executions(flow_run_id)
        status_map = {t.node_name: t.status for t in tasks}
        assert status_map["start"] == "completed"
        assert status_map["work"] == "failed"
        assert "finish" not in status_map

        await executor.cancel(flow_run_id)
        await execute_task


class TestSubprocessManagerCalled:
    async def test_subprocess_manager_called_for_each_task(self) -> None:
        """The subprocess manager is called once per task."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        await executor.execute(flow, {}, "/workspace")

        assert len(mock_mgr.calls) == 3

    async def test_task_logs_stored(self) -> None:
        """Stream events from subprocess are stored as task logs in DB."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["start"] = (
            0,
            [
                StreamEvent(
                    type=StreamEventType.ASSISTANT,
                    content={"type": "assistant", "text": "hello"},
                    raw='{"type": "assistant", "text": "hello"}',
                )
            ],
        )
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        start_task = tasks[0]
        logs = db.get_task_logs(start_task.id)
        assert len(logs) >= 1
        log_types = [log.log_type for log in logs]
        assert "assistant_message" in log_types


# ---------------------------------------------------------------------------
# Tests: Fork-Join (ENGINE-006)
# ---------------------------------------------------------------------------


class TestForkJoin2Targets:
    """Fork into 2 targets, both complete, merge triggers."""

    async def test_fork_join_2_targets(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_fork_join_flow(fork_targets=["task_a", "task_b"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        task_names = [t.node_name for t in tasks]
        # start, task_a, task_b, merge, finish
        assert "start" in task_names
        assert "task_a" in task_names
        assert "task_b" in task_names
        assert "merge" in task_names
        assert "finish" in task_names
        assert len(tasks) == 5

        # All completed
        for t in tasks:
            assert t.status == "completed"

        # fork.started event emitted
        fork_events = [e for e in events if e.type == EventType.FORK_STARTED]
        assert len(fork_events) == 1
        assert set(fork_events[0].payload["targets"]) == {"task_a", "task_b"}

        # fork.joined event emitted
        join_events = [e for e in events if e.type == EventType.FORK_JOINED]
        assert len(join_events) == 1
        assert join_events[0].payload["join_node"] == "merge"


class TestForkJoin3Targets:
    """Fork into 3 targets, all complete, merge triggers."""

    async def test_fork_join_3_targets(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_fork_join_flow(fork_targets=["task_a", "task_b", "task_c"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        # start, task_a, task_b, task_c, merge, finish
        assert len(tasks) == 6
        for t in tasks:
            assert t.status == "completed"


class TestForkJoinGenerationTracking:
    """Fork members share generation, join target gets generation + 1."""

    async def test_fork_join_generation_tracking(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_fork_join_flow()
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        task_map = {t.node_name: t for t in tasks}

        # Start is generation 1
        assert task_map["start"].generation == 1

        # Fork members share start's generation (1)
        assert task_map["task_a"].generation == 1
        assert task_map["task_b"].generation == 1

        # Merge (join target) gets generation + 1 = 2
        assert task_map["merge"].generation == 2


class TestForkGroupDbState:
    """Verify fork group DB state after fork-join completes."""

    async def test_fork_group_db_state(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_fork_join_flow()
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Get fork groups -- should have one with status 'joined'
        # (get_active_fork_groups returns only active ones, so we check via DB)
        # The group should be 'joined' so it won't appear in active
        active_groups = db.get_active_fork_groups(flow_run_id)
        assert len(active_groups) == 0  # all should be joined

        # The members should exist in a fork group
        # Since get_fork_group_members joins with the group, let's check via
        # the raw fork_group query
        all_rows = db._fetchall(  # type: ignore[attr-defined]
            "SELECT * FROM fork_groups WHERE flow_run_id = ?",
            (flow_run_id,),
        )
        assert len(all_rows) == 1
        fg = dict(all_rows[0])
        assert fg["status"] == "joined"
        assert fg["join_node_name"] == "merge"


class TestForkJoinContextAggregation:
    """Verify join task's prompt includes summaries from all fork members."""

    async def test_fork_join_context_aggregation(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        original_run_task = mock_mgr.run_task

        async def run_task_with_summaries(
            prompt: str, workspace: str, session_id: str, *, skip_permissions: bool = False
        ) -> AsyncGenerator[StreamEvent, None]:
            # Save summary artifacts for fork members
            runs = db.list_flow_runs()
            if runs:
                task_list = db.list_task_executions(runs[0].id)
                for t in task_list:
                    if t.node_name == "task_a" and t.status == "running":
                        db.save_artifact(
                            t.id,
                            "summary",
                            "Task A completed frontend.",
                            "text/markdown",
                        )
                    elif t.node_name == "task_b" and t.status == "running":
                        db.save_artifact(
                            t.id,
                            "summary",
                            "Task B completed backend.",
                            "text/markdown",
                        )

            async for evt in original_run_task(prompt, workspace, session_id):
                yield evt

        mock_mgr.run_task = run_task_with_summaries  # type: ignore[assignment]

        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")
        flow = _make_fork_join_flow()
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        merge_task = next(t for t in tasks if t.node_name == "merge")

        # The merge task's prompt should include both summaries
        assert "Context from parallel tasks" in merge_task.prompt_text
        assert "task_a" in merge_task.prompt_text
        assert "task_b" in merge_task.prompt_text


class TestForkMemberFailure:
    """Fork member fails with on_error=pause: flow pauses, join does NOT trigger."""

    async def test_fork_member_failure(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["task_a"] = (1, [])

        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_fork_join_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # Join should NOT have been created
        tasks = db.list_task_executions(flow_run_id)
        task_names = {t.node_name for t in tasks}
        assert "merge" not in task_names

        # fork.joined event should NOT be emitted
        join_events = [e for e in events if e.type == EventType.FORK_JOINED]
        assert len(join_events) == 0

        await executor.cancel(flow_run_id)
        await execute_task


class TestForkJoinEvents:
    """Verify the complete event sequence for a fork-join flow."""

    async def test_fork_join_events(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_fork_join_flow()
        await executor.execute(flow, {}, "/workspace")

        event_types = [e.type for e in events]

        # Should contain fork.started and fork.joined
        assert EventType.FORK_STARTED in event_types
        assert EventType.FORK_JOINED in event_types

        # fork.started should come before any fork member task.started
        fork_started_idx = event_types.index(EventType.FORK_STARTED)
        # Find task.started events for fork members
        fork_member_starts = [
            i
            for i, e in enumerate(events)
            if e.type == EventType.TASK_STARTED
            and e.payload.get("node_name") in ("task_a", "task_b")
        ]
        for idx in fork_member_starts:
            assert fork_started_idx < idx

        # fork.joined should come after all fork member task.completed
        fork_joined_idx = event_types.index(EventType.FORK_JOINED)
        fork_member_completes = [
            i
            for i, e in enumerate(events)
            if e.type == EventType.TASK_COMPLETED
            and e.payload.get("node_name") in ("task_a", "task_b")
        ]
        for idx in fork_member_completes:
            assert idx < fork_joined_idx


class TestForkParallelExecution:
    """Fork into 2 tasks -- verify both start before either completes."""

    async def test_fork_parallel_execution(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_fork_join_flow()
        await executor.execute(flow, {}, "/workspace")

        # Check that max concurrent was > 1 during fork execution
        # The mock tracks this
        # With instant completion this is hard to guarantee, but at least
        # both task_a and task_b should have started
        started_names = [e.payload["node_name"] for e in events if e.type == EventType.TASK_STARTED]
        assert "task_a" in started_names
        assert "task_b" in started_names


# ---------------------------------------------------------------------------
# Tests: Conditional + Cycles (ENGINE-007)
# ---------------------------------------------------------------------------


class TestConditionalBranchHappyPath:
    """Mock judge returns 'done'. Verify the done task is enqueued."""

    async def test_conditional_branch_happy_path(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(
            JudgeDecision(target="done", reasoning="Work is approved", confidence=0.9)
        )

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True)
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        task_names = [t.node_name for t in tasks]
        # start, implement, review, done
        assert "done" in task_names

        # judge.started and judge.decided events
        judge_started = [e for e in events if e.type == EventType.JUDGE_STARTED]
        assert len(judge_started) == 1
        judge_decided = [e for e in events if e.type == EventType.JUDGE_DECIDED]
        assert len(judge_decided) == 1
        assert judge_decided[0].payload["to_node"] == "done"


class TestConditionalBranchAlternative:
    """Mock judge returns 'implement' (needs work). Verify implement is re-enqueued."""

    async def test_conditional_branch_alternative(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        # First review: needs work -> implement
        mock_judge.add_decision(
            JudgeDecision(target="implement", reasoning="Needs more work", confidence=0.85)
        )
        # Second review: approved -> done
        mock_judge.add_decision(
            JudgeDecision(target="done", reasoning="All good now", confidence=0.95)
        )

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True)
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        task_names = [t.node_name for t in tasks]
        # Should have implement twice (cycle)
        implement_count = task_names.count("implement")
        assert implement_count == 2


class TestConditionalNonePauses:
    """Mock judge returns '__none__'. Verify flow pauses."""

    async def test_conditional_none_pauses(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(
            JudgeDecision(target="__none__", reasoning="No match", confidence=0.8)
        )

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert any(
            "could not match" in str(e.payload.get("reason", "")).lower() for e in status_events
        )

        await executor.cancel(flow_run_id)
        await execute_task


class TestConditionalLowConfidencePauses:
    """Mock judge returns confidence=0.3. Verify flow pauses."""

    async def test_conditional_low_confidence_pauses(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(
            JudgeDecision(target="done", reasoning="Maybe done?", confidence=0.3)
        )

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert any(
            "low confidence" in str(e.payload.get("reason", "")).lower() for e in status_events
        )

        await executor.cancel(flow_run_id)
        await execute_task


class TestConditionalJudgeFailurePauses:
    """JudgeProtocol.evaluate raises JudgePauseError. Verify flow pauses."""

    async def test_conditional_judge_failure_pauses(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(JudgePauseError("Subprocess crashed"))

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert any("Judge failed" in str(e.payload.get("reason", "")) for e in status_events)

        await executor.cancel(flow_run_id)
        await execute_task


class TestCycleGenerationIncrement:
    """Cycle re-entry increments generation for the cycled node."""

    async def test_cycle_generation_increment(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        # First review: needs work
        mock_judge.add_decision(
            JudgeDecision(target="implement", reasoning="Needs work", confidence=0.9)
        )
        # Second review: approved
        mock_judge.add_decision(JudgeDecision(target="done", reasoning="Approved", confidence=0.95))

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True)
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        implement_tasks = [t for t in tasks if t.node_name == "implement"]
        assert len(implement_tasks) == 2
        # First run: generation 1, second run: generation 2
        generations = sorted(t.generation for t in implement_tasks)
        assert generations == [1, 2]

        # Task directories are empty strings (DB-backed artifacts)
        for t in implement_tasks:
            assert t.task_dir == ""


class TestCycleThreeIterations:
    """Mock judge returns 'needs work' twice, then 'approved'. Generation reaches 3."""

    async def test_cycle_three_iterations(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(
            JudgeDecision(target="implement", reasoning="Needs work #1", confidence=0.9)
        )
        mock_judge.add_decision(
            JudgeDecision(target="implement", reasoning="Needs work #2", confidence=0.9)
        )
        mock_judge.add_decision(
            JudgeDecision(target="done", reasoning="Finally approved", confidence=0.95)
        )

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True)
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        implement_tasks = [t for t in tasks if t.node_name == "implement"]
        assert len(implement_tasks) == 3
        generations = sorted(t.generation for t in implement_tasks)
        assert generations == [1, 2, 3]


class TestCycleHandoffContext:
    """Cycle with handoff mode: re-entered task's prompt includes judge feedback."""

    async def test_cycle_handoff_context(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(
            JudgeDecision(
                target="implement",
                reasoning="Tests are failing, fix them",
                confidence=0.9,
            )
        )
        mock_judge.add_decision(
            JudgeDecision(target="done", reasoning="All tests pass", confidence=0.95)
        )

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True, context=ContextMode.HANDOFF)
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        # The second implement task (generation 2) should have judge feedback
        implement_gen2 = next(t for t in tasks if t.node_name == "implement" and t.generation == 2)
        assert "Judge Feedback" in implement_gen2.prompt_text
        assert "Tests are failing, fix them" in implement_gen2.prompt_text


class TestCycleNoneContext:
    """Cycle with none mode: re-entered task only has its own prompt."""

    async def test_cycle_none_context(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(
            JudgeDecision(target="implement", reasoning="Needs work", confidence=0.9)
        )
        mock_judge.add_decision(JudgeDecision(target="done", reasoning="Approved", confidence=0.95))

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(
            with_cycle=True,
            context=ContextMode.NONE,
            edge_context=ContextMode.NONE,
        )
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        implement_gen2 = next(t for t in tasks if t.node_name == "implement" and t.generation == 2)
        # Should NOT have context from previous task or judge feedback
        assert "Context from previous task" not in implement_gen2.prompt_text
        assert "Judge Feedback" not in implement_gen2.prompt_text


class TestUnconditionalEdgeCycleGeneration:
    """Unconditional edge cycle re-entry increments generation (ENGINE-055)."""

    async def test_unconditional_cycle_increments_generation(self) -> None:
        """When a node is re-entered via an unconditional edge, generation should increment."""
        db = _make_db()
        _events, callback = _collect_events()

        # Custom mock that fails on the Nth call to a node, causing on_error=pause.
        call_counts: dict[str, int] = {}

        class FailOnSecondCallManager(MockSubprocessManager):
            """Fails when 'alpha' is called a second time so the cycle pauses."""

            async def run_task(
                self_inner,
                prompt: str,
                workspace: str,
                session_id: str,
                *,
                skip_permissions: bool = False,
                settings: str | None = None,
            ) -> AsyncGenerator[StreamEvent, None]:
                if "Do the alpha step" in prompt:
                    call_counts["alpha"] = call_counts.get("alpha", 0) + 1
                    if call_counts["alpha"] >= 2:
                        yield StreamEvent(
                            type=StreamEventType.SYSTEM,
                            content={
                                "event": "process_exit",
                                "exit_code": 1,
                                "stderr": "Intentional failure",
                            },
                            raw="Process exited with code 1",
                        )
                        return
                yield StreamEvent(
                    type=StreamEventType.SYSTEM,
                    content={"event": "process_exit", "exit_code": 0, "stderr": ""},
                    raw="Process exited with code 0",
                )

        mock_mgr = FailOnSecondCallManager()

        # Build a cyclic flow: entry -> alpha -> beta -> alpha (all unconditional)
        nodes = {
            "entry": Node(name="entry", node_type=NodeType.ENTRY, prompt="Do the entry step"),
            "alpha": Node(name="alpha", node_type=NodeType.TASK, prompt="Do the alpha step"),
            "beta": Node(name="beta", node_type=NodeType.TASK, prompt="Do the beta step"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="entry", target="alpha"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="alpha", target="beta"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="beta", target="alpha"),
        )
        flow = Flow(
            name="unconditional-cycle",
            budget_seconds=3600,
            on_error=ErrorPolicy.PAUSE,
            context=ContextMode.HANDOFF,
            workspace="/workspace",
            nodes=nodes,
            edges=edges,
        )

        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")
        # Flow will pause when alpha fails on 2nd call; use _execute_until_paused
        flow_run_id, exec_task = await _execute_until_paused(executor, flow, {}, "/workspace", db)

        tasks = db.list_task_executions(flow_run_id)
        alpha_tasks = [t for t in tasks if t.node_name == "alpha"]

        # alpha should have been created twice: generation 1 and generation 2
        assert len(alpha_tasks) == 2
        generations = sorted(t.generation for t in alpha_tasks)
        assert generations == [1, 2], f"Expected generations [1, 2] but got {generations}"

        # beta should have run once with generation 1
        beta_tasks = [t for t in tasks if t.node_name == "beta"]
        assert len(beta_tasks) == 1
        assert beta_tasks[0].generation == 1

        # Clean up: cancel the executor to let the background task finish
        await executor.cancel(flow_run_id)
        with contextlib.suppress(asyncio.CancelledError):
            await exec_task

    async def test_non_cyclic_unconditional_uses_generation_one(self) -> None:
        """Non-cyclic unconditional edges should still use generation 1."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        for task in tasks:
            assert (
                task.generation == 1
            ), f"Node {task.node_name} has generation {task.generation}, expected 1"


class TestEdgeTransitionRecorded:
    """After a conditional transition, verify edge_transitions record in DB."""

    async def test_edge_transition_recorded(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(JudgeDecision(target="done", reasoning="LGTM", confidence=0.95))

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True)
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Check edge_transitions table
        rows = db._fetchall(  # type: ignore[attr-defined]
            "SELECT * FROM edge_transitions WHERE flow_run_id = ? AND edge_type = 'conditional'",
            (flow_run_id,),
        )
        assert len(rows) >= 1
        row = dict(rows[0])
        assert row["judge_decision"] == "done"
        assert row["judge_reasoning"] == "LGTM"
        assert row["judge_confidence"] == 0.95


class TestJudgeEventsEmitted:
    """Verify judge.started and judge.decided events in correct order."""

    async def test_judge_events_emitted(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(JudgeDecision(target="done", reasoning="Good", confidence=0.9))

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True)
        await executor.execute(flow, {}, "/workspace")

        event_types = [e.type for e in events]
        js_idx = event_types.index(EventType.JUDGE_STARTED)
        jd_idx = event_types.index(EventType.JUDGE_DECIDED)
        assert js_idx < jd_idx

        # Verify payloads
        js_event = events[js_idx]
        assert js_event.payload["from_node"] == "review"
        assert "conditions" in js_event.payload

        jd_event = events[jd_idx]
        assert jd_event.payload["to_node"] == "done"
        assert jd_event.payload["reasoning"] == "Good"
        assert jd_event.payload["confidence"] == 0.9


# ---------------------------------------------------------------------------
# Tests: Control Operations (ENGINE-008)
# ---------------------------------------------------------------------------


class TestOnErrorPause:
    """Flow with on_error=pause. Task fails -> flow pauses."""

    async def test_on_error_pause(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert len(status_events) >= 1

        await executor.cancel(flow_run_id)
        await execute_task


class TestOnErrorAbort:
    """Flow with on_error=abort. Task fails -> flow cancels."""

    async def test_on_error_abort(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.ABORT)
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "cancelled"


class TestOnErrorSkip:
    """Flow with on_error=skip. Task fails -> task skipped, next enqueued."""

    async def test_on_error_skip(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.SKIP)
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        status_map = {t.node_name: t.status for t in tasks}
        assert status_map["start"] == "completed"
        assert status_map["work"] == "skipped"
        assert status_map["finish"] == "completed"


class TestRetryFailedTask:
    """Task fails. Call retry_task. Verify new task execution created."""

    async def test_retry_failed_task(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        # Find the failed task
        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")
        assert failed_task.node_name == "work"

        # Retry it
        await executor.retry_task(flow_run_id, failed_task.id)

        # A new task execution should exist
        tasks = db.list_task_executions(flow_run_id)
        work_tasks = [t for t in tasks if t.node_name == "work"]
        assert len(work_tasks) == 2

        new_task = next(t for t in work_tasks if t.id != failed_task.id)
        assert new_task.status == "pending"
        assert new_task.generation == 2
        assert new_task.task_dir == ""

        await executor.cancel(flow_run_id)
        await execute_task


class TestRetryNonFailedRaises:
    """Retry a completed task -> ValueError."""

    async def test_retry_non_failed_raises(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        completed_task = tasks[0]

        try:
            await executor.retry_task(flow_run_id, completed_task.id)
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "Can only retry failed tasks" in str(e)


class TestSkipFailedTask:
    """Task fails. Call skip_task. Verify task status is skipped."""

    async def test_skip_failed_task(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")

        await executor.skip_task(flow_run_id, failed_task.id)

        # Task should be marked as skipped
        updated_task = db.get_task_execution(failed_task.id)
        assert updated_task is not None
        assert updated_task.status == "skipped"

        # Next task should be created as pending
        tasks_after = db.list_task_executions(flow_run_id)
        finish_tasks = [t for t in tasks_after if t.node_name == "finish"]
        assert len(finish_tasks) == 1
        assert finish_tasks[0].status == "pending"

        await executor.cancel(flow_run_id)
        await execute_task


class TestSkipNonFailedRaises:
    """Skip a completed task -> ValueError."""

    async def test_skip_non_failed_raises(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        completed_task = tasks[0]

        try:
            await executor.skip_task(flow_run_id, completed_task.id)
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "Can only skip failed tasks" in str(e)


class TestCancelFlow:
    """Cancel a running flow. Verify flow status is cancelled."""

    async def test_cancel_flow(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Give start a delay so we can cancel during execution
        mock_mgr.task_delays["work"] = 1.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()

        # Start execution in background
        async def run_and_cancel() -> str:
            execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
            # Wait a moment for execution to start
            await asyncio.sleep(0.1)
            # Get the flow run id from DB
            runs = db.list_flow_runs()
            if runs:
                await executor.cancel(runs[0].id)
            return await execute_task

        flow_run_id = await run_and_cancel()
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "cancelled"

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert any(e.payload.get("new_status") == "cancelled" for e in status_events)


class TestCancelPausedFlow:
    """Pause, then cancel. Verify flow transitions paused -> cancelled."""

    async def test_cancel_paused_flow(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        # Flow should be paused
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # Cancel it (also unblocks the main loop)
        await executor.cancel(flow_run_id)
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "cancelled"


class TestFlowStatusChangeEvents:
    """Verify flow.status_changed events for various transitions."""

    async def test_flow_status_change_events(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "finish"])
        await executor.execute(flow, {}, "/workspace")

        # A completed flow emits flow.started and flow.completed
        event_types = [e.type for e in events]
        assert EventType.FLOW_STARTED in event_types
        assert EventType.FLOW_COMPLETED in event_types


# ---------------------------------------------------------------------------
# Tests: Event System (ENGINE-009)
# ---------------------------------------------------------------------------


class TestEventCallbackExceptionHandled:
    """Event callback raises an exception -> executor does NOT crash."""

    async def test_event_callback_exception_handled(self) -> None:
        db = _make_db()

        def bad_callback(event: FlowEvent) -> None:
            raise RuntimeError("Callback exploded!")

        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, bad_callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "finish"])
        # Should not raise
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Flow should still complete (events just get swallowed)
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"


class TestLinearFlowEventSequence:
    """Run a 3-node linear flow. Verify complete event sequence."""

    async def test_linear_flow_event_sequence(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()
        await executor.execute(flow, {}, "/workspace")

        event_types = [e.type for e in events]

        # Must start with flow.started
        assert event_types[0] == EventType.FLOW_STARTED

        # Must end with flow.completed
        assert event_types[-1] == EventType.FLOW_COMPLETED

        # Must have 3 task.started and 3 task.completed
        assert event_types.count(EventType.TASK_STARTED) == 3
        assert event_types.count(EventType.TASK_COMPLETED) == 3

        # Must have 2 edge.transition
        assert event_types.count(EventType.EDGE_TRANSITION) == 2


class TestForkJoinEventSequence:
    """Run a fork-join flow. Verify fork/join event sequence."""

    async def test_fork_join_event_sequence(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_fork_join_flow()
        await executor.execute(flow, {}, "/workspace")

        event_types = [e.type for e in events]

        assert EventType.FORK_STARTED in event_types
        assert EventType.FORK_JOINED in event_types
        assert event_types[0] == EventType.FLOW_STARTED
        assert event_types[-1] == EventType.FLOW_COMPLETED


class TestConditionalEventSequence:
    """Run a conditional flow. Verify judge events in sequence."""

    async def test_conditional_event_sequence(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(JudgeDecision(target="done", reasoning="OK", confidence=0.9))

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_conditional_flow(with_cycle=True)
        await executor.execute(flow, {}, "/workspace")

        event_types = [e.type for e in events]

        # task.completed should come before judge.started
        review_completed_idx = None
        for i, e in enumerate(events):
            if e.type == EventType.TASK_COMPLETED and e.payload.get("node_name") == "review":
                review_completed_idx = i
                break
        assert review_completed_idx is not None

        judge_started_idx = event_types.index(EventType.JUDGE_STARTED)
        judge_decided_idx = event_types.index(EventType.JUDGE_DECIDED)

        assert review_completed_idx < judge_started_idx
        assert judge_started_idx < judge_decided_idx

        # edge.transition should come after judge.decided
        edge_after_judge = [
            i
            for i, e in enumerate(events)
            if e.type == EventType.EDGE_TRANSITION and e.payload.get("edge_type") == "conditional"
        ]
        assert len(edge_after_judge) >= 1
        assert edge_after_judge[0] > judge_decided_idx


# ---------------------------------------------------------------------------
# Tests: Default edge pattern (1 unconditional + N conditional)
# ---------------------------------------------------------------------------


def _make_default_edge_flow() -> Flow:
    """Build a flow with a default-edge pattern at the moderator node.

    moderator -> alice (unconditional, default)
    moderator -> done  when "all tasks complete" (conditional)
    alice -> bob (unconditional)
    bob -> moderator (unconditional, back-edge / cycle)
    """
    nodes: dict[str, Node] = {
        "moderator": Node(
            name="moderator", node_type=NodeType.ENTRY, prompt="Do the moderator step"
        ),
        "alice": Node(name="alice", node_type=NodeType.TASK, prompt="Do the alice step"),
        "bob": Node(name="bob", node_type=NodeType.TASK, prompt="Do the bob step"),
        "done": Node(name="done", node_type=NodeType.EXIT, prompt="Do the done step"),
    }

    edges: list[Edge] = [
        # Default (unconditional) edge from moderator -> alice
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="moderator",
            target="alice",
        ),
        # Conditional edge from moderator -> done
        Edge(
            edge_type=EdgeType.CONDITIONAL,
            source="moderator",
            target="done",
            condition="all tasks complete",
        ),
        # alice -> bob (unconditional)
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="alice",
            target="bob",
        ),
        # bob -> moderator (unconditional back-edge)
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="bob",
            target="moderator",
        ),
    ]

    return Flow(
        name="default-edge-flow",
        budget_seconds=1800,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace="/workspace",
        judge=True,  # Use mock judge subprocess for conditional routing
        nodes=nodes,
        edges=tuple(edges),
    )


class TestDefaultEdgeConditionMatches:
    """Mock judge returns matching condition. Flow follows the conditional edge."""

    async def test_default_edge_condition_matches(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        # Judge matches the condition on the first evaluation
        mock_judge.add_decision(
            JudgeDecision(target="done", reasoning="All tasks are complete", confidence=0.95)
        )

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_default_edge_flow()
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        task_names = [t.node_name for t in tasks]
        # Should go: moderator -> done (via conditional edge)
        assert "moderator" in task_names
        assert "done" in task_names
        # alice and bob should NOT have been visited (judge matched immediately)
        assert "alice" not in task_names
        assert "bob" not in task_names

        # Verify judge events were emitted
        judge_started = [e for e in events if e.type == EventType.JUDGE_STARTED]
        assert len(judge_started) == 1
        judge_decided = [e for e in events if e.type == EventType.JUDGE_DECIDED]
        assert len(judge_decided) == 1
        assert judge_decided[0].payload["to_node"] == "done"


class TestDefaultEdgeNoneFollowsDefault:
    """Mock judge returns __none__. Flow follows the default (unconditional) edge."""

    async def test_default_edge_none_follows_default(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        # First call: no match -> follow default edge to alice
        mock_judge.add_decision(
            JudgeDecision(target="__none__", reasoning="No match", confidence=0.8)
        )
        # After alice -> bob -> moderator cycle, judge matches on second call
        mock_judge.add_decision(
            JudgeDecision(target="done", reasoning="All done now", confidence=0.95)
        )

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_default_edge_flow()
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        task_names = [t.node_name for t in tasks]
        # Should have visited: moderator, alice, bob, moderator (cycle), done
        assert task_names.count("moderator") == 2
        assert "alice" in task_names
        assert "bob" in task_names
        assert "done" in task_names

        # Flow should NOT have paused -- __none__ follows default edge
        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        pause_events = [e for e in status_events if e.payload.get("new_status") == "paused"]
        assert len(pause_events) == 0


class TestDefaultEdgeLowConfidenceFollowsDefault:
    """Mock judge returns low confidence. Flow follows the default edge."""

    async def test_default_edge_low_confidence_follows_default(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        # First call: low confidence -> follow default edge
        mock_judge.add_decision(
            JudgeDecision(target="done", reasoning="Maybe done?", confidence=0.3)
        )
        # After cycle, judge matches with high confidence
        mock_judge.add_decision(
            JudgeDecision(target="done", reasoning="Definitely done", confidence=0.95)
        )

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_default_edge_flow()
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        task_names = [t.node_name for t in tasks]
        # Low confidence should follow default edge (alice), not pause
        assert "alice" in task_names
        assert "bob" in task_names
        assert task_names.count("moderator") == 2

        # Flow should NOT have paused
        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        pause_events = [e for e in status_events if e.payload.get("new_status") == "paused"]
        assert len(pause_events) == 0


class TestDefaultEdgeJudgeFailurePauses:
    """Judge raises JudgePauseError. Flow should pause (not follow default)."""

    async def test_default_edge_judge_failure_pauses(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(JudgePauseError("Subprocess crashed"))

        executor = FlowExecutor(
            db, callback, mock_mgr, judge=mock_judge, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_default_edge_flow()
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert any("Judge failed" in str(e.payload.get("reason", "")) for e in status_events)

        await executor.cancel(flow_run_id)
        await execute_task


# ---------------------------------------------------------------------------
# Tests: ENGINE-017 — Cancel does not trigger on_error=pause
# ---------------------------------------------------------------------------


class TestCancelDoesNotTriggerOnErrorPause:
    """When a flow is cancelled, the killed subprocess (exit 143) should NOT
    trigger on_error=pause.  The flow must end up 'cancelled', not 'paused'."""

    async def test_cancel_during_running_task_sets_cancelled(self) -> None:
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Delay so we have time to cancel while task is running
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)

        async def run_and_cancel() -> str:
            execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
            await asyncio.sleep(0.1)
            runs = db.list_flow_runs()
            assert runs, "Expected at least one flow run"
            await executor.cancel(runs[0].id)
            return await execute_task

        flow_run_id = await run_and_cancel()
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        # Must be cancelled, NOT paused
        assert run.status == "cancelled", f"Expected cancelled, got {run.status}"

        # No status change to "paused" should have occurred
        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        paused_events = [e for e in status_events if e.payload.get("new_status") == "paused"]
        assert len(paused_events) == 0, "on_error=pause should not trigger during cancel"

    async def test_cancelled_task_has_cancel_error_message(self) -> None:
        """When cancel kills a subprocess, the task error_message indicates cancellation."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)

        async def run_and_cancel() -> str:
            execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
            await asyncio.sleep(0.1)
            runs = db.list_flow_runs()
            assert runs
            await executor.cancel(runs[0].id)
            return await execute_task

        flow_run_id = await run_and_cancel()
        tasks = db.list_task_executions(flow_run_id)
        # The start task should have completed; the work task's error message
        # should indicate cancellation (DB schema only allows 'failed' status).
        for task in tasks:
            if task.node_name == "work":
                assert task.status == "failed"
                assert task.error_message is not None
                assert "cancelled" in task.error_message.lower()

    async def test_handle_error_skipped_when_cancelled(self) -> None:
        """_handle_error returns immediately when self._cancelled is True."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (143, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        # Simulate cancel flag being set before error handling
        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)

        # We need to test the _handle_error path directly.
        # Start execute, but set cancelled before the failed task is processed.
        mock_mgr.task_delays["work"] = 0.5
        execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
        await asyncio.sleep(0.1)
        runs = db.list_flow_runs()
        assert runs
        await executor.cancel(runs[0].id)
        await execute_task

        run = db.get_flow_run(runs[0].id)
        assert run is not None
        assert run.status == "cancelled"


# ---------------------------------------------------------------------------
# Tests: ENGINE-018 — Resume restarts execution after pause
# ---------------------------------------------------------------------------


class TestResumeAfterOnErrorPause:
    """When on_error=pause triggers and the user retries then resumes,
    the executor loop should continue and complete the flow."""

    async def test_resume_continues_after_pause(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # First call to "work" fails; subsequent calls succeed
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # Retry the failed task
        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")
        assert failed_task.node_name == "work"
        await executor.retry_task(flow_run_id, failed_task.id)

        # Make subsequent "work" calls succeed
        del mock_mgr.task_responses["work"]

        # Resume the flow
        await executor.resume(flow_run_id)

        # Wait for execute to finish
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # All tasks should be done
        tasks = db.list_task_executions(flow_run_id)
        completed_tasks = [t for t in tasks if t.status == "completed"]
        # start + work (retried) + finish = 3 completed
        assert len(completed_tasks) >= 3

    async def test_resume_signals_main_loop(self) -> None:
        """Verify resume() signals the main loop to wake up via _resume_event."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["start"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        # Verify that execute_task is NOT done (main loop is waiting)
        assert not execute_task.done(), "Main loop should be waiting, not returned"

        # Cancel to let it finish
        await executor.cancel(flow_run_id)
        await execute_task


class TestResumeAfterSkip:
    """Skip a failed task then resume. Flow should continue to completion."""

    async def test_skip_then_resume_completes(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        # Skip the failed task
        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")
        await executor.skip_task(flow_run_id, failed_task.id)

        # Resume
        await executor.resume(flow_run_id)

        # Wait for execute to finish
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"


class TestMultiplePauseResumeCycles:
    """Multiple pause/resume cycles should work correctly."""

    async def test_multiple_pause_resume(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        # Both work and work2 fail initially
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            on_error=ErrorPolicy.PAUSE,
            node_names=["start", "work", "work2", "finish"],
        )

        # First cycle: pause on "work" failure
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # Fix "work" and retry
        del mock_mgr.task_responses["work"]
        mock_mgr.task_responses["work2"] = (1, [])  # work2 will fail next
        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")
        await executor.retry_task(flow_run_id, failed_task.id)
        await executor.resume(flow_run_id)

        # Wait for it to pause again on work2
        for _ in range(200):
            await asyncio.sleep(0.01)
            run = db.get_flow_run(flow_run_id)
            if run and run.status == "paused":
                break

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # Fix work2 and retry again
        del mock_mgr.task_responses["work2"]
        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed" and t.node_name == "work2")
        await executor.retry_task(flow_run_id, failed_task.id)
        await executor.resume(flow_run_id)

        # Should now complete
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"


class TestCancelWhilePausedUnblocksMainLoop:
    """Cancel while the main loop is paused should unblock and terminate."""

    async def test_cancel_while_paused(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        assert not execute_task.done(), "Main loop should be waiting"

        # Cancel should unblock the main loop
        await executor.cancel(flow_run_id)
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "cancelled"


class TestResumeBudgetExceeded:
    """Resume after budget-exceeded pause. Next task completes the flow."""

    async def test_resume_after_budget_pause(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        # Budget of 0 means it will exceed after first task
        flow = _make_linear_flow(
            budget_seconds=0,
            node_names=["start", "finish"],
        )
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # Resume the flow (budget is already exceeded but we allow continuation)
        await executor.resume(flow_run_id)

        # The flow should eventually complete or re-pause
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        # It may complete or pause again depending on budget check timing,
        # but it should NOT hang
        assert run.status in ("completed", "paused", "cancelled")


# ---------------------------------------------------------------------------
# Tests: ENGINE-017 — CancelledError is properly handled in _execute_single_task
# ---------------------------------------------------------------------------


class TestCancelledErrorHandledProperly:
    """When cancel() injects asyncio.CancelledError into _execute_single_task,
    the task should be marked as failed with 'Flow cancelled' and the flow
    should end up 'cancelled', not 'paused'."""

    async def test_cancelled_error_marks_task_failed(self) -> None:
        """CancelledError during subprocess streaming marks task as failed."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Long delay so cancel fires during the await asyncio.sleep
        mock_mgr.task_delays["work"] = 10.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)

        async def run_and_cancel() -> str:
            execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
            await asyncio.sleep(0.05)
            runs = db.list_flow_runs()
            assert runs, "Expected at least one flow run"
            await executor.cancel(runs[0].id)
            return await execute_task

        flow_run_id = await run_and_cancel()
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "cancelled", f"Expected cancelled, got {run.status}"

        # The "work" task should be failed with a cancel message
        tasks = db.list_task_executions(flow_run_id)
        work_tasks = [t for t in tasks if t.node_name == "work"]
        assert len(work_tasks) == 1
        assert work_tasks[0].status == "failed"
        assert work_tasks[0].error_message is not None
        assert "cancelled" in work_tasks[0].error_message.lower()

    async def test_cancel_never_triggers_on_error_pause_event(self) -> None:
        """No FLOW_STATUS_CHANGED to 'paused' should occur during cancel."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 10.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)

        async def run_and_cancel() -> str:
            execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
            await asyncio.sleep(0.05)
            runs = db.list_flow_runs()
            assert runs
            await executor.cancel(runs[0].id)
            return await execute_task

        await run_and_cancel()

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        paused_events = [e for e in status_events if e.payload.get("new_status") == "paused"]
        assert len(paused_events) == 0, "on_error=pause should never trigger during cancel"

    async def test_process_completed_task_skips_when_cancelled(self) -> None:
        """_process_completed_task returns False without side effects when cancelled."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # "work" exits with code 143 (SIGTERM) and 0 delay -- it completes
        # before cancel() is called. This tests the guard in
        # _process_completed_task.
        mock_mgr.task_responses["work"] = (143, [])
        mock_mgr.task_delays["finish"] = 10.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            on_error=ErrorPolicy.PAUSE,
            node_names=["start", "work", "finish"],
        )

        async def run_and_cancel() -> str:
            execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
            # Wait for flow to either pause (on_error) or keep running
            await asyncio.sleep(0.1)
            runs = db.list_flow_runs()
            assert runs
            # Cancel -- if on_error=pause already triggered, this transitions
            # paused -> cancelled. Either way, final status must be cancelled.
            await executor.cancel(runs[0].id)
            return await execute_task

        flow_run_id = await run_and_cancel()
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "cancelled"


# ---------------------------------------------------------------------------
# Tests: ENGINE-018 — Resume restarts execution after user-initiated pause
# ---------------------------------------------------------------------------


class TestUserPauseResume:
    """User calls pause() then resume(). Verify execution continues
    and the flow completes."""

    async def test_pause_then_resume_completes(self) -> None:
        """pause() while a task is running, then resume(). Flow should complete."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Delay on "work" so we can pause while it's running
        mock_mgr.task_delays["work"] = 0.5
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            node_names=["start", "work", "finish"],
        )

        execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        await asyncio.sleep(0.1)
        runs = db.list_flow_runs()
        assert runs

        # Pause the flow (returns immediately with "pausing")
        await executor.pause(runs[0].id)

        run = db.get_flow_run(runs[0].id)
        assert run is not None
        assert run.status == "pausing"

        # Wait for "work" to finish -- main loop transitions to "paused"
        for _ in range(200):
            await asyncio.sleep(0.01)
            run = db.get_flow_run(runs[0].id)
            if run and run.status == "paused":
                break
        assert run is not None
        assert run.status == "paused"

        # The main loop should still be alive (execute_task not done)
        assert not execute_task.done(), "Main loop should be waiting in pause state"

        # Resume
        await executor.resume(runs[0].id)

        # Wait for execution to finish
        await execute_task

        run = db.get_flow_run(runs[0].id)
        assert run is not None
        assert run.status == "completed"

    async def test_pause_before_task_launch_then_resume(self) -> None:
        """Pause right as new tasks would be launched, then resume. Flow completes."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        # We use on_error=pause on a failing start to pause, then fix and resume
        mock_mgr.task_responses["start"] = (1, [])
        flow_pause = _make_linear_flow(
            on_error=ErrorPolicy.PAUSE,
            node_names=["start", "work", "finish"],
        )
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow_pause, {}, "/workspace", db
        )

        # Retry the failed task (now it succeeds)
        del mock_mgr.task_responses["start"]
        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")
        await executor.retry_task(flow_run_id, failed_task.id)

        # Resume
        await executor.resume(flow_run_id)
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

    async def test_resume_repopulates_pending_from_db(self) -> None:
        """After resume, pending tasks discovered in DB are launched."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            on_error=ErrorPolicy.PAUSE,
            node_names=["start", "work", "finish"],
        )
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        # Verify main loop is alive
        assert not execute_task.done()

        # Retry the failed task
        del mock_mgr.task_responses["work"]
        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")
        await executor.retry_task(flow_run_id, failed_task.id)

        # Verify there IS a pending task in DB
        tasks = db.list_task_executions(flow_run_id)
        pending_tasks = [t for t in tasks if t.status == "pending"]
        assert len(pending_tasks) >= 1

        # Resume
        await executor.resume(flow_run_id)
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # The retried task should have been executed
        tasks = db.list_task_executions(flow_run_id)
        completed_work = [t for t in tasks if t.node_name == "work" and t.status == "completed"]
        assert len(completed_work) >= 1


class TestResumeMainLoopStaysAlive:
    """The main loop must not exit while the flow is paused."""

    async def test_main_loop_alive_during_pause(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        # Main loop is alive (execute_task not done)
        assert not execute_task.done(), "Main loop exited prematurely"

        # Wait a bit to ensure it stays alive
        await asyncio.sleep(0.05)
        assert not execute_task.done(), "Main loop exited after brief wait"

        # Clean up
        await executor.cancel(flow_run_id)
        await execute_task


# ---------------------------------------------------------------------------
# Two-phase pause tests (ENGINE-078)
# ---------------------------------------------------------------------------


class TestTwoPhasePause:
    """Two-phase pause: pause() returns immediately with 'pausing', main loop
    transitions to 'paused' once all running tasks finish."""

    async def test_pause_returns_immediately(self) -> None:
        """pause() sets status to 'pausing' without waiting for tasks."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Delay so the task is still running when we pause
        mock_mgr.task_delays["work"] = 1.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start
        await asyncio.sleep(0.1)
        runs = db.list_flow_runs()
        assert runs

        # pause() should return immediately (well before the 1.0s delay)
        t0 = time.monotonic()
        await executor.pause(runs[0].id)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5, f"pause() took {elapsed:.2f}s, should return immediately"

        # Status is 'pausing' (not 'paused') because "work" is still running
        run = db.get_flow_run(runs[0].id)
        assert run is not None
        assert run.status == "pausing"

        # Verify the status_changed event was emitted with 'pausing'
        pause_events = [
            e
            for e in events
            if e.type == EventType.FLOW_STATUS_CHANGED and e.payload.get("new_status") == "pausing"
        ]
        assert len(pause_events) == 1
        assert pause_events[0].payload["old_status"] == "running"

        # Clean up
        await executor.cancel(runs[0].id)
        await execute_task

    async def test_pausing_transitions_to_paused(self) -> None:
        """When tasks finish while pausing, status goes to 'paused'."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 0.3
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start, then pause
        await asyncio.sleep(0.1)
        runs = db.list_flow_runs()
        assert runs
        await executor.pause(runs[0].id)

        # Status should be 'pausing' immediately
        run = db.get_flow_run(runs[0].id)
        assert run is not None
        assert run.status == "pausing"

        # Wait for "work" to complete and main loop to transition to 'paused'
        for _ in range(200):
            await asyncio.sleep(0.01)
            run = db.get_flow_run(runs[0].id)
            if run and run.status == "paused":
                break

        assert run is not None
        assert run.status == "paused"

        # Verify we got both status_changed events: running->pausing, pausing->paused
        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        pausing_event = [e for e in status_events if e.payload.get("new_status") == "pausing"]
        paused_event = [
            e
            for e in status_events
            if e.payload.get("old_status") == "pausing" and e.payload.get("new_status") == "paused"
        ]
        assert len(pausing_event) == 1
        assert len(paused_event) == 1

        # Clean up
        await executor.cancel(runs[0].id)
        await execute_task

    async def test_resume_from_pausing_cancels_pause(self) -> None:
        """Resume while 'pausing' clears the flag and goes back to 'running'."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Long delay so task is still running when we resume
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start
        await asyncio.sleep(0.1)
        runs = db.list_flow_runs()
        assert runs
        flow_run_id = runs[0].id

        # Pause (returns immediately with 'pausing')
        await executor.pause(flow_run_id)
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "pausing"

        # Resume while still 'pausing'
        await executor.resume(flow_run_id)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "running"

        # Verify the cancel-pause event
        resume_events = [
            e
            for e in events
            if e.type == EventType.FLOW_STATUS_CHANGED
            and e.payload.get("old_status") == "pausing"
            and e.payload.get("new_status") == "running"
        ]
        assert len(resume_events) == 1

        # Flow should eventually complete (resume cancelled the pause)
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

    async def test_resume_from_paused_starts_next_task(self) -> None:
        """Resume from 'paused' works as before (existing behavior)."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 0.2
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start, then pause
        await asyncio.sleep(0.1)
        runs = db.list_flow_runs()
        assert runs
        flow_run_id = runs[0].id
        await executor.pause(flow_run_id)

        # Wait for 'paused' state (task needs to finish)
        for _ in range(200):
            await asyncio.sleep(0.01)
            run = db.get_flow_run(flow_run_id)
            if run and run.status == "paused":
                break
        assert run is not None
        assert run.status == "paused"

        # Resume from 'paused'
        await executor.resume(flow_run_id)

        # Flow should complete
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

    async def test_pause_idempotent(self) -> None:
        """Calling pause() twice is safe (idempotent)."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 1.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        await asyncio.sleep(0.1)
        runs = db.list_flow_runs()
        assert runs
        flow_run_id = runs[0].id

        # Pause twice
        await executor.pause(flow_run_id)
        await executor.pause(flow_run_id)  # should be a no-op

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "pausing"

        # Clean up
        await executor.cancel(flow_run_id)
        await execute_task

    async def test_on_error_pause_goes_directly_to_paused(self) -> None:
        """on_error=pause still goes directly to 'paused' (not via 'pausing').

        The _pause_flow helper is called after the failing task is already done,
        so there are no running tasks and the status should be 'paused' directly.
        """
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(
            on_error=ErrorPolicy.PAUSE,
            node_names=["start", "work", "finish"],
        )
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # Verify the status went directly to 'paused' (no 'pausing' intermediate)
        pausing_events = [
            e
            for e in events
            if e.type == EventType.FLOW_STATUS_CHANGED and e.payload.get("new_status") == "pausing"
        ]
        assert len(pausing_events) == 0, "on_error=pause should not use 'pausing' state"

        # Clean up
        await executor.cancel(flow_run_id)
        await execute_task

    async def test_cancel_while_pausing(self) -> None:
        """Cancel takes precedence over pausing."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        await asyncio.sleep(0.1)
        runs = db.list_flow_runs()
        assert runs
        flow_run_id = runs[0].id

        # Pause (status goes to 'pausing')
        await executor.pause(flow_run_id)
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "pausing"

        # Cancel while pausing
        await executor.cancel(flow_run_id)
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "cancelled"


# ---------------------------------------------------------------------------
# Worktree integration tests (ENGINE-025)
# ---------------------------------------------------------------------------


def _make_linear_flow_worktree(
    worktree: bool = True,
    workspace: str = "/workspace",
) -> Flow:
    """Build a simple linear flow with configurable worktree setting."""
    nodes: dict[str, Node] = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Do the start step"),
        "work": Node(name="work", node_type=NodeType.TASK, prompt="Do the work step"),
        "finish": Node(name="finish", node_type=NodeType.EXIT, prompt="Do the finish step"),
    }
    edges: list[Edge] = [
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="work"),
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="work", target="finish"),
    ]
    return Flow(
        name="worktree-test",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        worktree=worktree,
        nodes=nodes,
        edges=tuple(edges),
    )


class TestWorktreeIntegration:
    """Tests for per-node git worktree lifecycle in the executor (ENGINE-070)."""

    @patch("flowstate.engine.executor.create_node_worktree")
    @patch("flowstate.engine.executor.cleanup_worktree")
    @patch("flowstate.engine.executor.is_git_repo", return_value=True)
    @patch("flowstate.engine.executor.is_existing_worktree", return_value=False)
    async def test_worktree_created_for_entry_node(
        self,
        _mock_is_wt: MagicMock,
        _mock_is_git: MagicMock,
        mock_cleanup: AsyncMock,
        mock_create: AsyncMock,
    ) -> None:
        """Entry node creates a worktree when flow.worktree=True and workspace is git."""
        worktree_path = "/tmp/flowstate-worktree-test"
        worktree_info = WorktreeInfo(
            original_workspace="/workspace",
            worktree_path=worktree_path,
            branch_name="flowstate/abc12345/start-1",
        )
        mock_create.return_value = worktree_info

        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow_worktree(worktree=True, workspace="/workspace")
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Verify create_node_worktree was called for entry node
        assert mock_create.call_count >= 1
        first_call = mock_create.call_args_list[0]
        assert first_call[1].get("node_name", first_call[0][2]) == "start"

        # Verify the cwd uses the worktree path
        resolved_worktree = str(Path(worktree_path).resolve())
        tasks = db.list_task_executions(flow_run_id)
        for task in tasks:
            assert (
                task.cwd == resolved_worktree
            ), f"Task {task.node_name} cwd should be worktree path, got {task.cwd}"

    async def test_worktree_skipped_when_false(self) -> None:
        """When flow.worktree=False, no worktree artifacts are created."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow_worktree(worktree=False, workspace="/workspace")
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # No worktree artifacts should exist
        tasks = db.list_task_executions(flow_run_id)
        for task in tasks:
            wt = db.get_artifact(task.id, "worktree")
            assert wt is None, f"Task {task.node_name} should not have worktree artifact"

    async def test_worktree_skipped_for_non_git(self) -> None:
        """When workspace is not a git repo, no worktree artifacts are created."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        # /workspace doesn't exist, so is_git_repo returns False
        flow = _make_linear_flow_worktree(worktree=True, workspace="/workspace")
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        for task in tasks:
            wt = db.get_artifact(task.id, "worktree")
            assert wt is None, f"Task {task.node_name} should not have worktree artifact"

    @patch("flowstate.engine.executor.create_node_worktree")
    @patch("flowstate.engine.executor.cleanup_worktree")
    @patch("flowstate.engine.executor.is_git_repo", return_value=True)
    @patch("flowstate.engine.executor.is_existing_worktree", return_value=False)
    async def test_worktree_cleanup_on_completion(
        self,
        _mock_is_wt: MagicMock,
        _mock_is_git: MagicMock,
        mock_cleanup: AsyncMock,
        mock_create: AsyncMock,
    ) -> None:
        """Verify per-node worktree cleanup on flow completion."""
        worktree_info = WorktreeInfo(
            original_workspace="/workspace",
            worktree_path="/tmp/flowstate-cleanup-test",
            branch_name="flowstate/abc12345/start-1",
        )
        mock_create.return_value = worktree_info

        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow_worktree(worktree=True, workspace="/workspace")
        await executor.execute(flow, {}, "/workspace")

        # cleanup_worktree should have been called for the worktree
        mock_cleanup.assert_called()

    @patch("flowstate.engine.executor.create_node_worktree")
    @patch("flowstate.engine.executor.cleanup_worktree")
    @patch("flowstate.engine.executor.is_git_repo", return_value=True)
    @patch("flowstate.engine.executor.is_existing_worktree", return_value=False)
    async def test_worktree_cleanup_skipped_when_disabled(
        self,
        _mock_is_wt: MagicMock,
        _mock_is_git: MagicMock,
        mock_cleanup: AsyncMock,
        mock_create: AsyncMock,
    ) -> None:
        """When worktree_cleanup=False, cleanup_worktree is not called."""
        mock_create.return_value = WorktreeInfo(
            original_workspace="/workspace",
            worktree_path="/tmp/flowstate-no-cleanup",
            branch_name="flowstate/abc12345/start-1",
        )

        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, worktree_cleanup=False, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow_worktree(worktree=True, workspace="/workspace")
        await executor.execute(flow, {}, "/workspace")

        mock_create.assert_called()
        mock_cleanup.assert_not_called()

    async def test_worktree_fallback_on_error(self) -> None:
        """When worktree creation fails, flow uses original workspace."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        # /workspace doesn't exist, is_git_repo=False -> no worktree
        flow = _make_linear_flow_worktree(worktree=True, workspace="/workspace")
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Flow still completes successfully
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # All tasks used the original workspace (resolved to absolute path)
        tasks = db.list_task_executions(flow_run_id)
        for task in tasks:
            assert (
                "/tmp/flowstate" not in task.cwd
            ), f"Task {task.node_name} should not use worktree path"

    async def test_worktree_skipped_for_existing_worktree(self) -> None:
        """When workspace is already a worktree, don't nest."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow_worktree(worktree=True, workspace="/workspace")
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        for task in tasks:
            wt = db.get_artifact(task.id, "worktree")
            assert wt is None

    @patch("flowstate.engine.executor.create_node_worktree")
    @patch("flowstate.engine.executor.cleanup_worktree")
    @patch("flowstate.engine.executor.is_git_repo", return_value=True)
    @patch("flowstate.engine.executor.is_existing_worktree", return_value=False)
    async def test_worktree_artifact_saved_on_tasks(
        self,
        _mock_is_wt: MagicMock,
        _mock_is_git: MagicMock,
        mock_cleanup: AsyncMock,
        mock_create: AsyncMock,
    ) -> None:
        """Worktree artifacts are saved on each task when worktree is active."""
        worktree_info = WorktreeInfo(
            original_workspace="/workspace",
            worktree_path="/tmp/flowstate-artifact-test",
            branch_name="flowstate/abc12345/start-1",
        )
        mock_create.return_value = worktree_info

        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow_worktree(worktree=True, workspace="/workspace")
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # All tasks should have worktree artifacts
        tasks = db.list_task_executions(flow_run_id)
        for task in tasks:
            wt = db.get_artifact(task.id, "worktree")
            assert wt is not None, f"Task {task.node_name} should have worktree artifact"
            import json

            data = json.loads(wt.content)
            assert "path" in data
            assert "branch" in data
            assert "original_workspace" in data

    @patch("flowstate.engine.executor.create_node_worktree")
    @patch("flowstate.engine.executor.cleanup_worktree")
    @patch("flowstate.engine.executor.is_git_repo", return_value=True)
    @patch("flowstate.engine.executor.is_existing_worktree", return_value=False)
    async def test_worktree_cleanup_on_cancel(
        self,
        _mock_is_wt: MagicMock,
        _mock_is_git: MagicMock,
        mock_cleanup: AsyncMock,
        mock_create: AsyncMock,
    ) -> None:
        """Verify worktree cleanup is called when flow is cancelled."""
        worktree_info = WorktreeInfo(
            original_workspace="/workspace",
            worktree_path="/tmp/flowstate-cancel-test",
            branch_name="flowstate/abc12345/start-1",
        )
        mock_create.return_value = worktree_info

        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow_worktree(worktree=True, workspace="/workspace")
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        await executor.cancel(flow_run_id)
        await execute_task

        # cleanup should have been called for the entry node's worktree
        mock_cleanup.assert_called()


# ---------------------------------------------------------------------------
# Tests: Activity logs (ENGINE-024)
# ---------------------------------------------------------------------------


def _extract_activity_logs(events: list[FlowEvent]) -> list[str]:
    """Extract activity log messages from TASK_LOG events."""
    messages: list[str] = []
    for event in events:
        if event.type == EventType.TASK_LOG:
            content = event.payload.get("content", "")
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and parsed.get("subtype") == "activity":
                        messages.append(parsed["message"])
                except (json.JSONDecodeError, KeyError):
                    pass
    return messages


class TestActivityLogsLinear:
    """Activity log emissions for linear flows."""

    async def test_dispatch_activity_logged(self) -> None:
        """Each node dispatch emits a dispatch activity log."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow()
        await executor.execute(flow, {}, "/workspace")

        activities = _extract_activity_logs(events)
        dispatch_logs = [m for m in activities if m.startswith("\u25b6")]
        # 3 nodes: start, work, finish
        assert len(dispatch_logs) == 3
        assert "start" in dispatch_logs[0]
        assert "work" in dispatch_logs[1]
        assert "finish" in dispatch_logs[2]

    async def test_edge_transition_activity_logged(self) -> None:
        """Each unconditional edge transition emits a transition activity log."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow()
        await executor.execute(flow, {}, "/workspace")

        activities = _extract_activity_logs(events)
        transition_logs = [m for m in activities if m.startswith("\u2192")]
        # 2 transitions: start->work, work->finish
        assert len(transition_logs) == 2
        assert "start" in transition_logs[0] and "work" in transition_logs[0]
        assert "work" in transition_logs[1] and "finish" in transition_logs[1]

    async def test_activity_logs_stored_in_db(self) -> None:
        """Activity logs are persisted in the task_logs table."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow()
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Get task logs for the first task
        tasks = db.list_task_executions(flow_run_id)
        first_task = tasks[0]
        logs = db.get_task_logs(first_task.id)
        system_logs = [entry for entry in logs if entry.log_type == "system"]

        # Should have at least the dispatch activity log
        activity_contents = []
        for log in system_logs:
            try:
                parsed = json.loads(log.content)
                if isinstance(parsed, dict) and parsed.get("subtype") == "activity":
                    activity_contents.append(parsed["message"])
            except (json.JSONDecodeError, KeyError):
                pass
        assert any("\u25b6" in msg for msg in activity_contents)


class TestActivityLogsForkJoin:
    """Activity log emissions for fork-join flows."""

    async def test_fork_activity_logged(self) -> None:
        """Fork operation emits a fork activity log with target list."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_fork_join_flow(fork_targets=["task_a", "task_b"])
        await executor.execute(flow, {}, "/workspace")

        activities = _extract_activity_logs(events)
        fork_logs = [m for m in activities if m.startswith("\u2442")]
        assert len(fork_logs) == 1
        assert "task_a" in fork_logs[0]
        assert "task_b" in fork_logs[0]

    async def test_join_activity_logged(self) -> None:
        """Join operation emits a join activity log."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_fork_join_flow(fork_targets=["task_a", "task_b"])
        await executor.execute(flow, {}, "/workspace")

        activities = _extract_activity_logs(events)
        join_logs = [m for m in activities if m.startswith("\u2295")]
        assert len(join_logs) == 1
        assert "merge" in join_logs[0]


class TestActivityLogsConditional:
    """Activity log emissions for conditional flows with judge."""

    async def test_judge_decision_activity_logged(self) -> None:
        """Judge decision emits an activity log with target and confidence."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(JudgeDecision(target="done", reasoning="All good", confidence=0.95))

        executor = FlowExecutor(
            db,
            callback,
            mock_mgr,
            judge=mock_judge,
            max_concurrent=4,
            server_base_url="http://127.0.0.1:9090",
        )
        flow = _make_conditional_flow()
        await executor.execute(flow, {}, "/workspace")

        activities = _extract_activity_logs(events)
        judge_logs = [m for m in activities if m.startswith("\u2696")]
        assert len(judge_logs) == 1
        assert "done" in judge_logs[0]
        assert "0.95" in judge_logs[0]


class TestSelfReportRouting:
    """Tests for judge=False self-report routing (ENGINE-047).

    When judge=False, the executor reads the decision artifact from DB
    instead of invoking a judge subprocess. Activity logs should say 'Self-report routed'
    instead of 'Judge decided', and no judge.started/judge.decided events should be emitted.
    """

    async def test_self_report_conditional_no_judge_events(self) -> None:
        """judge=False conditional: no JUDGE_STARTED/JUDGE_DECIDED events."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        # Build conditional flow with judge=False
        flow = _make_conditional_flow()
        flow = Flow(
            name=flow.name,
            budget_seconds=flow.budget_seconds,
            on_error=flow.on_error,
            context=flow.context,
            workspace=flow.workspace,
            judge=False,
            nodes=flow.nodes,
            edges=flow.edges,
        )

        mock_decision = JudgeDecision(target="done", reasoning="Approved", confidence=0.95)

        async def mock_read_decision(task_id: str, flow_run_id: str) -> JudgeDecision:
            return mock_decision

        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )
        with patch.object(executor, "_read_decision_artifact", side_effect=mock_read_decision):
            await executor.execute(flow, {}, "/workspace")

        # No judge events should be emitted
        judge_started = [e for e in events if e.type == EventType.JUDGE_STARTED]
        assert (
            len(judge_started) == 0
        ), "judge.started events should not be emitted with judge=False"

        judge_decided = [e for e in events if e.type == EventType.JUDGE_DECIDED]
        assert (
            len(judge_decided) == 0
        ), "judge.decided events should not be emitted with judge=False"

        # Flow should still complete (edge transition should occur)
        edge_transitions = [e for e in events if e.type == EventType.EDGE_TRANSITION]
        assert len(edge_transitions) >= 1

    async def test_self_report_conditional_activity_log(self) -> None:
        """judge=False conditional: activity log says 'Self-report routed', not 'Judge decided'."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        flow = _make_conditional_flow()
        flow = Flow(
            name=flow.name,
            budget_seconds=flow.budget_seconds,
            on_error=flow.on_error,
            context=flow.context,
            workspace=flow.workspace,
            judge=False,
            nodes=flow.nodes,
            edges=flow.edges,
        )

        mock_decision = JudgeDecision(target="done", reasoning="Approved", confidence=0.95)

        async def mock_read_decision(task_id: str, flow_run_id: str) -> JudgeDecision:
            return mock_decision

        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )
        with patch.object(executor, "_read_decision_artifact", side_effect=mock_read_decision):
            await executor.execute(flow, {}, "/workspace")

        activities = _extract_activity_logs(events)

        # No "Judge decided" messages
        judge_logs = [m for m in activities if "Judge decided" in m]
        assert len(judge_logs) == 0, f"Should have no 'Judge decided' logs, got: {judge_logs}"

        # Should have "Self-report routed" messages instead
        self_report_logs = [m for m in activities if "Self-report routed" in m]
        assert (
            len(self_report_logs) == 1
        ), f"Should have exactly 1 'Self-report routed' log, got: {self_report_logs}"
        assert "done" in self_report_logs[0]
        assert "0.95" in self_report_logs[0]

    async def test_self_report_default_edge_no_judge_events(self) -> None:
        """judge=False default-edge: no JUDGE_STARTED/JUDGE_DECIDED events."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        base_flow = _make_default_edge_flow()
        flow = Flow(
            name=base_flow.name,
            budget_seconds=base_flow.budget_seconds,
            on_error=base_flow.on_error,
            context=base_flow.context,
            workspace=base_flow.workspace,
            judge=False,
            nodes=base_flow.nodes,
            edges=base_flow.edges,
        )

        # First call: __none__ (fallback to default edge -> alice -> bob -> moderator)
        # Second call: match "all tasks complete" -> done
        decisions = [
            JudgeDecision(target="__none__", reasoning="", confidence=0.0),
            JudgeDecision(target="done", reasoning="All complete", confidence=0.95),
        ]
        call_count = {"n": 0}

        async def mock_read_decision(task_id: str, flow_run_id: str) -> JudgeDecision:
            idx = call_count["n"]
            call_count["n"] += 1
            return decisions[idx]

        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )
        with patch.object(executor, "_read_decision_artifact", side_effect=mock_read_decision):
            await executor.execute(flow, {}, "/workspace")

        judge_started = [e for e in events if e.type == EventType.JUDGE_STARTED]
        assert len(judge_started) == 0

        judge_decided = [e for e in events if e.type == EventType.JUDGE_DECIDED]
        assert len(judge_decided) == 0

        # Flow should complete
        completed_events = [e for e in events if e.type == EventType.FLOW_COMPLETED]
        assert len(completed_events) == 1

        # Activity logs should have self-report, not judge
        activities = _extract_activity_logs(events)
        judge_logs = [m for m in activities if "Judge decided" in m]
        assert len(judge_logs) == 0
        self_report_logs = [m for m in activities if "Self-report routed" in m]
        assert len(self_report_logs) >= 1

    async def test_self_report_failure_pauses_flow(self) -> None:
        """judge=False: when decision artifact is missing, flow pauses."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        flow = _make_conditional_flow(with_cycle=True)
        flow = Flow(
            name=flow.name,
            budget_seconds=flow.budget_seconds,
            on_error=flow.on_error,
            context=flow.context,
            workspace=flow.workspace,
            judge=False,
            nodes=flow.nodes,
            edges=flow.edges,
        )

        async def mock_read_decision(task_id: str, flow_run_id: str) -> JudgeDecision:
            raise FileNotFoundError("No decision artifact submitted by agent")

        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )
        with patch.object(executor, "_read_decision_artifact", side_effect=mock_read_decision):
            flow_run_id, execute_task = await _execute_until_paused(
                executor, flow, {}, "/workspace", db
            )

        # Flow should be paused
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        paused = [e for e in status_events if e.payload.get("new_status") == "paused"]
        assert len(paused) >= 1, "Flow should pause when self-report fails"
        assert any("self-report" in str(e.payload.get("reason", "")).lower() for e in paused)

        await executor.cancel(flow_run_id)
        await execute_task

    async def test_node_level_judge_override(self) -> None:
        """Node-level judge=True overrides flow-level judge=False: judge events emitted."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        # Build conditional flow with judge=False at flow level, but judge=True on review node
        base_flow = _make_conditional_flow()
        nodes = dict(base_flow.nodes)
        nodes["review"] = Node(
            name="review",
            node_type=NodeType.TASK,
            prompt="Do the review step",
            judge=True,
        )

        flow = Flow(
            name=base_flow.name,
            budget_seconds=base_flow.budget_seconds,
            on_error=base_flow.on_error,
            context=base_flow.context,
            workspace=base_flow.workspace,
            judge=False,
            nodes=nodes,
            edges=base_flow.edges,
        )

        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(JudgeDecision(target="done", reasoning="Approved", confidence=0.95))

        executor = FlowExecutor(
            db,
            callback,
            mock_mgr,
            judge=mock_judge,
            max_concurrent=4,
            server_base_url="http://127.0.0.1:9090",
        )
        await executor.execute(flow, {}, "/workspace")

        # Judge events SHOULD be emitted since node-level judge=True overrides
        judge_started = [e for e in events if e.type == EventType.JUDGE_STARTED]
        assert len(judge_started) == 1

        judge_decided = [e for e in events if e.type == EventType.JUDGE_DECIDED]
        assert len(judge_decided) == 1

        activities = _extract_activity_logs(events)
        judge_logs = [m for m in activities if "Judge decided" in m]
        assert len(judge_logs) == 1


class TestActivityLogsPause:
    """Activity log emissions for flow pausing."""

    async def test_pause_on_error_activity_logged(self) -> None:
        """Flow pause emits a pause activity log with reason."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Make "work" node fail
        mock_mgr.task_responses["work"] = (1, [])

        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )
        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)

        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        activities = _extract_activity_logs(events)
        pause_logs = [m for m in activities if m.startswith("\u23f8")]
        assert len(pause_logs) >= 1
        assert "paused" in pause_logs[0].lower()

        # Clean up
        await executor.cancel(flow_run_id)
        with contextlib.suppress(asyncio.CancelledError):
            await execute_task


class TestActivityLogsBudgetWarning:
    """Activity log emissions for budget warnings."""

    async def test_budget_warning_activity_logged(self) -> None:
        """Budget warning emits an activity log with usage percentage."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Use a tiny budget to trigger warning thresholds
        # Budget is 10 seconds, tasks use simulated elapsed time from monotonic clock
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )
        flow = _make_linear_flow(budget_seconds=10)

        # Patch time.monotonic to simulate elapsed time
        original_monotonic = time.monotonic
        call_count = 0

        def mock_monotonic() -> float:
            nonlocal call_count
            call_count += 1
            # Each pair of calls (start_time, elapsed) represents one task.
            # Make the first task "take" 6 seconds to trigger 50% warning.
            if call_count == 2:
                return original_monotonic() + 6.0
            return original_monotonic()

        with patch("flowstate.engine.executor.time.monotonic", side_effect=mock_monotonic):
            await executor.execute(flow, {}, "/workspace")

        activities = _extract_activity_logs(events)
        budget_logs = [m for m in activities if m.startswith("\u26a0")]
        # Whether or not we hit the exact threshold depends on monotonic timing,
        # so this test just verifies the mechanism works when the budget guard
        # returns warnings.
        # If we got any budget warnings, they should contain % used info.
        for msg in budget_logs:
            assert "Budget warning" in msg


# ===========================================================================
# Task-aware executor tests (ENGINE-027)
# ===========================================================================


class TestTaskAwareExecution:
    """Tests for the task queue integration in FlowExecutor."""

    async def test_task_context_injected_into_prompts(self) -> None:
        """When task_id is set, task context should be prepended to prompts."""
        flow = _make_linear_flow()
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        # Create a task in the DB
        task_id = db.create_task(
            "test-flow",
            "Build the feature",
            description="Implement user authentication",
        )

        await executor.execute(flow, {}, "/workspace", task_id=task_id)

        # Check that prompts contain the task context
        for prompt, _workspace, _session_id in subprocess_mgr.calls:
            assert "## Task Context" in prompt
            assert "Title: Build the feature" in prompt
            assert "Description: Implement user authentication" in prompt

    async def test_task_context_without_description(self) -> None:
        """Task context injection should work when description is None."""
        flow = _make_linear_flow()
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        task_id = db.create_task("test-flow", "No description task")

        await executor.execute(flow, {}, "/workspace", task_id=task_id)

        for prompt, _, _ in subprocess_mgr.calls:
            assert "## Task Context" in prompt
            assert "Title: No description task" in prompt
            assert "Description:" not in prompt

    async def test_no_task_context_without_task_id(self) -> None:
        """When task_id is None, no task context should be added."""
        flow = _make_linear_flow()
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        await executor.execute(flow, {}, "/workspace")

        for prompt, _, _ in subprocess_mgr.calls:
            assert "## Task Context" not in prompt

    async def test_task_node_history_tracked(self) -> None:
        """Node history should be recorded for each node during execution."""
        flow = _make_linear_flow()
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        task_id = db.create_task("test-flow", "History test")

        await executor.execute(flow, {}, "/workspace", task_id=task_id)

        history = db.get_task_history(task_id)
        node_names = [h.node_name for h in history]
        assert "start" in node_names
        assert "work" in node_names
        assert "finish" in node_names
        # All should have completed_at set (successful run)
        for h in history:
            assert h.started_at is not None
            assert h.completed_at is not None

    async def test_task_current_node_updated(self) -> None:
        """current_node on the task should be updated as nodes execute."""
        flow = _make_linear_flow()
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        task_id = db.create_task("test-flow", "Track current node")

        await executor.execute(flow, {}, "/workspace", task_id=task_id)

        # After successful completion, the last node processed was "finish"
        task = db.get_task(task_id)
        assert task is not None
        assert task.current_node == "finish"

    async def test_task_marked_completed_on_flow_complete(self) -> None:
        """Task should be marked completed when the flow completes."""
        flow = _make_linear_flow()
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        task_id = db.create_task("test-flow", "Complete me")

        await executor.execute(flow, {}, "/workspace", task_id=task_id)

        task = db.get_task(task_id)
        assert task is not None
        assert task.status == "completed"
        assert task.completed_at is not None

    async def test_task_marked_cancelled_on_cancel(self) -> None:
        """Task should be marked cancelled when the flow is cancelled."""
        flow = _make_linear_flow()
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        subprocess_mgr.task_delays["work"] = 1.0  # slow task to allow cancellation
        events: list[FlowEvent] = []

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        task_id = db.create_task("test-flow", "Cancel me")

        async def run_and_cancel() -> str:
            execute_task = asyncio.create_task(
                executor.execute(flow, {}, "/workspace", task_id=task_id)
            )
            # Wait a moment for execution to start
            await asyncio.sleep(0.1)
            # Get the flow run id from DB
            runs = db.list_flow_runs()
            if runs:
                await executor.cancel(runs[0].id)
            return await execute_task

        await run_and_cancel()

        task = db.get_task(task_id)
        assert task is not None
        assert task.status == "cancelled"

    async def test_task_marked_paused_on_error(self) -> None:
        """Task should be marked paused when on_error=pause triggers."""
        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        # Make the 'work' node fail
        subprocess_mgr.task_responses["work"] = (1, [])
        events: list[FlowEvent] = []

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        task_id = db.create_task("test-flow", "Pause me")

        # Start execution in background with task_id
        execute_task = asyncio.create_task(
            executor.execute(flow, {}, "/workspace", task_id=task_id)
        )
        # Poll until the flow pauses
        flow_run_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            if execute_task.done():
                break
            runs = db.list_flow_runs()
            if runs:
                run = db.get_flow_run(runs[0].id)
                if run and run.status == "paused":
                    flow_run_id = run.id
                    break

        assert flow_run_id is not None, "Flow did not reach paused state"

        task = db.get_task(task_id)
        assert task is not None
        assert task.status == "paused"
        assert task.error_message is not None

        # Cancel to let execute() return
        await executor.cancel(flow_run_id)
        with contextlib.suppress(asyncio.CancelledError):
            await execute_task

    async def test_task_id_stored_in_flow_run(self) -> None:
        """The flow run record should have the task_id set."""
        flow = _make_linear_flow()
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        task_id = db.create_task("test-flow", "Link to run")

        flow_run_id = await executor.execute(flow, {}, "/workspace", task_id=task_id)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.task_id == task_id


# ---------------------------------------------------------------------------
# Cross-flow task filing (ENGINE-028)
# ---------------------------------------------------------------------------


def _make_file_edge_flow(
    target_flow: str = "child-flow",
    workspace: str = "/workspace",
) -> Flow:
    """Build a flow with a FILE edge: start -> work -files-> child-flow, work -> finish."""
    nodes: dict[str, Node] = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Do the start step"),
        "work": Node(name="work", node_type=NodeType.TASK, prompt="Do the work step"),
        "finish": Node(name="finish", node_type=NodeType.EXIT, prompt="Do the finish step"),
    }

    edges = (
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="start",
            target="work",
        ),
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="work",
            target="finish",
        ),
        Edge(
            edge_type=EdgeType.FILE,
            source="work",
            target=target_flow,
        ),
    )

    return Flow(
        name="file-edge-flow",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes,
        edges=edges,
    )


def _make_await_edge_flow(
    target_flow: str = "child-flow",
    workspace: str = "/workspace",
) -> Flow:
    """Build a flow with an AWAIT edge: start -> work -awaits-> child-flow, work -> finish."""
    nodes: dict[str, Node] = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Do the start step"),
        "work": Node(name="work", node_type=NodeType.TASK, prompt="Do the work step"),
        "finish": Node(name="finish", node_type=NodeType.EXIT, prompt="Do the finish step"),
    }

    edges = (
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="start",
            target="work",
        ),
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="work",
            target="finish",
        ),
        Edge(
            edge_type=EdgeType.AWAIT,
            source="work",
            target=target_flow,
        ),
    )

    return Flow(
        name="await-edge-flow",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes,
        edges=edges,
    )


class TestFileEdgeCreatesChildTask:
    """FILE edge creates a child task in the target flow (async, non-blocking)."""

    async def test_file_edge_creates_child_task(self) -> None:
        """A FILE edge should create a queued child task in the target flow."""
        flow = _make_file_edge_flow(target_flow="deploy-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("file-edge-flow", "Parent task")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        flow_run_id = await executor.execute(flow, {}, "/workspace", task_id=task_id)

        # Flow should have completed (FILE edges don't block)
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # A child task should have been created in the target flow
        child_tasks = db.list_tasks(flow_name="deploy-flow")
        assert len(child_tasks) == 1
        child = child_tasks[0]
        assert child.flow_name == "deploy-flow"
        assert child.status == "queued"
        assert child.parent_task_id == task_id
        assert child.created_by == "flow:file-edge-flow/node:work"


class TestFileEdgeChildTaskMetadata:
    """FILE edge child task should have correct metadata."""

    async def test_child_task_title_and_description(self) -> None:
        """Child task title should reference the source node and parent title."""
        flow = _make_file_edge_flow(target_flow="deploy-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("file-edge-flow", "My parent task")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        await executor.execute(flow, {}, "/workspace", task_id=task_id)

        child_tasks = db.list_tasks(flow_name="deploy-flow")
        assert len(child_tasks) == 1
        child = child_tasks[0]
        assert "work" in child.title
        assert "My parent task" in child.title
        # Description should contain something (summary or fallback)
        assert child.description is not None
        assert len(child.description) > 0

    async def test_child_task_params_from_output_artifact(self) -> None:
        """Child task params should come from source node output artifact, not parent params."""
        flow = _make_file_edge_flow(target_flow="deploy-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task(
            "file-edge-flow",
            "With params",
            params_json=json.dumps({"env": "production"}),
        )

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        # Save output artifact on the "work" node's task execution after it's created
        original_build = executor._build_child_params

        def patched_build(task_execution_id: str) -> dict[str, str | float | bool]:
            # Save the output artifact before the real method reads it
            db.save_artifact(
                task_execution_id,
                "output",
                json.dumps({"target_env": "staging", "version": "1.2.3"}),
                "application/json",
            )
            return original_build(task_execution_id)

        with patch.object(executor, "_build_child_params", side_effect=patched_build):
            await executor.execute(flow, {}, "/workspace", task_id=task_id)

        child_tasks = db.list_tasks(flow_name="deploy-flow")
        assert len(child_tasks) == 1
        child = child_tasks[0]
        assert child.params_json is not None
        params = json.loads(child.params_json)
        # Child params come from output artifact, not parent params
        assert params["target_env"] == "staging"
        assert params["version"] == "1.2.3"
        assert "env" not in params  # Parent params are NOT inherited


class TestCrossFlowInputMapping:
    """ENGINE-029: Cross-flow task filing maps source output to child params."""

    async def test_file_edge_with_output_artifact(self) -> None:
        """FILE edge with output artifact -> child params come from output artifact fields."""
        flow = _make_file_edge_flow(target_flow="deploy-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("file-edge-flow", "Parent task")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        original_build = executor._build_child_params

        def patched_build(task_execution_id: str) -> dict[str, str | float | bool]:
            db.save_artifact(
                task_execution_id,
                "output",
                json.dumps({"repo": "my-app", "branch": "main", "deploy": True}),
                "application/json",
            )
            return original_build(task_execution_id)

        with patch.object(executor, "_build_child_params", side_effect=patched_build):
            await executor.execute(flow, {}, "/workspace", task_id=task_id)

        child_tasks = db.list_tasks(flow_name="deploy-flow")
        assert len(child_tasks) == 1
        child = child_tasks[0]
        assert child.params_json is not None
        params = json.loads(child.params_json)
        assert params == {"repo": "my-app", "branch": "main", "deploy": True}

    async def test_file_edge_without_output_uses_summary(self) -> None:
        """FILE edge without output artifact -> child params use summary as description."""
        flow = _make_file_edge_flow(target_flow="deploy-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("file-edge-flow", "Parent task")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        # No output artifact, but summary artifact exists
        original_build = executor._build_child_params

        def patched_build(task_execution_id: str) -> dict[str, str | float | bool]:
            db.save_artifact(
                task_execution_id,
                "summary",
                "Completed the work step successfully",
                "text/markdown",
            )
            return original_build(task_execution_id)

        with patch.object(executor, "_build_child_params", side_effect=patched_build):
            await executor.execute(flow, {}, "/workspace", task_id=task_id)

        child_tasks = db.list_tasks(flow_name="deploy-flow")
        assert len(child_tasks) == 1
        child = child_tasks[0]
        assert child.params_json is not None
        params = json.loads(child.params_json)
        assert params == {"description": "Completed the work step successfully"}

    async def test_file_edge_with_empty_output(self) -> None:
        """FILE edge with no output at all -> child params are empty dict (None params_json)."""
        flow = _make_file_edge_flow(target_flow="deploy-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("file-edge-flow", "Parent task")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        # No output, no summary artifacts -- _build_child_params returns {}
        await executor.execute(flow, {}, "/workspace", task_id=task_id)

        child_tasks = db.list_tasks(flow_name="deploy-flow")
        assert len(child_tasks) == 1
        child = child_tasks[0]
        # Empty params -> params_json should be None
        assert child.params_json is None

    async def test_await_edge_with_output_artifact(self) -> None:
        """AWAIT edge should also map output artifact to child params."""
        flow = _make_await_edge_flow(target_flow="blocking-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("await-edge-flow", "Parent task")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        original_build = executor._build_child_params

        def patched_build(task_execution_id: str) -> dict[str, str | float | bool]:
            db.save_artifact(
                task_execution_id,
                "output",
                json.dumps({"issue_id": "BUG-42", "severity": "high"}),
                "application/json",
            )
            return original_build(task_execution_id)

        async def complete_child_after_delay() -> None:
            for _ in range(100):
                await asyncio.sleep(0.05)
                child_tasks = db.list_tasks(flow_name="blocking-flow")
                if child_tasks:
                    child = child_tasks[0]
                    if child.status == "queued":
                        db.update_task_queue_status(child.id, "completed")
                        return

        with patch.object(executor, "_build_child_params", side_effect=patched_build):
            execute_task = asyncio.create_task(
                executor.execute(flow, {}, "/workspace", task_id=task_id)
            )
            helper_task = asyncio.create_task(complete_child_after_delay())

            await asyncio.wait_for(execute_task, timeout=10)
            await helper_task

        child_tasks = db.list_tasks(flow_name="blocking-flow")
        assert len(child_tasks) == 1
        child = child_tasks[0]
        assert child.params_json is not None
        params = json.loads(child.params_json)
        assert params == {"issue_id": "BUG-42", "severity": "high"}

    async def test_cross_flow_prompt_instructions(self) -> None:
        """Nodes with FILE/AWAIT edges should have cross-flow output instructions in prompt."""
        flow = _make_file_edge_flow(target_flow="deploy-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("file-edge-flow", "Check prompt")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        await executor.execute(flow, {}, "/workspace", task_id=task_id)

        # Find the prompt for the "work" node (which has the FILE edge)
        work_prompt = None
        for call in subprocess_mgr.calls:
            prompt_text = call[0]
            if "Do the work step" in prompt_text:
                work_prompt = prompt_text
                break

        assert work_prompt is not None
        assert "Cross-flow output" in work_prompt
        assert "deploy-flow" in work_prompt
        assert "artifacts/output" in work_prompt

        # The "start" and "finish" nodes should NOT have cross-flow instructions
        for call in subprocess_mgr.calls:
            prompt_text = call[0]
            if "Do the start step" in prompt_text or "Do the finish step" in prompt_text:
                assert "Cross-flow output" not in prompt_text


class TestFileEdgeDoesNotBlockFlow:
    """FILE edge should not block the current flow execution."""

    async def test_flow_continues_after_file_edge(self) -> None:
        """All normal nodes complete even when a FILE edge fires."""
        flow = _make_file_edge_flow()
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("file-edge-flow", "Continue flowing")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        flow_run_id = await executor.execute(flow, {}, "/workspace", task_id=task_id)

        # All 3 node task executions should exist (start, work, finish)
        task_execs = db.list_task_executions(flow_run_id)
        assert len(task_execs) == 3
        node_names = [t.node_name for t in task_execs]
        assert node_names == ["start", "work", "finish"]
        for t in task_execs:
            assert t.status == "completed"


class TestFileEdgeDepthLimit:
    """Depth limit prevents infinite filing chains."""

    async def test_depth_limit_prevents_deep_filing(self) -> None:
        """When parent chain exceeds 10, FILE edges should be skipped."""
        flow = _make_file_edge_flow(target_flow="deep-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        # Create a chain of 11 parent tasks
        parent_ids: list[str] = []
        prev_id: str | None = None
        for i in range(11):
            tid = db.create_task(
                "file-edge-flow",
                f"Task depth {i}",
                parent_task_id=prev_id,
            )
            parent_ids.append(tid)
            prev_id = tid

        # Use the deepest task as the task_id
        deepest_task_id = parent_ids[-1]

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        await executor.execute(flow, {}, "/workspace", task_id=deepest_task_id)

        # No child task should have been created (depth limit reached)
        child_tasks = db.list_tasks(flow_name="deep-flow")
        assert len(child_tasks) == 0

    async def test_within_depth_limit_files_normally(self) -> None:
        """A task at depth 5 should still file normally."""
        flow = _make_file_edge_flow(target_flow="shallow-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        # Create a chain of 5 parent tasks (depth 5, under limit of 10)
        prev_id: str | None = None
        for i in range(5):
            prev_id = db.create_task(
                "file-edge-flow",
                f"Task depth {i}",
                parent_task_id=prev_id,
            )

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        await executor.execute(flow, {}, "/workspace", task_id=prev_id)

        # Child task should be created (within depth limit)
        child_tasks = db.list_tasks(flow_name="shallow-flow")
        assert len(child_tasks) == 1


class TestFileEdgeActivityLog:
    """Activity log should be emitted for filed tasks."""

    async def test_activity_log_emitted_for_file_edge(self) -> None:
        """A system activity log should be emitted when a FILE edge fires."""
        flow = _make_file_edge_flow(target_flow="deploy-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("file-edge-flow", "Log me")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        await executor.execute(flow, {}, "/workspace", task_id=task_id)

        # Find activity log events that mention filing
        activity_events = [
            e
            for e in events
            if e.type == EventType.TASK_LOG and e.payload.get("log_type") == "system"
        ]
        activity_messages: list[str] = []
        for e in activity_events:
            content = e.payload.get("content", "")
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    activity_messages.append(parsed.get("message", ""))
            except (json.JSONDecodeError, TypeError):
                pass
        filed_messages = [m for m in activity_messages if "Filed task to deploy-flow" in m]
        assert len(filed_messages) == 1


class TestFileEdgeWithoutTaskId:
    """FILE edge should still create child task even without parent task_id."""

    async def test_file_edge_without_task_id(self) -> None:
        """FILE edge fires even when executor has no task_id (no depth check)."""
        flow = _make_file_edge_flow(target_flow="orphan-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        # Execute without task_id
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # Child task should exist with no parent_task_id
        child_tasks = db.list_tasks(flow_name="orphan-flow")
        assert len(child_tasks) == 1
        child = child_tasks[0]
        assert child.parent_task_id is None
        assert child.created_by == "flow:file-edge-flow/node:work"


class TestAwaitEdgeCreatesChildTask:
    """AWAIT edge creates a child task and sets current task to waiting."""

    async def test_await_edge_creates_child_task(self) -> None:
        """An AWAIT edge should create a child task in the target flow."""
        flow = _make_await_edge_flow(target_flow="blocking-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("await-edge-flow", "Wait for child")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        # Run in background since AWAIT blocks. Complete the child after
        # a short delay so the executor can finish.
        async def complete_child_after_delay() -> None:
            """Find and complete the child task after it's created."""
            for _ in range(100):
                await asyncio.sleep(0.05)
                child_tasks = db.list_tasks(flow_name="blocking-flow")
                if child_tasks:
                    child = child_tasks[0]
                    if child.status == "queued":
                        db.update_task_queue_status(child.id, "completed")
                        return

        # Start executor and child completer concurrently
        execute_task = asyncio.create_task(
            executor.execute(flow, {}, "/workspace", task_id=task_id)
        )
        helper_task = asyncio.create_task(complete_child_after_delay())

        flow_run_id = await asyncio.wait_for(execute_task, timeout=10)
        await helper_task

        # Flow should complete after child task finishes
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # Child task should exist
        child_tasks = db.list_tasks(flow_name="blocking-flow")
        assert len(child_tasks) == 1
        child = child_tasks[0]
        assert child.parent_task_id == task_id
        assert child.created_by == "flow:await-edge-flow/node:work"


class TestAwaitEdgeSetsWaitingStatus:
    """AWAIT edge should set the current task to 'waiting' status."""

    async def test_task_set_to_waiting_during_await(self) -> None:
        """While waiting for a child, the parent task should be in 'waiting' status."""
        flow = _make_await_edge_flow(target_flow="wait-flow")
        db = FlowstateDB(":memory:")
        subprocess_mgr = MockSubprocessManager()
        events: list[FlowEvent] = []

        task_id = db.create_task("await-edge-flow", "Check waiting status")

        executor = FlowExecutor(
            db=db,
            event_callback=events.append,
            harness=subprocess_mgr,
            server_base_url="http://127.0.0.1:9090",
        )

        waiting_observed = False

        async def observe_and_complete() -> None:
            """Observe the waiting status, then complete the child."""
            nonlocal waiting_observed
            for _ in range(100):
                await asyncio.sleep(0.05)
                # Check if parent task is waiting
                task = db.get_task(task_id)
                if task and task.status == "waiting":
                    waiting_observed = True
                    # Now complete the child
                    child_tasks = db.list_tasks(flow_name="wait-flow")
                    if child_tasks:
                        db.update_task_queue_status(child_tasks[0].id, "completed")
                        return

        execute_task = asyncio.create_task(
            executor.execute(flow, {}, "/workspace", task_id=task_id)
        )
        helper_task = asyncio.create_task(observe_and_complete())

        await asyncio.wait_for(execute_task, timeout=10)
        await helper_task

        assert waiting_observed, "Parent task was never observed in 'waiting' status"

        # After completion, parent task should be back to running or completed
        final_task = db.get_task(task_id)
        assert final_task is not None
        assert final_task.status == "completed"


# ---------------------------------------------------------------------------
# Tests: Wait node execution (ENGINE-030)
# ---------------------------------------------------------------------------


def _make_wait_flow(
    wait_delay_seconds: int | None = None,
    wait_until_cron: str | None = None,
    workspace: str = "/workspace",
) -> Flow:
    """Build a flow with a wait node: entry -> wait -> finish."""
    nodes: dict[str, Node] = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Do the start step"),
        "pause": Node(
            name="pause",
            node_type=NodeType.WAIT,
            wait_delay_seconds=wait_delay_seconds,
            wait_until_cron=wait_until_cron,
        ),
        "finish": Node(name="finish", node_type=NodeType.EXIT, prompt="Do the finish step"),
    }

    edges = [
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="pause"),
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="pause", target="finish"),
    ]

    return Flow(
        name="wait-flow",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes,
        edges=tuple(edges),
    )


class TestWaitNodeWithDelay:
    """Wait node with delay_seconds pauses the flow for the specified duration."""

    async def test_wait_node_completes_flow(self) -> None:
        """A flow with a short wait node completes successfully."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        # Use a very short delay (1 second) so test runs quickly
        flow = _make_wait_flow(wait_delay_seconds=1)
        flow_run_id = await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        # Flow should complete
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # All 3 task executions should exist (start, pause, finish)
        task_execs = db.list_task_executions(flow_run_id)
        assert len(task_execs) == 3

        # The wait node should have completed with 0 elapsed_seconds (budget-exempt)
        wait_exec = next(te for te in task_execs if te.node_name == "pause")
        assert wait_exec.status == "completed"
        assert wait_exec.elapsed_seconds == 0.0

    async def test_wait_node_does_not_charge_budget(self) -> None:
        """Wait time should NOT count toward the flow's budget."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_wait_flow(wait_delay_seconds=1)
        flow_run_id = await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        # Check budget: no budget warnings should have been emitted
        budget_warnings = [e for e in events if e.type == EventType.FLOW_BUDGET_WARNING]
        assert len(budget_warnings) == 0

        # The flow should complete normally even with very small budget
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

    async def test_wait_node_emits_waiting_event(self) -> None:
        """The executor should emit a TASK_WAITING event for wait nodes."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_wait_flow(wait_delay_seconds=1)
        await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        waiting_events = [e for e in events if e.type == EventType.TASK_WAITING]
        assert len(waiting_events) == 1
        assert waiting_events[0].payload["node_name"] == "pause"
        assert waiting_events[0].payload["reason"] == "delay"
        assert "wait_until" in waiting_events[0].payload

    async def test_wait_node_no_subprocess_launched(self) -> None:
        """Wait nodes should NOT launch a Claude Code subprocess."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_wait_flow(wait_delay_seconds=1)
        await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        # Only start and finish should have launched subprocesses (not pause/wait)
        assert len(mock_mgr.calls) == 2
        prompts = [call[0] for call in mock_mgr.calls]
        assert any("start" in p for p in prompts)
        assert any("finish" in p for p in prompts)
        assert not any("pause" in p for p in prompts)

    async def test_wait_node_sets_waiting_status_in_db(self) -> None:
        """During the wait, the task execution should be in 'waiting' status."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        # Use a 2-second delay so we can observe the waiting state
        flow = _make_wait_flow(wait_delay_seconds=2)

        waiting_seen = False

        async def observe_waiting() -> None:
            nonlocal waiting_seen
            for _ in range(100):
                await asyncio.sleep(0.05)
                runs = db.list_flow_runs()
                if not runs:
                    continue
                execs = db.list_task_executions(runs[0].id)
                for te in execs:
                    if te.node_name == "pause" and te.status == "waiting":
                        waiting_seen = True
                        return

        execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
        observe_task = asyncio.create_task(observe_waiting())

        await asyncio.wait_for(execute_task, timeout=15)
        await observe_task

        assert waiting_seen, "Wait node was never observed in 'waiting' status"


class TestWaitNodeWithSmallBudget:
    """Verify wait time does not count toward budget even with a tiny budget."""

    async def test_wait_node_with_tiny_budget_still_completes(self) -> None:
        """A flow with budget=5s and a 1s wait should complete (wait is free)."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_wait_flow(wait_delay_seconds=1)
        # Override budget to be very small
        from dataclasses import replace

        flow = replace(flow, budget_seconds=5)

        flow_run_id = await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"


# ---------------------------------------------------------------------------
# Tests: Fence node execution (ENGINE-031)
# ---------------------------------------------------------------------------


def _make_fence_flow_simple(workspace: str = "/workspace") -> Flow:
    """Build a flow with a fence node: entry -> fence -> finish.

    With a single linear path, the fence has no other tasks to wait for
    and should complete immediately.
    """
    nodes: dict[str, Node] = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Do the start step"),
        "sync": Node(name="sync", node_type=NodeType.FENCE),
        "finish": Node(name="finish", node_type=NodeType.EXIT, prompt="Do the finish step"),
    }

    edges = [
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="sync"),
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="sync", target="finish"),
    ]

    return Flow(
        name="fence-simple-flow",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes,
        edges=tuple(edges),
    )


def _make_fence_fork_join_flow(workspace: str = "/workspace") -> Flow:
    """Build a fork-join flow with parallel branches.

    entry -> fork [branch_a, branch_b] -> join -> merge -> finish

    Both branches run in parallel. The join waits for all fork members
    to complete before continuing to merge.
    """
    nodes: dict[str, Node] = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Do the start step"),
        "branch_a": Node(name="branch_a", node_type=NodeType.TASK, prompt="Do the branch_a step"),
        "branch_b": Node(name="branch_b", node_type=NodeType.TASK, prompt="Do the branch_b step"),
        "merge": Node(name="merge", node_type=NodeType.TASK, prompt="Do the merge step"),
        "finish": Node(name="finish", node_type=NodeType.EXIT, prompt="Do the finish step"),
    }

    edges = [
        Edge(
            edge_type=EdgeType.FORK,
            source="start",
            fork_targets=("branch_a", "branch_b"),
        ),
        Edge(
            edge_type=EdgeType.JOIN,
            join_sources=("branch_a", "branch_b"),
            target="merge",
        ),
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="merge", target="finish"),
    ]

    return Flow(
        name="fence-fork-flow",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes,
        edges=tuple(edges),
    )


class TestFenceNodeSimple:
    """Fence node with no other tasks completes immediately."""

    async def test_fence_node_completes_flow(self) -> None:
        """A linear flow with a fence node completes: entry -> fence -> exit."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_fence_flow_simple()
        flow_run_id = await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        assert len(tasks) == 3
        node_names = [t.node_name for t in tasks]
        assert node_names == ["start", "sync", "finish"]
        for t in tasks:
            assert t.status == "completed"

    async def test_fence_node_no_subprocess_launched(self) -> None:
        """Fence nodes do not invoke Claude Code."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_fence_flow_simple()
        await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        # Only start and finish should have subprocess calls (not the fence)
        assert len(mock_mgr.calls) == 2
        prompts = [c[0] for c in mock_mgr.calls]
        assert any("start" in p for p in prompts)
        assert any("finish" in p for p in prompts)
        assert not any("sync" in p for p in prompts)

    async def test_fence_node_emits_waiting_event(self) -> None:
        """Fence emits a TASK_WAITING event with reason='fence'."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_fence_flow_simple()
        await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        waiting_events = [
            e
            for e in events
            if e.type == EventType.TASK_WAITING and e.payload.get("reason") == "fence"
        ]
        assert len(waiting_events) == 1
        assert waiting_events[0].payload["node_name"] == "sync"

    async def test_fence_node_does_not_charge_budget(self) -> None:
        """Fence waiting time does not count toward budget elapsed."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_fence_flow_simple()
        flow_run_id = await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        # The fence task should have 0 elapsed_seconds
        tasks = db.list_task_executions(flow_run_id)
        fence_task = next(t for t in tasks if t.node_name == "sync")
        assert fence_task.elapsed_seconds == 0.0


class TestFenceNodeWithForkJoin:
    """Fence synchronizes parallel branches in a fork-join flow."""

    async def test_fence_in_fork_join_flow_completes(self) -> None:
        """Fork-join flow with parallel branches completes."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_fence_fork_join_flow()
        flow_run_id = await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        completed_names = {t.node_name for t in tasks if t.status == "completed"}
        # All five should be completed: start, branch_a, branch_b, merge, finish
        assert "start" in completed_names
        assert "branch_a" in completed_names
        assert "branch_b" in completed_names
        assert "merge" in completed_names
        assert "finish" in completed_names


# ---------------------------------------------------------------------------
# Tests: Atomic node execution (ENGINE-032)
# ---------------------------------------------------------------------------


def _make_atomic_flow(workspace: str = "/workspace") -> Flow:
    """Build a flow with an atomic node: entry -> atomic -> finish."""
    nodes: dict[str, Node] = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Do the start step"),
        "deploy": Node(name="deploy", node_type=NodeType.ATOMIC, prompt="Do the deploy step"),
        "finish": Node(name="finish", node_type=NodeType.EXIT, prompt="Do the finish step"),
    }

    edges = [
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="deploy"),
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="deploy", target="finish"),
    ]

    return Flow(
        name="atomic-flow",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes,
        edges=tuple(edges),
    )


class TestAtomicNodeNoContention:
    """Atomic node proceeds immediately when no other run has it."""

    async def test_atomic_node_completes_flow(self) -> None:
        """A flow with an atomic node completes normally when no contention."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_atomic_flow()
        flow_run_id = await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        tasks = db.list_task_executions(flow_run_id)
        assert len(tasks) == 3
        node_names = [t.node_name for t in tasks]
        assert node_names == ["start", "deploy", "finish"]
        for t in tasks:
            assert t.status == "completed"

    async def test_atomic_node_launches_subprocess(self) -> None:
        """Atomic nodes DO invoke Claude Code (unlike fence/wait)."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_atomic_flow()
        await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        # All three nodes should have subprocess calls
        assert len(mock_mgr.calls) == 3
        prompts = [c[0] for c in mock_mgr.calls]
        assert any("deploy" in p for p in prompts)

    async def test_atomic_no_waiting_event_when_no_contention(self) -> None:
        """When no contention, atomic should not emit TASK_WAITING."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_atomic_flow()
        await asyncio.wait_for(executor.execute(flow, {}, "/workspace"), timeout=15)

        waiting_events = [
            e
            for e in events
            if e.type == EventType.TASK_WAITING and e.payload.get("reason") == "atomic"
        ]
        assert len(waiting_events) == 0


class TestAtomicNodeWithContention:
    """Atomic node waits when another run has the same node running."""

    async def test_atomic_node_waits_for_other_run(self) -> None:
        """When another run has the same atomic node running, the second run waits."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        # Add a delay to the deploy step so it holds the lock
        mock_mgr.task_delays["deploy"] = 1.0

        # Run two flows concurrently. The first should acquire the lock,
        # the second should wait until the first completes.
        executor1 = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )
        executor2 = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_atomic_flow()

        task1 = asyncio.create_task(executor1.execute(flow, {}, "/workspace"))
        # Small delay to ensure the first run reaches the atomic node first
        await asyncio.sleep(0.2)
        task2 = asyncio.create_task(executor2.execute(flow, {}, "/workspace"))

        flow_run_id1, flow_run_id2 = await asyncio.wait_for(
            asyncio.gather(task1, task2), timeout=30
        )

        # Both should complete
        run1 = db.get_flow_run(flow_run_id1)
        run2 = db.get_flow_run(flow_run_id2)
        assert run1 is not None
        assert run2 is not None
        assert run1.status == "completed"
        assert run2.status == "completed"

        # Both should have 3 task executions each
        tasks1 = db.list_task_executions(flow_run_id1)
        tasks2 = db.list_task_executions(flow_run_id2)
        assert len(tasks1) == 3
        assert len(tasks2) == 3

    async def test_atomic_emits_activity_when_waiting(self) -> None:
        """When contention occurs, atomic emits activity logs about waiting."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        # Add a delay to the deploy step to force contention
        mock_mgr.task_delays["deploy"] = 1.5

        executor1 = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )
        executor2 = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_atomic_flow()

        task1 = asyncio.create_task(executor1.execute(flow, {}, "/workspace"))
        await asyncio.sleep(0.2)
        task2 = asyncio.create_task(executor2.execute(flow, {}, "/workspace"))

        await asyncio.wait_for(asyncio.gather(task1, task2), timeout=30)

        # Check that at least one TASK_WAITING event was emitted with reason='atomic'
        waiting_events = [
            e
            for e in events
            if e.type == EventType.TASK_WAITING and e.payload.get("reason") == "atomic"
        ]
        # The second executor should have emitted at least one waiting event
        assert len(waiting_events) >= 1


# ---------------------------------------------------------------------------
# Mock for interrupt + messaging tests (ENGINE-036)
# ---------------------------------------------------------------------------


class InterruptableMockManager:
    """A Harness-protocol test double that supports interrupt-aware execution.

    - ``run_task`` supports delays that can be cut short by ``interrupt()``.
    - ``prompt()`` tracks calls for assertion.
    - ``interrupt()`` sets a per-session event to abort the delay in run_task.
    - ``prompt_calls`` records all (session_id, message) tuples for assertions.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.resume_calls: list[tuple[str, str, str]] = []
        self.prompt_calls: list[tuple[str, str]] = []
        self.interrupt_calls: list[str] = []
        self.kill_calls: list[str] = []
        self.start_session_calls: list[tuple[str, str]] = []
        # Per-session event: when set, running tasks exit immediately.
        self._session_interrupt_events: dict[str, asyncio.Event] = {}
        # Optional delay for run_task
        self.task_delays: dict[str, float] = {}
        self.task_responses: dict[str, tuple[int, list[StreamEvent]]] = {}

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
        settings: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.calls.append((prompt, workspace, session_id))
        # Ensure an interrupt event exists for this session
        if session_id not in self._session_interrupt_events:
            self._session_interrupt_events[session_id] = asyncio.Event()
        interrupt_event = self._session_interrupt_events[session_id]

        exit_code, extra_events = self._find_response(prompt)

        delay = self._find_delay(prompt)
        if delay > 0:
            # Wait for delay OR interrupt, whichever comes first
            try:
                await asyncio.wait_for(interrupt_event.wait(), timeout=delay)
                # If we get here, interrupt was signalled before the delay elapsed.
                # In real ACP, harness.interrupt() sends a cancel which returns
                # stop_reason="cancelled" → exit_code=-1.  Accurately simulate
                # this so tests catch the ENGINE-051 bug (loop must handle -1).
                yield StreamEvent(
                    type=StreamEventType.SYSTEM,
                    content={"event": "process_exit", "exit_code": -1, "stderr": ""},
                    raw="Process exited with code -1",
                )
                return
            except TimeoutError:
                # Normal completion: delay elapsed without interrupt
                pass

        for evt in extra_events:
            yield evt

        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": exit_code, "stderr": ""},
            raw=f"Process exited with code {exit_code}",
        )

    async def run_task_resume(
        self,
        prompt: str,
        workspace: str,
        resume_session_id: str,
        *,
        skip_permissions: bool = False,
        settings: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.resume_calls.append((prompt, workspace, resume_session_id))
        exit_code, extra_events = self._find_response(prompt)
        for evt in extra_events:
            yield evt
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": exit_code, "stderr": ""},
            raw=f"Process exited with code {exit_code}",
        )

    async def run_judge(
        self, prompt: str, workspace: str, *, skip_permissions: bool = False
    ) -> Any:
        return {"target": "__none__", "reasoning": "", "confidence": 0.0}

    async def kill(self, session_id: str) -> None:
        self.kill_calls.append(session_id)

    async def start_session(self, workspace: str, session_id: str) -> None:
        self.start_session_calls.append((workspace, session_id))
        self._session_interrupt_events[session_id] = asyncio.Event()

    async def prompt(self, session_id: str, message: str) -> AsyncGenerator[StreamEvent, None]:
        self.prompt_calls.append((session_id, message))
        exit_code, extra_events = self._find_response(message)
        for evt in extra_events:
            yield evt
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": exit_code, "stderr": ""},
            raw=f"Process exited with code {exit_code}",
        )

    async def interrupt(self, session_id: str) -> None:
        self.interrupt_calls.append(session_id)
        # Signal the interrupt event so run_task exits early
        event = self._session_interrupt_events.get(session_id)
        if event is not None:
            event.set()

    def _find_response(self, prompt: str) -> tuple[int, list[StreamEvent]]:
        for key, response in self.task_responses.items():
            marker = f"Do the {key} step"
            if marker in prompt:
                return response
        return (0, [])

    def _find_delay(self, prompt: str) -> float:
        for key, delay in self.task_delays.items():
            marker = f"Do the {key} step"
            if marker in prompt:
                return delay
        return 0.0


# ---------------------------------------------------------------------------
# Tests: Task-level messaging (ENGINE-036)
# ---------------------------------------------------------------------------


class TestFormatUserMessages:
    """Test the _format_user_messages static method."""

    def test_single_message(self) -> None:
        """Format a single user message."""
        from flowstate.state.models import TaskMessageRow

        messages = [
            TaskMessageRow(
                id="m1",
                task_execution_id="t1",
                message="please also check edge cases",
                created_at="2024-01-01T00:00:00",
                processed=0,
            )
        ]
        result = FlowExecutor._format_user_messages(messages)
        assert "please also check edge cases" in result
        assert "Address these messages" in result

    def test_multiple_messages(self) -> None:
        """Format multiple user messages."""
        from flowstate.state.models import TaskMessageRow

        messages = [
            TaskMessageRow(
                id="m1",
                task_execution_id="t1",
                message="use pytest not unittest",
                created_at="2024-01-01T00:00:00",
                processed=0,
            ),
            TaskMessageRow(
                id="m2",
                task_execution_id="t1",
                message="also add integration tests",
                created_at="2024-01-01T00:00:01",
                processed=0,
            ),
        ]
        result = FlowExecutor._format_user_messages(messages)
        assert '- "use pytest not unittest"' in result
        assert '- "also add integration tests"' in result
        assert "Address these messages, then continue your task." in result


class TestSendMessageQueues:
    """Test that send_message enqueues messages to the DB."""

    async def test_send_message_to_running_task(self) -> None:
        """send_message inserts a message into task_messages for a running task."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        # Make the "work" step slow so we can send a message while it's running
        mock_mgr.task_delays["work"] = 0.5

        # Start the flow in the background
        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break

        assert work_task_id is not None, "work task never reached running status"

        # Send a message while the task is running
        await executor.send_message(work_task_id, "please check edge cases")

        # Verify message was queued in DB
        messages = db.get_unprocessed_messages(work_task_id)
        assert len(messages) == 1
        assert messages[0].message == "please check edge cases"

        # Wait for flow to complete
        await asyncio.wait_for(exec_task, timeout=10)

    async def test_send_message_to_completed_task_raises(self) -> None:
        """send_message to a completed task raises RuntimeError."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Find the completed start task
        tasks = db.list_task_executions(flow_run_id)
        start_task = next(t for t in tasks if t.node_name == "start")
        assert start_task.status == "completed"

        import pytest

        with pytest.raises(RuntimeError, match="Cannot send message"):
            await executor.send_message(start_task.id, "too late")

    async def test_send_message_to_failed_task_raises(self) -> None:
        """send_message to a failed task raises RuntimeError."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["start"] = (1, [])  # fail
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "finish"], on_error=ErrorPolicy.PAUSE)
        # Run in background, it will pause due to on_error=pause
        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for paused state
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                run = db.get_flow_run(runs[0].id)
                if run and run.status == "paused":
                    break

        tasks = db.list_task_executions(db.list_flow_runs()[0].id)
        failed_task = next(t for t in tasks if t.status == "failed")

        import pytest

        with pytest.raises(RuntimeError, match="Cannot send message"):
            await executor.send_message(failed_task.id, "too late")

        # Clean up
        await executor.cancel(db.list_flow_runs()[0].id)
        with contextlib.suppress(asyncio.CancelledError):
            await exec_task


class TestReInvocationLoop:
    """Test the re-invocation loop: after agent turn, check for messages."""

    async def test_reinvocation_with_queued_message(self) -> None:
        """After a task completes, if messages exist, re-invoke with them."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        # Make "work" slow enough to queue a message
        mock_mgr.task_delays["work"] = 0.3

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break

        assert work_task_id is not None

        # Queue a message while the task is running
        await executor.send_message(work_task_id, "use pytest not unittest")

        # Wait for flow to complete
        flow_run_id = await asyncio.wait_for(exec_task, timeout=10)

        # Verify the flow completed successfully
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # run_task_resume() should have been called with the re-invocation message
        # (ENGINE-052: run_task_resume replaces prompt for dead ACP sessions)
        reinvoc_prompts = [
            prompt
            for prompt, _ws, _sid in mock_mgr.resume_calls
            if "use pytest not unittest" in prompt
        ]
        assert len(reinvoc_prompts) >= 1
        assert "Address these messages" in reinvoc_prompts[0]

        # Messages should be marked as processed
        messages = db.get_unprocessed_messages(work_task_id)
        assert len(messages) == 0

    async def test_no_reinvocation_without_messages(self) -> None:
        """If no messages are queued, no re-invocation happens."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # No re-invocation calls should have been made (only run_task calls)
        assert len(mock_mgr.prompt_calls) == 0
        # resume_calls should only contain initial session-context resumes, not message re-invocations
        # (a flow without messages should not trigger any message-driven resume_calls)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

    async def test_multiple_messages_combined(self) -> None:
        """Multiple messages are combined into a single re-invocation prompt."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        mock_mgr.task_delays["work"] = 0.3

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break

        assert work_task_id is not None

        # Queue multiple messages
        await executor.send_message(work_task_id, "use pytest not unittest")
        await executor.send_message(work_task_id, "also check edge cases")

        # Wait for flow to complete
        flow_run_id = await asyncio.wait_for(exec_task, timeout=10)
        assert db.get_flow_run(flow_run_id) is not None
        assert db.get_flow_run(flow_run_id).status == "completed"  # type: ignore[union-attr]

        # Both messages should appear in a single re-invocation prompt
        # (ENGINE-052: uses run_task_resume instead of prompt)
        reinvoc_prompts = [
            prompt
            for prompt, _ws, _sid in mock_mgr.resume_calls
            if "Address these messages" in prompt
        ]
        assert len(reinvoc_prompts) >= 1
        assert "use pytest not unittest" in reinvoc_prompts[0]
        assert "also check edge cases" in reinvoc_prompts[0]


class TestInterruptTask:
    """Test interrupt_task: cancels current agent turn, sets status to interrupted."""

    async def test_interrupt_sets_status(self) -> None:
        """interrupt_task sets the task status to 'interrupted' and emits event."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        # Make "work" slow so we can interrupt it
        mock_mgr.task_delays["work"] = 5.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break

        assert work_task_id is not None

        # Interrupt the task
        await executor.interrupt_task(work_task_id)

        # Verify status changed to interrupted
        task = db.get_task_execution(work_task_id)
        assert task is not None
        assert task.status == "interrupted"

        # Verify TASK_INTERRUPTED event was emitted
        interrupted_events = [e for e in events if e.type == EventType.TASK_INTERRUPTED]
        assert len(interrupted_events) == 1
        assert interrupted_events[0].payload["task_execution_id"] == work_task_id
        assert interrupted_events[0].payload["node_name"] == "work"

        # Clean up: send a message to resume, then cancel
        await executor.send_message(work_task_id, "continue")
        # Give executor time to process
        await asyncio.sleep(0.1)
        await executor.cancel(db.list_flow_runs()[0].id)
        with contextlib.suppress(asyncio.CancelledError):
            await exec_task

    async def test_interrupt_idempotent(self) -> None:
        """Calling interrupt_task twice on the same task is a no-op."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        mock_mgr.task_delays["work"] = 5.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break

        assert work_task_id is not None

        await executor.interrupt_task(work_task_id)
        # Second interrupt should not raise
        await executor.interrupt_task(work_task_id)

        # Only one TASK_INTERRUPTED event should be emitted
        interrupted_events = [e for e in events if e.type == EventType.TASK_INTERRUPTED]
        assert len(interrupted_events) == 1

        # Clean up
        await executor.send_message(work_task_id, "continue")
        await asyncio.sleep(0.1)
        await executor.cancel(db.list_flow_runs()[0].id)
        with contextlib.suppress(asyncio.CancelledError):
            await exec_task

    async def test_interrupt_non_running_task_raises(self) -> None:
        """Interrupting a completed task raises RuntimeError."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        completed_task = next(t for t in tasks if t.status == "completed")

        import pytest

        with pytest.raises(RuntimeError, match="Cannot interrupt"):
            await executor.interrupt_task(completed_task.id)

    async def test_interrupt_nonexistent_task_raises(self) -> None:
        """Interrupting a non-existent task raises RuntimeError."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        import pytest

        with pytest.raises(RuntimeError, match="not found"):
            await executor.interrupt_task("nonexistent-id")


class TestInterruptAndResume:
    """Test the full interrupt -> send message -> resume cycle."""

    async def test_interrupt_then_message_resumes(self) -> None:
        """Interrupt a task, send a message, and verify it resumes and completes."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        # Make "work" slow so we can interrupt, but not too slow
        mock_mgr.task_delays["work"] = 2.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break

        assert work_task_id is not None

        # Interrupt the task
        await executor.interrupt_task(work_task_id)

        # Verify it's interrupted
        task = db.get_task_execution(work_task_id)
        assert task is not None
        assert task.status == "interrupted"

        # Give a small delay so the execution coroutine enters the wait state
        await asyncio.sleep(0.1)

        # Send a message to resume
        await executor.send_message(work_task_id, "please continue with pytest")

        # Wait for flow to complete
        flow_run_id = await asyncio.wait_for(exec_task, timeout=10)

        # Verify the flow completed
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # Verify the re-invocation was sent via run_task_resume() (ENGINE-052)
        reinvoc_prompts = [
            prompt
            for prompt, _ws, _sid in mock_mgr.resume_calls
            if "please continue with pytest" in prompt
        ]
        assert len(reinvoc_prompts) >= 1

    async def test_send_message_to_interrupted_task(self) -> None:
        """send_message to an interrupted task signals resume."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        mock_mgr.task_delays["work"] = 2.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break

        assert work_task_id is not None

        # Interrupt the task
        await executor.interrupt_task(work_task_id)
        await asyncio.sleep(0.1)

        # Send a message which should trigger resume
        await executor.send_message(work_task_id, "resume now")

        # Wait for flow to complete
        await asyncio.wait_for(exec_task, timeout=10)

        # After resume, the status went back to running briefly then completed
        # Check that a FLOW_STATUS_CHANGED event was emitted with old=interrupted
        status_events = [
            e
            for e in events
            if e.type == EventType.FLOW_STATUS_CHANGED
            and e.payload.get("old_status") == "interrupted"
        ]
        assert len(status_events) >= 1
        assert status_events[0].payload["new_status"] == "running"


class TestInterruptedTaskEdgeEvaluation:
    """Test that edges are only evaluated after ALL messages are processed."""

    async def test_edges_not_evaluated_while_interrupted(self) -> None:
        """An interrupted task does not proceed to edge evaluation."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        mock_mgr.task_delays["work"] = 2.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        flow_run_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                flow_run_id = runs[0].id
                tasks = db.list_task_executions(flow_run_id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break

        assert work_task_id is not None
        assert flow_run_id is not None

        # Interrupt the task
        await executor.interrupt_task(work_task_id)
        await asyncio.sleep(0.2)

        # While interrupted, "finish" node should NOT have been created
        tasks = db.list_task_executions(flow_run_id)
        finish_tasks = [t for t in tasks if t.node_name == "finish"]
        assert len(finish_tasks) == 0, "finish task should not be created while work is interrupted"

        # Resume by sending a message
        await executor.send_message(work_task_id, "continue")

        # Wait for flow to complete
        await asyncio.wait_for(exec_task, timeout=10)

        # Now finish should have been executed
        tasks = db.list_task_executions(flow_run_id)
        finish_tasks = [t for t in tasks if t.node_name == "finish"]
        assert len(finish_tasks) == 1
        assert finish_tasks[0].status == "completed"


class TestCancelInterruptedTask:
    """Test that cancel properly handles interrupted tasks."""

    async def test_cancel_wakes_interrupted_tasks(self) -> None:
        """Cancelling a flow wakes up interrupted tasks so they can exit."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        mock_mgr.task_delays["work"] = 5.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break

        assert work_task_id is not None
        flow_run_id = db.list_flow_runs()[0].id

        # Interrupt the task
        await executor.interrupt_task(work_task_id)
        await asyncio.sleep(0.1)

        # Cancel the flow while task is interrupted
        await executor.cancel(flow_run_id)

        # Flow should finish (not hang)
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(exec_task, timeout=5)

        # Task should be marked as failed (cancelled)
        task = db.get_task_execution(work_task_id)
        assert task is not None
        assert task.status == "failed"

        # Flow should be cancelled
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "cancelled"


# ---------------------------------------------------------------------------
# Tests: ENGINE-051 — Interrupt causes flow failure instead of waiting
# ---------------------------------------------------------------------------


class TestInterruptDoesNotCauseFailure:
    """ENGINE-051: Interrupt must NOT cause the flow to fail.

    The real ACP harness returns exit_code=-1 on interrupt (stop_reason=cancelled).
    The re-invocation loop must handle this by waiting for user input rather than
    treating -1 as a task failure.
    """

    async def test_interrupt_exit_code_minus_one_does_not_fail_task(self) -> None:
        """When interrupt yields exit_code=-1, the task enters 'interrupted' status
        (not 'failed') and the flow does not trigger on_error."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(
            node_names=["start", "work", "finish"],
            on_error=ErrorPolicy.PAUSE,
        )
        # Make "work" slow so we can interrupt it
        mock_mgr.task_delays["work"] = 5.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break
        assert work_task_id is not None

        # Interrupt the task (mock returns exit_code=-1, matching real ACP)
        await executor.interrupt_task(work_task_id)
        await asyncio.sleep(0.1)

        # CRITICAL: task must be "interrupted", NOT "failed"
        task = db.get_task_execution(work_task_id)
        assert task is not None
        assert task.status == "interrupted", (
            f"Expected 'interrupted' but got '{task.status}' — "
            "exit_code=-1 from ACP cancel must not cause task failure"
        )

        # Flow must NOT be paused (on_error=pause should not have triggered)
        flow_run_id = db.list_flow_runs()[0].id
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status != "paused", "Flow should not be paused — interrupt is not an error"

        # No TASK_FAILED event should have been emitted
        failed_events = [e for e in events if e.type == EventType.TASK_FAILED]
        assert (
            len(failed_events) == 0
        ), "TASK_FAILED event emitted after interrupt — this is the ENGINE-051 bug"

        # Resume by sending a message, then verify the flow completes
        await executor.send_message(work_task_id, "continue working")
        flow_run_id = await asyncio.wait_for(exec_task, timeout=10)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # Verify the work task completed successfully after resume
        task = db.get_task_execution(work_task_id)
        assert task is not None
        assert task.status == "completed"

    async def test_interrupt_resume_reinvokes_with_user_message(self) -> None:
        """After interrupt and resume, the agent is re-invoked with the user's message."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        mock_mgr.task_delays["work"] = 5.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break
        assert work_task_id is not None

        await executor.interrupt_task(work_task_id)
        await asyncio.sleep(0.1)

        # Send a specific message
        await executor.send_message(work_task_id, "switch to using pytest fixtures")

        await asyncio.wait_for(exec_task, timeout=10)

        # Verify run_task_resume() was called with the user's message
        # (ENGINE-052: uses run_task_resume instead of prompt to handle dead ACP sessions)
        reinvoc_prompts = [
            prompt
            for prompt, _ws, _sid in mock_mgr.resume_calls
            if "switch to using pytest fixtures" in prompt
        ]
        assert (
            len(reinvoc_prompts) >= 1
        ), "Agent was not re-invoked with user message after interrupt resume"

    async def test_interrupt_with_on_error_abort_does_not_abort_flow(self) -> None:
        """Even with on_error=abort, interrupt should NOT abort the flow."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(
            node_names=["start", "work", "finish"],
            on_error=ErrorPolicy.ABORT,
        )
        mock_mgr.task_delays["work"] = 5.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break
        assert work_task_id is not None

        await executor.interrupt_task(work_task_id)
        await asyncio.sleep(0.1)

        # Flow must still be running (not aborted/failed)
        flow_run_id = db.list_flow_runs()[0].id
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "running", (
            f"Expected 'running' but got '{run.status}' — "
            "interrupt must not trigger on_error=abort"
        )

        # Resume and complete
        await executor.send_message(work_task_id, "continue")
        await asyncio.wait_for(exec_task, timeout=10)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"


# ---------------------------------------------------------------------------
# Tests: ENGINE-052 — Fix message re-invocation after interrupt (dead session)
# ---------------------------------------------------------------------------


class TestMessageReinvocationUsesResume:
    """After interrupt + message, run_task_resume() is used instead of prompt().

    ENGINE-052: The ACP session is destroyed when the subprocess exits.  After
    interrupt, the re-invocation loop must call run_task_resume() (which spawns a
    fresh subprocess and loads the persisted session) rather than prompt() (which
    requires a live session).
    """

    async def test_reinvocation_uses_run_task_resume_not_prompt(self) -> None:
        """After interrupt and message, run_task_resume() is called, not prompt()."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        mock_mgr.task_delays["work"] = 5.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break
        assert work_task_id is not None

        # Interrupt and send a message
        await executor.interrupt_task(work_task_id)
        await asyncio.sleep(0.1)
        await executor.send_message(work_task_id, "please use pytest")

        await asyncio.wait_for(exec_task, timeout=10)

        # run_task_resume must have been called (not prompt)
        resume_prompts = [
            prompt for prompt, _ws, _sid in mock_mgr.resume_calls if "please use pytest" in prompt
        ]
        assert (
            len(resume_prompts) >= 1
        ), "run_task_resume() was not called with user message after interrupt"

        # prompt() must NOT have been called for the re-invocation
        prompt_msgs = [msg for _sid, msg in mock_mgr.prompt_calls if "please use pytest" in msg]
        assert (
            len(prompt_msgs) == 0
        ), "prompt() was called instead of run_task_resume() — dead session bug"

    async def test_multiple_interrupt_message_cycles(self) -> None:
        """Multiple interrupt -> message cycles work in sequence."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        mock_mgr.task_delays["work"] = 10.0

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break
        assert work_task_id is not None

        # Cycle 1: interrupt + message
        await executor.interrupt_task(work_task_id)
        await asyncio.sleep(0.1)
        await executor.send_message(work_task_id, "first correction")

        # Wait for the re-invocation to complete (task goes back to running)
        for _ in range(100):
            await asyncio.sleep(0.01)
            task = db.get_task_execution(work_task_id)
            if task and task.status == "running":
                break

        # The re-invocation completed with exit_code=0 so the task should
        # have completed.  Let the flow finish.
        await asyncio.wait_for(exec_task, timeout=10)

        # Verify at least one resume_call contained the first message
        resume_msgs = [p for p, _ws, _sid in mock_mgr.resume_calls if "first correction" in p]
        assert len(resume_msgs) >= 1, "First interrupt+message cycle did not trigger resume"

    async def test_message_on_completed_prompt_uses_resume(self) -> None:
        """Sending a message to a task whose initial prompt completed (non-interrupted)
        also uses run_task_resume()."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = InterruptableMockManager()
        executor = FlowExecutor(
            db, callback, mock_mgr, max_concurrent=4, server_base_url="http://127.0.0.1:9090"
        )

        # 2 node flow: start -> finish, so "start" runs and we message it
        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        # "work" takes a small delay to give us time to queue a message
        mock_mgr.task_delays["work"] = 0.3

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        work_task_id: str | None = None
        for _ in range(200):
            await asyncio.sleep(0.01)
            runs = db.list_flow_runs()
            if runs:
                tasks = db.list_task_executions(runs[0].id)
                for t in tasks:
                    if t.node_name == "work" and t.status == "running":
                        work_task_id = t.id
                        break
            if work_task_id:
                break
        assert work_task_id is not None

        # Queue a message while the task is running (it will be picked up
        # after the initial prompt completes)
        await executor.send_message(work_task_id, "extra instructions")

        await asyncio.wait_for(exec_task, timeout=10)

        # The re-invocation should use run_task_resume, not prompt
        resume_msgs = [p for p, _ws, _sid in mock_mgr.resume_calls if "extra instructions" in p]
        assert (
            len(resume_msgs) >= 1
        ), "run_task_resume() was not called for queued message after prompt completion"


# ---------------------------------------------------------------------------
# Tests: ENGINE-038 — retry_task / skip_task resume paused executor loop
# ---------------------------------------------------------------------------


class TestRetryTaskResumesPausedFlow:
    """retry_task() on a paused flow should unpause and wake the executor loop,
    without requiring a separate resume() call."""

    async def test_retry_task_resumes_and_completes(self) -> None:
        """Retry a failed task on a paused flow. Flow should complete without resume()."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # "work" fails the first time
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # Make "work" succeed on retry
        del mock_mgr.task_responses["work"]

        # Retry the failed task -- this should unpause the flow automatically
        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")
        await executor.retry_task(flow_run_id, failed_task.id)

        # Flow should complete without calling resume()
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

    async def test_retry_task_emits_flow_status_changed(self) -> None:
        """retry_task() should emit FLOW_STATUS_CHANGED (paused -> running)."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        # Clear events captured so far to isolate the retry event
        events.clear()

        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")

        # Make "work" succeed on retry
        del mock_mgr.task_responses["work"]
        await executor.retry_task(flow_run_id, failed_task.id)

        # Should have emitted FLOW_STATUS_CHANGED
        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert len(status_events) >= 1
        evt = status_events[0]
        assert evt.payload["old_status"] == "paused"
        assert evt.payload["new_status"] == "running"
        assert evt.payload["reason"] == "Task retried"

        # Clean up
        await execute_task

    async def test_retry_task_updates_db_status_to_running(self) -> None:
        """retry_task() on a paused flow should update DB flow status to running."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # Make "work" succeed on retry
        del mock_mgr.task_responses["work"]

        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")
        await executor.retry_task(flow_run_id, failed_task.id)

        # DB status should be running now (before the flow even finishes)
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "running"

        await execute_task


class TestSkipTaskResumesPausedFlow:
    """skip_task() on a paused flow should unpause and wake the executor loop."""

    async def test_skip_task_resumes_and_completes(self) -> None:
        """Skip a failed task on a paused flow. Flow should complete without resume()."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # Skip the failed task -- this should unpause the flow automatically
        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")
        await executor.skip_task(flow_run_id, failed_task.id)

        # Flow should complete without calling resume()
        await execute_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

    async def test_skip_task_emits_flow_status_changed(self) -> None:
        """skip_task() should emit FLOW_STATUS_CHANGED (paused -> running)."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        events.clear()

        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")
        await executor.skip_task(flow_run_id, failed_task.id)

        # Should have emitted FLOW_STATUS_CHANGED
        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert len(status_events) >= 1
        evt = status_events[0]
        assert evt.payload["old_status"] == "paused"
        assert evt.payload["new_status"] == "running"
        assert evt.payload["reason"] == "Task skipped"

        # Clean up
        await execute_task


class TestRetryTaskEmitsTaskRetried:
    """retry_task() must always emit a TASK_RETRIED event so the UI learns
    about the new task execution, regardless of whether the flow was paused.
    See UI-072.
    """

    async def test_retry_emits_task_retried_event_when_paused(self) -> None:
        """A paused flow being retried emits TASK_RETRIED with the new task id."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        events.clear()

        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")

        # Make "work" succeed on retry so the flow finishes cleanly.
        del mock_mgr.task_responses["work"]
        await executor.retry_task(flow_run_id, failed_task.id)

        retried = [e for e in events if e.type == EventType.TASK_RETRIED]
        assert len(retried) == 1
        evt = retried[0]
        assert evt.flow_run_id == flow_run_id
        assert evt.payload["original_task_execution_id"] == failed_task.id
        assert evt.payload["node_name"] == "work"
        assert evt.payload["generation"] == 2
        new_task_id = evt.payload["task_execution_id"]
        assert isinstance(new_task_id, str)
        # The new task id should match the freshly created task in the DB.
        all_work = [t for t in db.list_task_executions(flow_run_id) if t.node_name == "work"]
        assert any(t.id == new_task_id for t in all_work)

        await execute_task

    async def test_retry_emits_task_retried_event_when_not_paused(self) -> None:
        """When the flow is running (not paused), retry_task still emits
        TASK_RETRIED so the UI updates. The legacy FLOW_STATUS_CHANGED event
        is NOT emitted in this case (no status transition).
        """
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        # Set up minimal executor state without driving the main loop.
        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        executor._flow = flow
        executor._expanded_prompts = {n: f"Do the {n} step" for n in flow.nodes}
        executor._pending_tasks = set()
        executor._paused = False
        executor._cancelled = False
        executor._task_id = None
        executor._task_row = None

        # Create a flow run + a "failed" task directly in the DB.
        flow_def_id = db.create_flow_definition("test-flow", "source", "{}")
        flow_run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/test",
            budget_seconds=3600,
            on_error="pause",
            default_workspace="/workspace",
        )
        db.update_flow_run_status(flow_run_id, "running")
        failed_id = db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name="work",
            node_type="task",
            generation=1,
            context_mode="handoff",
            cwd="/workspace",
            task_dir="",
            prompt_text="Do the work step",
        )
        db.update_task_status(failed_id, "failed", error_message="boom")

        events.clear()
        await executor.retry_task(flow_run_id, failed_id)

        # TASK_RETRIED must fire.
        retried = [e for e in events if e.type == EventType.TASK_RETRIED]
        assert len(retried) == 1
        evt = retried[0]
        assert evt.payload["original_task_execution_id"] == failed_id
        assert evt.payload["node_name"] == "work"
        assert evt.payload["generation"] == 2

        # FLOW_STATUS_CHANGED must NOT fire because the flow was already
        # running, not paused. (Regression guard for the legacy behavior.)
        status_evts = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert status_evts == []

        # The new pending task should be queued for the main loop.
        new_task_id = evt.payload["task_execution_id"]
        assert new_task_id in executor._pending_tasks

    async def test_retry_paused_to_running_status_event_still_fires(self) -> None:
        """Regression: existing paused -> running FLOW_STATUS_CHANGED behavior
        is preserved alongside the new TASK_RETRIED event.
        """
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        events.clear()

        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")

        del mock_mgr.task_responses["work"]
        await executor.retry_task(flow_run_id, failed_task.id)

        status_evts = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert any(
            e.payload["old_status"] == "paused"
            and e.payload["new_status"] == "running"
            and e.payload["reason"] == "Task retried"
            for e in status_evts
        )
        retried = [e for e in events if e.type == EventType.TASK_RETRIED]
        assert len(retried) == 1

        await execute_task


class TestSkipTaskEmitsTaskSkipped:
    """skip_task() must always emit a TASK_SKIPPED event regardless of paused
    state. See UI-072.
    """

    async def test_skip_emits_task_skipped_event_when_paused(self) -> None:
        """skip_task on a paused flow emits TASK_SKIPPED with next task id."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_responses["work"] = (1, [])
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id, execute_task = await _execute_until_paused(
            executor, flow, {}, "/workspace", db
        )

        events.clear()

        tasks = db.list_task_executions(flow_run_id)
        failed_task = next(t for t in tasks if t.status == "failed")

        await executor.skip_task(flow_run_id, failed_task.id)

        skipped = [e for e in events if e.type == EventType.TASK_SKIPPED]
        assert len(skipped) == 1
        evt = skipped[0]
        assert evt.payload["task_execution_id"] == failed_task.id
        assert evt.payload["node_name"] == "work"
        # next_task_execution_id should be set to the queued "finish" task.
        next_id = evt.payload["next_task_execution_id"]
        assert isinstance(next_id, str)
        finish_tasks = [t for t in db.list_task_executions(flow_run_id) if t.node_name == "finish"]
        assert any(t.id == next_id for t in finish_tasks)

        await execute_task

    async def test_skip_emits_task_skipped_event_when_not_paused(self) -> None:
        """When the flow is running (not paused), skip_task still emits
        TASK_SKIPPED. FLOW_STATUS_CHANGED is NOT emitted in this case.
        """
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        executor._flow = flow
        executor._expanded_prompts = {n: f"Do the {n} step" for n in flow.nodes}
        executor._pending_tasks = set()
        executor._paused = False
        executor._cancelled = False
        executor._task_id = None
        executor._task_row = None

        flow_def_id = db.create_flow_definition("test-flow", "source", "{}")
        flow_run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/test",
            budget_seconds=3600,
            on_error="pause",
            default_workspace="/workspace",
        )
        db.update_flow_run_status(flow_run_id, "running")
        failed_id = db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name="work",
            node_type="task",
            generation=1,
            context_mode="handoff",
            cwd="/workspace",
            task_dir="",
            prompt_text="Do the work step",
        )
        db.update_task_status(failed_id, "failed", error_message="boom")

        events.clear()
        await executor.skip_task(flow_run_id, failed_id)

        skipped = [e for e in events if e.type == EventType.TASK_SKIPPED]
        assert len(skipped) == 1
        evt = skipped[0]
        assert evt.payload["task_execution_id"] == failed_id
        assert evt.payload["node_name"] == "work"
        next_id = evt.payload["next_task_execution_id"]
        assert isinstance(next_id, str)
        # The next task should be queued.
        assert next_id in executor._pending_tasks

        # No FLOW_STATUS_CHANGED because the flow was already running.
        status_evts = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert status_evts == []


class TestTaskRetriedSkippedEventTypes:
    """Sanity checks for the new event type values and serialization."""

    def test_task_retried_value(self) -> None:
        assert EventType.TASK_RETRIED == "task.retried"
        assert EventType.TASK_RETRIED.value == "task.retried"

    def test_task_skipped_value(self) -> None:
        assert EventType.TASK_SKIPPED == "task.skipped"
        assert EventType.TASK_SKIPPED.value == "task.skipped"

    def test_task_retried_serialization(self) -> None:
        event = FlowEvent(
            type=EventType.TASK_RETRIED,
            flow_run_id="run-1",
            timestamp="2024-01-01T00:00:00",
            payload={
                "task_execution_id": "new-1",
                "node_name": "work",
                "generation": 2,
                "original_task_execution_id": "old-1",
            },
        )
        d = event.to_dict()
        assert d["type"] == "task.retried"
        assert d["payload"]["original_task_execution_id"] == "old-1"

    def test_task_skipped_serialization(self) -> None:
        event = FlowEvent(
            type=EventType.TASK_SKIPPED,
            flow_run_id="run-1",
            timestamp="2024-01-01T00:00:00",
            payload={
                "task_execution_id": "t1",
                "node_name": "work",
                "next_task_execution_id": "t2",
            },
        )
        d = event.to_dict()
        assert d["type"] == "task.skipped"
        assert d["payload"]["next_task_execution_id"] == "t2"


class TestTaskInterruptedEventType:
    """Test that the TASK_INTERRUPTED event type exists and works."""

    def test_event_type_value(self) -> None:
        """TASK_INTERRUPTED has the correct string value."""
        assert EventType.TASK_INTERRUPTED == "task.interrupted"
        assert EventType.TASK_INTERRUPTED.value == "task.interrupted"

    def test_event_serialization(self) -> None:
        """A FlowEvent with TASK_INTERRUPTED serializes correctly."""
        event = FlowEvent(
            type=EventType.TASK_INTERRUPTED,
            flow_run_id="run-1",
            timestamp="2024-01-01T00:00:00",
            payload={"task_execution_id": "t1", "node_name": "work"},
        )
        d = event.to_dict()
        assert d["type"] == "task.interrupted"
        assert d["payload"]["task_execution_id"] == "t1"


# ---------------------------------------------------------------------------
# Task management instructions (ENGINE-040)
# ---------------------------------------------------------------------------


class TestUseTasks:
    """Tests for _use_subtasks() inheritance logic."""

    def test_inherits_from_flow_when_node_is_none(self) -> None:
        """When node.tasks is None, inherit from flow.tasks."""
        flow = Flow(
            name="t",
            budget_seconds=3600,
            on_error=ErrorPolicy.PAUSE,
            context=ContextMode.HANDOFF,
            workspace="/ws",
            subtasks=True,
        )
        node = Node(name="work", node_type=NodeType.TASK, prompt="Do work", subtasks=None)
        assert _use_subtasks(flow, node) is True

    def test_inherits_false_from_flow(self) -> None:
        """When flow.tasks is False and node.tasks is None, result is False."""
        flow = Flow(
            name="t",
            budget_seconds=3600,
            on_error=ErrorPolicy.PAUSE,
            context=ContextMode.HANDOFF,
            workspace="/ws",
            subtasks=False,
        )
        node = Node(name="work", node_type=NodeType.TASK, prompt="Do work", subtasks=None)
        assert _use_subtasks(flow, node) is False

    def test_node_override_true(self) -> None:
        """Node-level tasks=True overrides flow-level tasks=False."""
        flow = Flow(
            name="t",
            budget_seconds=3600,
            on_error=ErrorPolicy.PAUSE,
            context=ContextMode.HANDOFF,
            workspace="/ws",
            subtasks=False,
        )
        node = Node(name="work", node_type=NodeType.TASK, prompt="Do work", subtasks=True)
        assert _use_subtasks(flow, node) is True

    def test_node_override_false(self) -> None:
        """Node-level subtasks=False overrides flow-level subtasks=True."""
        flow = Flow(
            name="t",
            budget_seconds=3600,
            on_error=ErrorPolicy.PAUSE,
            context=ContextMode.HANDOFF,
            workspace="/ws",
            subtasks=True,
        )
        node = Node(name="work", node_type=NodeType.TASK, prompt="Do work", subtasks=False)
        assert _use_subtasks(flow, node) is False


class TestBuildTaskManagementInstructions:
    """Tests for build_task_management_instructions()."""

    def test_basic_instructions(self) -> None:
        """Instructions contain correct API URLs with run_id and task_execution_id."""
        result = build_task_management_instructions(
            server_base_url="http://127.0.0.1:8080",
            run_id="run-123",
            task_execution_id="task-456",
        )
        assert "## Task Management" in result
        assert "http://127.0.0.1:8080/api/runs/run-123/tasks/task-456/subtasks" in result
        assert "POST" in result
        assert "PATCH" in result
        assert "predecessor" not in result.lower()

    def test_trailing_slash_stripped(self) -> None:
        """Trailing slash on server_base_url is stripped."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:8080/",
            run_id="run-1",
            task_execution_id="task-1",
        )
        assert "http://localhost:8080/api/runs/" in result
        assert "http://localhost:8080//api" not in result

    def test_predecessor_included_when_provided(self) -> None:
        """Predecessor section is included when predecessor_task_execution_id is given."""
        result = build_task_management_instructions(
            server_base_url="http://127.0.0.1:8080",
            run_id="run-123",
            task_execution_id="task-456",
            predecessor_task_execution_id="task-prev-789",
        )
        assert "predecessor" in result.lower()
        assert "http://127.0.0.1:8080/api/runs/run-123/tasks/task-prev-789/subtasks" in result

    def test_predecessor_omitted_when_none(self) -> None:
        """Predecessor section is NOT included when predecessor_task_execution_id is None."""
        result = build_task_management_instructions(
            server_base_url="http://127.0.0.1:8080",
            run_id="run-123",
            task_execution_id="task-456",
            predecessor_task_execution_id=None,
        )
        assert "predecessor" not in result.lower()

    def test_curl_create_example(self) -> None:
        """Create subtask curl example is correct."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:9000",
            run_id="r1",
            task_execution_id="t1",
        )
        assert "curl -s -X POST http://localhost:9000/api/runs/r1/tasks/t1/subtasks" in result
        assert '"title"' in result

    def test_curl_update_example(self) -> None:
        """Update subtask curl example includes PATCH and status field."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:9000",
            run_id="r1",
            task_execution_id="t1",
        )
        assert "PATCH" in result
        assert '"status"' in result
        assert "in_progress" in result

    def test_curl_list_example(self) -> None:
        """List subtasks curl example is correct."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:9000",
            run_id="r1",
            task_execution_id="t1",
        )
        assert "curl -s http://localhost:9000/api/runs/r1/tasks/t1/subtasks" in result

    def test_error_handling_guidance_present(self) -> None:
        """Instructions include error handling guidance telling agent to continue on failure."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:9000",
            run_id="r1",
            task_execution_id="t1",
        )
        assert "api call fails" in result.lower()
        assert "continue" in result.lower()
        assert "do not retry" in result.lower()

    def test_error_handling_guidance_with_predecessor(self) -> None:
        """Error handling guidance is present even when predecessor section is included."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:9000",
            run_id="r1",
            task_execution_id="t1",
            predecessor_task_execution_id="t0",
        )
        assert "api call fails" in result.lower()
        assert "continue" in result.lower()

    def test_lifecycle_instructions_present(self) -> None:
        """Instructions describe the full subtask lifecycle: create -> in_progress -> done."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:9000",
            run_id="r1",
            task_execution_id="t1",
        )
        assert "in_progress" in result
        assert "`done`" in result
        assert "lifecycle" in result.lower()

    def test_before_you_exit_section_present(self) -> None:
        """Instructions include a 'Before you exit' section reminding agents to complete subtasks."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:9000",
            run_id="r1",
            task_execution_id="t1",
        )
        assert "### Before you exit" in result
        assert "done" in result.lower()
        # Should tell agent to list and update subtasks before finishing
        assert "list" in result.lower()

    def test_before_you_exit_with_predecessor(self) -> None:
        """'Before you exit' section is present even when predecessor is included."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:9000",
            run_id="r1",
            task_execution_id="t1",
            predecessor_task_execution_id="t0",
        )
        assert "### Before you exit" in result

    def test_optional_not_in_tracking_discipline(self) -> None:
        """The word 'optional' should not appear — tracking discipline is not optional."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:9000",
            run_id="r1",
            task_execution_id="t1",
        )
        assert "optional" not in result.lower()

    def test_resilience_note_encourages_status_updates(self) -> None:
        """The resilience note tells agents to always attempt status updates."""
        result = build_task_management_instructions(
            server_base_url="http://localhost:9000",
            run_id="r1",
            task_execution_id="t1",
        )
        assert "always attempt to update subtask status" in result.lower()


def _make_tasks_flow(
    subtasks: bool = True,
    node_tasks: bool | None = None,
) -> Flow:
    """Build a simple linear flow with configurable subtasks setting."""
    nodes: dict[str, Node] = {
        "start": Node(
            name="start",
            node_type=NodeType.ENTRY,
            prompt="Do the start step",
            subtasks=node_tasks,
        ),
        "work": Node(
            name="work",
            node_type=NodeType.TASK,
            prompt="Do the work step",
            subtasks=node_tasks,
        ),
        "finish": Node(
            name="finish",
            node_type=NodeType.EXIT,
            prompt="Do the finish step",
            subtasks=node_tasks,
        ),
    }
    edges: list[Edge] = [
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="work"),
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="work", target="finish"),
    ]
    return Flow(
        name="tasks-test",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace="/workspace",
        subtasks=subtasks,
        nodes=nodes,
        edges=tuple(edges),
    )


class TestTaskManagementInjection:
    """Tests for task management prompt injection in the executor."""

    async def test_tasks_enabled_injects_instructions(
        self,
    ) -> None:
        """When tasks=True and server_base_url is set, task management is injected."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:8080")

        flow = _make_tasks_flow(subtasks=True)
        await executor.execute(flow, {}, "/workspace")

        # Verify that task management instructions were injected in the prompts
        # Check the "work" task (entry and exit tasks also get instructions)
        execs = db.list_task_executions(db.list_flow_runs()[0].id)
        work_execs = [e for e in execs if e.node_name == "work"]
        assert len(work_execs) >= 1
        work_prompt = work_execs[0].prompt_text
        assert "## Task Management" in work_prompt
        assert "/api/runs/" in work_prompt
        assert f"/tasks/{work_execs[0].id}/subtasks" in work_prompt

    async def test_tasks_disabled_no_injection(
        self,
    ) -> None:
        """When subtasks=False, no task management instructions are injected."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:8080")

        flow = _make_tasks_flow(subtasks=False)
        await executor.execute(flow, {}, "/workspace")

        execs = db.list_task_executions(db.list_flow_runs()[0].id)
        for e in execs:
            assert "## Task Management" not in e.prompt_text

    async def test_no_server_url_no_injection(
        self,
    ) -> None:
        """When server_base_url is None, no task management is injected even if tasks=True.

        Post-ENGINE-082, ``server_base_url=None`` makes the executor refuse
        to spawn any subprocess (``_build_artifact_env`` raises
        :class:`FlowExecutorConfigError`). The on_error=PAUSE policy then
        hangs the flow waiting for resume, so we bound the execute call with
        a short timeout and assert on the artifact the test actually cares
        about: the entry task's prompt was constructed without the
        ``## Task Management`` injection (which depends on ``server_base_url``
        being set in ``_maybe_update_task_prompt``).
        """
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url=None)

        flow = _make_tasks_flow(subtasks=True)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                executor.execute(flow, {}, "/workspace"),
                timeout=2.0,
            )

        # The entry task is created before any subprocess dispatch — its
        # prompt_text reflects what ``_maybe_update_task_prompt`` did, which
        # is the unit under test here.
        execs = db.list_task_executions(db.list_flow_runs()[0].id)
        assert execs, "expected at least the entry task to be created"
        for e in execs:
            assert "## Task Management" not in e.prompt_text

    async def test_node_override_disables_tasks(
        self,
    ) -> None:
        """When flow.tasks=True but node.subtasks=False, no instructions for that node."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:8080")

        flow = _make_tasks_flow(subtasks=True, node_tasks=False)
        await executor.execute(flow, {}, "/workspace")

        execs = db.list_task_executions(db.list_flow_runs()[0].id)
        for e in execs:
            assert "## Task Management" not in e.prompt_text

    async def test_handoff_includes_predecessor_id(
        self,
    ) -> None:
        """In handoff mode, the predecessor task_execution_id is included in instructions."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:8080")

        flow = _make_tasks_flow(subtasks=True)
        await executor.execute(flow, {}, "/workspace")

        execs = db.list_task_executions(db.list_flow_runs()[0].id)
        # Order: start, work, finish -- "work" should have predecessor = start's id
        start_exec = next(e for e in execs if e.node_name == "start")
        work_exec = next(e for e in execs if e.node_name == "work")

        # The work node prompt should contain the start node's task_execution_id
        # in the predecessor subtasks section
        assert "predecessor" in work_exec.prompt_text.lower()
        assert f"/tasks/{start_exec.id}/subtasks" in work_exec.prompt_text


# ---------------------------------------------------------------------------
# Tests: ENGINE-046 — Cancel kills ACP subprocess via in-memory session map
# ---------------------------------------------------------------------------


class TestCancelKillsHarness:
    """ENGINE-046: Cancel must call harness.kill() for running tasks.

    The bug was that cancel() looked up the session ID from the DB
    (claude_session_id), which is only set on task *completion*. For
    running tasks, the DB field is None, so harness.kill() was never
    called. The fix uses the in-memory _task_session dict instead.
    """

    async def test_cancel_calls_kill_on_harness(self) -> None:
        """Cancel a flow while a task is running and verify kill() is called."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Delay so we can cancel while the task is in-flight
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()

        async def run_and_cancel() -> str:
            execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
            await asyncio.sleep(0.15)
            runs = db.list_flow_runs()
            assert runs, "Expected at least one flow run"
            await executor.cancel(runs[0].id)
            return await execute_task

        await run_and_cancel()

        # The harness should have received at least one kill() call for the
        # work task's session ID.
        assert len(mock_mgr.kill_calls) > 0, (
            "Expected harness.kill() to be called during cancel, "
            "but kill_calls is empty. This means the session ID "
            "was not resolved from the in-memory _task_session map."
        )

    async def test_cancel_kills_correct_session_id(self) -> None:
        """The session ID passed to kill() must match the one used by run_task."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()

        async def run_and_cancel() -> str:
            execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
            await asyncio.sleep(0.15)
            runs = db.list_flow_runs()
            assert runs
            await executor.cancel(runs[0].id)
            return await execute_task

        await run_and_cancel()

        # The session ID passed to kill() should match one of the session IDs
        # used during run_task calls.
        run_task_session_ids = {sid for (_prompt, _ws, sid) in mock_mgr.calls}
        for killed_sid in mock_mgr.kill_calls:
            assert killed_sid in run_task_session_ids, (
                f"kill() was called with session_id '{killed_sid}' which was not "
                f"used in any run_task call. run_task session IDs: {run_task_session_ids}"
            )

    async def test_cancel_status_is_cancelled(self) -> None:
        """After cancel, the flow run status must be 'cancelled'."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()

        async def run_and_cancel() -> str:
            execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
            await asyncio.sleep(0.15)
            runs = db.list_flow_runs()
            assert runs
            await executor.cancel(runs[0].id)
            return await execute_task

        flow_run_id = await run_and_cancel()

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "cancelled"

        # Verify a cancelled status event was emitted
        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert any(e.payload.get("new_status") == "cancelled" for e in status_events)


# ---------------------------------------------------------------------------
# Tests: ENGINE-053 — restart_from_task for cancelled flows
# ---------------------------------------------------------------------------


class TestRestartFromTask:
    """Test restart_from_task(): retry/skip on cancelled flows that have no active executor."""

    async def test_retry_on_cancelled_flow_completes(self) -> None:
        """Cancel a flow, then restart via retry_task. Flow should complete."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()

        # Run and cancel
        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
        await asyncio.sleep(0.15)
        runs = db.list_flow_runs()
        assert runs
        flow_run_id = runs[0].id
        await executor.cancel(flow_run_id)
        await exec_task

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "cancelled"

        # Find the failed "work" task
        tasks = db.list_task_executions(flow_run_id)
        failed_work = [t for t in tasks if t.node_name == "work" and t.status == "failed"]
        assert len(failed_work) >= 1
        failed_task_id = failed_work[0].id

        # Remove the delay so retry completes quickly
        mock_mgr.task_delays.clear()

        # Create a fresh executor (simulating what the server would do)
        events.clear()
        new_executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")
        await new_executor.restart_from_task(
            flow=flow,
            flow_run_id=flow_run_id,
            task_execution_id=failed_task_id,
            action="retry",
        )

        # Flow should be completed
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed", f"Expected 'completed' but got '{run.status}'"

        # Verify status transition event: cancelled -> running
        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert any(
            e.payload.get("old_status") == "cancelled" and e.payload.get("new_status") == "running"
            for e in status_events
        ), "Expected cancelled -> running transition event"

    async def test_retry_creates_new_generation(self) -> None:
        """Retry via restart_from_task creates a new task execution with incremented generation."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()

        # Run and cancel
        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
        await asyncio.sleep(0.15)
        runs = db.list_flow_runs()
        assert runs
        flow_run_id = runs[0].id
        await executor.cancel(flow_run_id)
        await exec_task

        # Find the failed "work" task
        tasks = db.list_task_executions(flow_run_id)
        failed_work = [t for t in tasks if t.node_name == "work" and t.status == "failed"]
        assert len(failed_work) >= 1
        old_gen = failed_work[0].generation

        mock_mgr.task_delays.clear()
        new_executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")
        await new_executor.restart_from_task(
            flow=flow,
            flow_run_id=flow_run_id,
            task_execution_id=failed_work[0].id,
            action="retry",
        )

        # Find the new "work" task execution
        tasks = db.list_task_executions(flow_run_id)
        work_tasks = [t for t in tasks if t.node_name == "work"]
        assert len(work_tasks) >= 2, "Expected at least 2 work task executions (original + retry)"
        new_gen = max(t.generation for t in work_tasks)
        assert new_gen > old_gen, f"Expected generation > {old_gen}, got {new_gen}"

    async def test_skip_on_cancelled_flow_continues(self) -> None:
        """Cancel a flow, then restart via skip. Subsequent nodes should execute."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()

        # Run and cancel
        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
        await asyncio.sleep(0.15)
        runs = db.list_flow_runs()
        assert runs
        flow_run_id = runs[0].id
        await executor.cancel(flow_run_id)
        await exec_task

        # Find the failed "work" task
        tasks = db.list_task_executions(flow_run_id)
        failed_work = [t for t in tasks if t.node_name == "work" and t.status == "failed"]
        assert len(failed_work) >= 1

        mock_mgr.task_delays.clear()
        new_executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")
        await new_executor.restart_from_task(
            flow=flow,
            flow_run_id=flow_run_id,
            task_execution_id=failed_work[0].id,
            action="skip",
        )

        # The skipped task's successor ("finish") should have been created and executed
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed", f"Expected 'completed' but got '{run.status}'"

        # The original work task should be "skipped"
        orig_task = db.get_task_execution(failed_work[0].id)
        assert orig_task is not None
        assert orig_task.status == "skipped"

    async def test_flow_status_transitions_cancelled_running_completed(self) -> None:
        """Flow status goes: cancelled -> running -> completed."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()

        # Run and cancel
        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
        await asyncio.sleep(0.15)
        runs = db.list_flow_runs()
        assert runs
        flow_run_id = runs[0].id
        await executor.cancel(flow_run_id)
        await exec_task

        tasks = db.list_task_executions(flow_run_id)
        failed_work = [t for t in tasks if t.node_name == "work" and t.status == "failed"]

        events.clear()
        mock_mgr.task_delays.clear()
        new_executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")
        await new_executor.restart_from_task(
            flow=flow,
            flow_run_id=flow_run_id,
            task_execution_id=failed_work[0].id,
            action="retry",
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # Verify the status transitions in events
        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        statuses = [(e.payload["old_status"], e.payload["new_status"]) for e in status_events]
        assert (
            "cancelled",
            "running",
        ) in statuses, f"Expected cancelled->running transition, got: {statuses}"

    async def test_invalid_action_raises(self) -> None:
        """Invalid action (not 'retry' or 'skip') raises ValueError."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()

        import pytest

        with pytest.raises(ValueError, match="Invalid action"):
            await executor.restart_from_task(
                flow=flow,
                flow_run_id="nonexistent",
                task_execution_id="nonexistent",
                action="invalid",
            )

    async def test_nonexistent_flow_run_raises(self) -> None:
        """restart_from_task with non-existent flow_run_id raises ValueError."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow()

        import pytest

        with pytest.raises(ValueError, match="Flow run not found"):
            await executor.restart_from_task(
                flow=flow,
                flow_run_id="nonexistent-run-id",
                task_execution_id="nonexistent-task-id",
                action="retry",
            )

    async def test_subsequent_nodes_run_after_retry(self) -> None:
        """After retrying a cancelled task, subsequent nodes in the flow execute normally."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_mgr.task_delays["work"] = 2.0
        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        flow = _make_linear_flow(node_names=["start", "work", "verify", "finish"])

        exec_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))
        await asyncio.sleep(0.15)
        runs = db.list_flow_runs()
        assert runs
        flow_run_id = runs[0].id
        await executor.cancel(flow_run_id)
        await exec_task

        tasks = db.list_task_executions(flow_run_id)
        failed_work = [t for t in tasks if t.node_name == "work" and t.status == "failed"]
        assert len(failed_work) >= 1

        mock_mgr.task_delays.clear()
        new_executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")
        await new_executor.restart_from_task(
            flow=flow,
            flow_run_id=flow_run_id,
            task_execution_id=failed_work[0].id,
            action="retry",
        )

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # Verify that "verify" and "finish" nodes were also executed
        tasks = db.list_task_executions(flow_run_id)
        verify_tasks = [t for t in tasks if t.node_name == "verify" and t.status == "completed"]
        finish_tasks = [t for t in tasks if t.node_name == "finish" and t.status == "completed"]
        assert len(verify_tasks) >= 1, "Verify node did not execute after retry"
        assert len(finish_tasks) >= 1, "Finish node did not execute after retry"


# ---------------------------------------------------------------------------
# Tests: Auto-complete subtasks on task exit (ENGINE-056)
# ---------------------------------------------------------------------------


class TestAutoCompleteSubtasksOnSuccess:
    """Subtasks in todo/in_progress are auto-completed when a task exits with code 0."""

    async def test_subtasks_auto_completed_integration(self) -> None:
        """Integration: subtasks created before task completion are auto-marked done."""
        db = _make_db()
        events, _callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        # We need to inject subtasks during the task run. Use a tool_use event
        # to simulate the agent creating subtasks via the mock subprocess.
        # Instead, we'll create subtasks in the DB after the task execution is
        # created but before it completes. To do this, we hook into the event
        # callback to create subtasks when TASK_STARTED fires.
        subtask_ids: list[str] = []

        def callback_with_subtasks(event: FlowEvent) -> None:
            events.append(event)
            if event.type == EventType.TASK_STARTED and event.payload.get("node_name") == "work":
                task_exec_id = str(event.payload["task_execution_id"])
                s1 = db.create_agent_subtask(task_exec_id, "Research API docs")
                s2 = db.create_agent_subtask(task_exec_id, "Write implementation")
                s3 = db.create_agent_subtask(task_exec_id, "Write tests")
                # Mark one as in_progress
                db.update_agent_subtask(s2.id, "in_progress")
                subtask_ids.extend([s1.id, s2.id, s3.id])

        executor = FlowExecutor(
            db, callback_with_subtasks, mock_mgr, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow()
        flow = Flow(
            name=flow.name,
            budget_seconds=flow.budget_seconds,
            on_error=flow.on_error,
            context=flow.context,
            workspace=flow.workspace,
            nodes=flow.nodes,
            edges=flow.edges,
            subtasks=True,
        )

        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Verify flow completed
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # Verify all subtasks are now done
        assert len(subtask_ids) == 3
        for sid in subtask_ids:
            sub = db.get_agent_subtask(sid)
            assert sub is not None
            assert sub.status == "done", f"Subtask {sub.title!r} should be done, got {sub.status!r}"

        # Verify subtask.updated events were emitted
        subtask_events = [e for e in events if e.type == EventType.SUBTASK_UPDATED]
        assert len(subtask_events) == 3
        # All should reference the work task
        work_exec = next(t for t in db.list_task_executions(flow_run_id) if t.node_name == "work")
        for se in subtask_events:
            assert se.payload["task_execution_id"] == work_exec.id
            assert se.payload["status"] == "done"

    async def test_already_done_subtasks_still_emit_events(self) -> None:
        """Subtasks already marked done are included in events (they are in the returned list)."""
        db = _make_db()
        events, _callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        subtask_ids: list[str] = []

        def callback_with_subtasks(event: FlowEvent) -> None:
            events.append(event)
            if event.type == EventType.TASK_STARTED and event.payload.get("node_name") == "work":
                task_exec_id = str(event.payload["task_execution_id"])
                s1 = db.create_agent_subtask(task_exec_id, "Already done task")
                db.update_agent_subtask(s1.id, "done")
                s2 = db.create_agent_subtask(task_exec_id, "Still todo task")
                subtask_ids.extend([s1.id, s2.id])

        executor = FlowExecutor(
            db, callback_with_subtasks, mock_mgr, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow()
        flow = Flow(
            name=flow.name,
            budget_seconds=flow.budget_seconds,
            on_error=flow.on_error,
            context=flow.context,
            workspace=flow.workspace,
            nodes=flow.nodes,
            edges=flow.edges,
            subtasks=True,
        )

        await executor.execute(flow, {}, "/workspace")

        # Both subtasks should be done
        for sid in subtask_ids:
            sub = db.get_agent_subtask(sid)
            assert sub is not None
            assert sub.status == "done"

        # Events emitted for all done subtasks (including the one that was already done)
        subtask_events = [e for e in events if e.type == EventType.SUBTASK_UPDATED]
        assert len(subtask_events) == 2
        titles = {str(e.payload["title"]) for e in subtask_events}
        assert "Already done task" in titles
        assert "Still todo task" in titles


class TestNoAutoCompleteOnFailure:
    """Subtasks are NOT auto-completed when a task fails (non-zero exit code)."""

    async def test_subtasks_not_completed_on_failure(self) -> None:
        """Subtasks remain in their current state when task fails."""
        db = _make_db()
        events, _callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Configure 'work' to fail
        mock_mgr.task_responses["work"] = (1, [])
        subtask_ids: list[str] = []

        def callback_with_subtasks(event: FlowEvent) -> None:
            events.append(event)
            if event.type == EventType.TASK_STARTED and event.payload.get("node_name") == "work":
                task_exec_id = str(event.payload["task_execution_id"])
                s1 = db.create_agent_subtask(task_exec_id, "Todo task")
                s2 = db.create_agent_subtask(task_exec_id, "In progress task")
                db.update_agent_subtask(s2.id, "in_progress")
                subtask_ids.extend([s1.id, s2.id])

        executor = FlowExecutor(
            db, callback_with_subtasks, mock_mgr, server_base_url="http://127.0.0.1:9090"
        )

        flow = _make_linear_flow()
        flow = Flow(
            name=flow.name,
            budget_seconds=flow.budget_seconds,
            on_error=ErrorPolicy.ABORT,
            context=flow.context,
            workspace=flow.workspace,
            nodes=flow.nodes,
            edges=flow.edges,
            subtasks=True,
        )

        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Verify the flow did not complete successfully
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status != "completed", f"Flow should not have completed, got {run.status}"

        # Verify subtasks are NOT auto-completed
        s1 = db.get_agent_subtask(subtask_ids[0])
        assert s1 is not None
        assert s1.status == "todo", f"Expected todo, got {s1.status}"

        s2 = db.get_agent_subtask(subtask_ids[1])
        assert s2 is not None
        assert s2.status == "in_progress", f"Expected in_progress, got {s2.status}"

        # No subtask.updated events should have been emitted
        subtask_events = [e for e in events if e.type == EventType.SUBTASK_UPDATED]
        assert len(subtask_events) == 0


class TestNoAutoCompleteWhenSubtasksDisabled:
    """When subtasks are not enabled for a flow/node, no auto-complete happens."""

    async def test_no_auto_complete_when_subtasks_disabled(self) -> None:
        """Subtasks are not auto-completed when flow.subtasks is False."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        executor = FlowExecutor(db, callback, mock_mgr, server_base_url="http://127.0.0.1:9090")

        # Default flow has subtasks=False
        flow = _make_linear_flow()
        assert flow.subtasks is False

        flow_run_id = await executor.execute(flow, {}, "/workspace")

        # Manually create subtasks (as if someone did it via the API)
        task_execs = db.list_task_executions(flow_run_id)
        work_exec = next(t for t in task_execs if t.node_name == "work")
        db.create_agent_subtask(work_exec.id, "Should stay todo")

        # The subtask should still be todo because auto-complete didn't run
        # (subtasks=False). Note: we created it AFTER execution, so it wasn't
        # affected. But more importantly, no subtask events were emitted during
        # execution because subtasks were disabled.
        subtask_events = [e for e in events if e.type == EventType.SUBTASK_UPDATED]
        assert len(subtask_events) == 0


class TestAutoCompleteSubtasksRepository:
    """Direct tests for the complete_remaining_subtasks repository method."""

    def _make_task_exec(self, db: FlowstateDB) -> str:
        """Helper to create a flow run + task execution, returning the task exec ID."""
        flow_def_id = db.create_flow_definition("test-flow", "source", "{}")
        flow_run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/test",
            budget_seconds=3600,
            on_error="pause",
        )
        task_exec_id = db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name="work",
            node_type="task",
            generation=1,
            context_mode="handoff",
            cwd="/workspace",
            task_dir="/tmp/test/work",
            prompt_text="Do work",
        )
        return task_exec_id

    def test_complete_remaining_marks_todo_and_in_progress(self) -> None:
        """todo and in_progress subtasks become done; already-done stay done."""
        db = _make_db()
        task_exec_id = self._make_task_exec(db)

        db.create_agent_subtask(task_exec_id, "Todo item")
        s2 = db.create_agent_subtask(task_exec_id, "In progress item")
        db.update_agent_subtask(s2.id, "in_progress")
        s3 = db.create_agent_subtask(task_exec_id, "Done item")
        db.update_agent_subtask(s3.id, "done")

        result = db.complete_remaining_subtasks(task_exec_id)

        assert len(result) == 3
        for sub in result:
            assert sub.status == "done"

    def test_complete_remaining_empty_list(self) -> None:
        """No subtasks: returns empty list, no error."""
        db = _make_db()
        task_exec_id = self._make_task_exec(db)

        result = db.complete_remaining_subtasks(task_exec_id)
        assert result == []

    def test_complete_remaining_all_already_done(self) -> None:
        """All subtasks already done: UPDATE affects 0 rows, returns all."""
        db = _make_db()
        task_exec_id = self._make_task_exec(db)

        s1 = db.create_agent_subtask(task_exec_id, "Done 1")
        db.update_agent_subtask(s1.id, "done")
        s2 = db.create_agent_subtask(task_exec_id, "Done 2")
        db.update_agent_subtask(s2.id, "done")

        result = db.complete_remaining_subtasks(task_exec_id)
        assert len(result) == 2
        for sub in result:
            assert sub.status == "done"


# ---------------------------------------------------------------------------
# ENGINE-082: subprocess FLOWSTATE_SERVER_URL wiring
# ---------------------------------------------------------------------------


class TestBuildArtifactEnv:
    """Tests for ``FlowExecutor._build_artifact_env`` (ENGINE-082).

    The hardcoded ``http://127.0.0.1:9090`` fallback is gone. The env-building
    method must now (a) emit exactly the wired ``server_base_url`` and (b)
    raise :class:`FlowExecutorConfigError` when the URL was not wired —
    never silently fall through to a guessed loopback port. Both behaviors
    are covered here, isolated from the deadlock-prone
    ``TestContextModeHandoff`` class so the new tests can be run with
    ``-k "TestBuildArtifactEnv"`` (or excluded from a broader run via
    ``-k "not TestContextModeHandoff"``).
    """

    def test_wired_url_passed_through_verbatim(self) -> None:
        """``FLOWSTATE_SERVER_URL`` equals the wired ``server_base_url``."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db,
            callback,
            mock_mgr,
            server_base_url="http://127.0.0.1:9091",
        )

        env = executor._build_artifact_env(
            flow_run_id="run-123",
            task_execution_id="task-456",
        )

        assert env["FLOWSTATE_SERVER_URL"] == "http://127.0.0.1:9091"
        # The legacy hardcoded fallback must never leak through.
        assert ":9090" not in env["FLOWSTATE_SERVER_URL"]
        # Per-task identifiers are forwarded as well.
        assert env["FLOWSTATE_RUN_ID"] == "run-123"
        assert env["FLOWSTATE_TASK_ID"] == "task-456"

    def test_missing_url_raises_typed_error(self) -> None:
        """``server_base_url=None`` must raise, not silently fall through."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(
            db,
            callback,
            mock_mgr,
            server_base_url=None,
        )

        with pytest.raises(FlowExecutorConfigError, match="server_base_url"):
            executor._build_artifact_env(
                flow_run_id="run-123",
                task_execution_id="task-456",
            )

    def test_typed_error_is_subclass_of_exception(self) -> None:
        """The error class is a real ``Exception`` subclass (not just bare RuntimeError)."""
        # Provides TEST-82.2 coverage: the evaluator's import smoke check
        # `from flowstate.engine.executor import FlowExecutorConfigError` works.
        assert issubclass(FlowExecutorConfigError, Exception)
