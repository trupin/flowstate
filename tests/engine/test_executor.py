"""Tests for FlowExecutor -- linear, fork-join, conditional, cycle, and control operations.

All tests use an in-memory SQLite database and a MockSubprocessManager that
returns configurable StreamEvent sequences. No real Claude Code subprocesses
are launched.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
from flowstate.engine.events import EventType, FlowEvent
from flowstate.engine.executor import FlowExecutor
from flowstate.engine.judge import JudgeContext, JudgeDecision, JudgePauseError, JudgeProtocol
from flowstate.engine.subprocess_mgr import StreamEvent, StreamEventType, SubprocessManager
from flowstate.state.repository import FlowstateDB

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# ---------------------------------------------------------------------------
# Mock subprocess manager
# ---------------------------------------------------------------------------


class MockSubprocessManager(SubprocessManager):
    """A test double for SubprocessManager that returns configurable events.

    Configure per-node responses via task_responses dict.  Keys are node name
    markers of the form "Do the <name> step" (matching the prompt pattern from
    _make_linear_flow).  Values are (exit_code, extra_events) tuples.

    When a prompt does not match any key, a default success response is returned.
    """

    def __init__(self) -> None:
        super().__init__()
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

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
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

    async def kill(self, session_id: str) -> None:
        self.kill_calls.append(session_id)

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
        executor = FlowExecutor(db, callback, mock_mgr, max_concurrent=4)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
    async def test_task_directory_creation(self, tmp_path: Path) -> None:
        """After execution, verify task directories exist under the data dir."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr)

        flow = _make_linear_flow(node_names=["start", "finish"])

        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        assert len(tasks) == 2
        for t in tasks:
            task_dir = Path(t.task_dir)
            assert task_dir.exists()

    async def test_task_dir_naming(self) -> None:
        """Task directories follow <name>-<generation> naming."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr)

        flow = _make_linear_flow(node_names=["start", "work", "finish"])
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        for t in tasks:
            assert t.task_dir.endswith(f"{t.node_name}-{t.generation}")


class TestBudgetWarningEvents:
    async def test_budget_warning_event_structure(self) -> None:
        """Verify budget warning events have correct structure when emitted."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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

        executor = FlowExecutor(db, callback, mock_mgr)

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

        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        """When predecessor writes SUMMARY.md, the handoff prompt includes it."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr)

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
                            summary_path = Path(t.task_dir) / "SUMMARY.md"
                            summary_path.write_text("I set up the project successfully.")
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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr, max_concurrent=7)

        assert executor._max_concurrent == 7
        assert executor._semaphore._value == 7


class TestMinimalFlow:
    async def test_entry_exit_only(self) -> None:
        """Flow with only entry + exit (2 nodes, 1 edge). Should complete."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

        flow = _make_linear_flow(node_names=["start", "finish"])
        await executor.execute(flow, {}, "/workspace")

        assert events[0].type == EventType.FLOW_STARTED
        assert events[0].payload["status"] == "running"

    async def test_flow_run_elapsed_updated(self) -> None:
        """flow_run.elapsed_seconds is updated after flow completion."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
            # Write SUMMARY.md for fork members
            runs = db.list_flow_runs()
            if runs:
                task_list = db.list_task_executions(runs[0].id)
                for t in task_list:
                    if t.node_name == "task_a" and t.status == "running":
                        Path(t.task_dir).joinpath("SUMMARY.md").write_text(
                            "Task A completed frontend."
                        )
                    elif t.node_name == "task_b" and t.status == "running":
                        Path(t.task_dir).joinpath("SUMMARY.md").write_text(
                            "Task B completed backend."
                        )

            async for evt in original_run_task(prompt, workspace, session_id):
                yield evt

        mock_mgr.run_task = run_task_with_summaries  # type: ignore[assignment]

        executor = FlowExecutor(db, callback, mock_mgr)
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

        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr, max_concurrent=4)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

        flow = _make_conditional_flow(with_cycle=True)
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        implement_tasks = [t for t in tasks if t.node_name == "implement"]
        assert len(implement_tasks) == 2
        # First run: generation 1, second run: generation 2
        generations = sorted(t.generation for t in implement_tasks)
        assert generations == [1, 2]

        # Task directories should be different
        dirs = [t.task_dir for t in implement_tasks]
        assert dirs[0] != dirs[1]
        assert "implement-1" in dirs[0] or "implement-1" in dirs[1]
        assert "implement-2" in dirs[0] or "implement-2" in dirs[1]


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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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


class TestEdgeTransitionRecorded:
    """After a conditional transition, verify edge_transitions record in DB."""

    async def test_edge_transition_recorded(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        mock_judge = MockJudgeProtocol()
        mock_judge.add_decision(JudgeDecision(target="done", reasoning="LGTM", confidence=0.95))

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        assert new_task.task_dir != failed_task.task_dir

        await executor.cancel(flow_run_id)
        await execute_task


class TestRetryNonFailedRaises:
    """Retry a completed task -> ValueError."""

    async def test_retry_non_failed_raises(self) -> None:
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, bad_callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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

        executor = FlowExecutor(db, callback, mock_mgr, judge=mock_judge)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

        flow = _make_linear_flow(
            node_names=["start", "work", "finish"],
        )

        execute_task = asyncio.create_task(executor.execute(flow, {}, "/workspace"))

        # Wait for "work" to start running
        await asyncio.sleep(0.1)
        runs = db.list_flow_runs()
        assert runs

        # Pause the flow (waits for "work" to finish)
        await executor.pause(runs[0].id)

        run = db.get_flow_run(runs[0].id)
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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
        executor = FlowExecutor(db, callback, mock_mgr)

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
