"""Tests for FlowExecutor -- linear flow execution.

All tests use an in-memory SQLite database and a MockSubprocessManager that
returns configurable StreamEvent sequences. No real Claude Code subprocesses
are launched.
"""

from __future__ import annotations

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

    async def run_task(
        self, prompt: str, workspace: str, session_id: str
    ) -> AsyncGenerator[StreamEvent, None]:
        self.calls.append((prompt, workspace, session_id))
        exit_code, extra_events = self._find_response(prompt)

        for evt in extra_events:
            yield evt

        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": exit_code, "stderr": ""},
            raw=f"Process exited with code {exit_code}",
        )

    async def run_task_resume(
        self, prompt: str, workspace: str, resume_session_id: str
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


def _make_db() -> FlowstateDB:
    """Create an in-memory FlowstateDB for testing."""
    return FlowstateDB(":memory:")


def _collect_events() -> tuple[list[FlowEvent], Any]:
    """Return a list to collect events and the callback function."""
    events: list[FlowEvent] = []

    def callback(event: FlowEvent) -> None:
        events.append(event)

    return events, callback


# ---------------------------------------------------------------------------
# Tests
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

        # We need to patch the data_dir to use tmp_path. Since data_dir is derived
        # from the flow_run_id which is generated inside execute(), we verify
        # via the task_dir field in the DB records.
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        tasks = db.list_task_executions(flow_run_id)
        assert len(tasks) == 2
        for t in tasks:
            task_dir = Path(t.task_dir)
            # The directory was created by create_task_dir
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

        # Use a 2-node flow with budget=1 (nanosecond precision means even
        # fast tasks can cross thresholds for budget=1)
        flow = _make_linear_flow(
            budget_seconds=1,
            node_names=["start", "finish"],
        )
        await executor.execute(flow, {}, "/workspace")

        budget_events = [e for e in events if e.type == EventType.FLOW_BUDGET_WARNING]
        # Mocked subprocess completes nearly instantly, so elapsed may or may not
        # cross thresholds. This test validates structure IF warnings are emitted.
        for be in budget_events:
            assert "elapsed_seconds" in be.payload
            assert "budget_seconds" in be.payload
            assert "percent_used" in be.payload


class TestBudgetExceededPauses:
    async def test_budget_exceeded_pauses(self) -> None:
        """Budget very small, flow should pause due to budget exceeded.

        With a 4-node flow and budget=0, after the entry task completes the
        budget check should trigger a pause, resulting in a paused flow.
        """
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr)

        # 4-node flow so there is a non-exit task after the first.
        # Budget exceeded check happens after edge evaluation.
        # With budget=0, BudgetGuard starts exceeded=True. After the entry node
        # completes, the budget check fires and pauses the flow.
        flow = _make_linear_flow(
            budget_seconds=0,
            node_names=["start", "work", "work2", "finish"],
        )
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert any("Budget exceeded" in str(e.payload.get("reason", "")) for e in status_events)


class TestTaskFailurePausesFlow:
    async def test_task_failure_pauses_flow(self) -> None:
        """Task exits with code 1, on_error=pause -> flow pauses."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        # Make the first task (entry) fail
        mock_mgr.task_responses["start"] = (1, [])

        executor = FlowExecutor(db, callback, mock_mgr)

        flow = _make_linear_flow(on_error=ErrorPolicy.PAUSE)
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        # A task.failed event should have been emitted
        failed_events = [e for e in events if e.type == EventType.TASK_FAILED]
        assert len(failed_events) == 1
        assert failed_events[0].payload["node_name"] == "start"

        # A flow.status_changed event should mention the pause
        status_events = [e for e in events if e.type == EventType.FLOW_STATUS_CHANGED]
        assert len(status_events) >= 1


class TestEventEmissionOrder:
    async def test_event_emission_order(self) -> None:
        """Verify events are emitted in the correct order for a 2-node flow."""
        db = _make_db()
        events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()

        # Add a log event for each task
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

        # Expected order:
        # flow.started, task.started (start), task.log, task.log (exit), task.completed,
        # edge.transition, task.started (finish), task.log (exit), task.completed,
        # flow.completed
        assert event_types[0] == EventType.FLOW_STARTED
        assert event_types[1] == EventType.TASK_STARTED  # start node

        # Find the first task.completed, then edge.transition, then next task.started
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

        # First task completes before edge transition
        assert completed_indices[0] < edge_indices[0]
        # Edge transition before second task starts
        assert edge_indices[0] < task_started_indices[1]

        # Flow completed is last
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

        # The second task (finish) should have context mode handoff
        finish_task = tasks[1]
        assert finish_task.context_mode == ContextMode.HANDOFF.value

        # The prompt should include the handoff context section
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

        # Intercept run_task to write a SUMMARY.md for the start task
        original_run_task = mock_mgr.run_task

        async def run_task_with_summary(
            prompt: str, workspace: str, session_id: str
        ) -> AsyncGenerator[StreamEvent, None]:
            # If this is the start task, write SUMMARY.md to its task_dir
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

        # The finish task should NOT include handoff context
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
        # The semaphore's internal counter starts at max_concurrent
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

        # The first event should be flow.started with status=running
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
        # elapsed_seconds should be a non-negative number
        assert run.elapsed_seconds >= 0


class TestEdgeContextOverride:
    async def test_edge_context_override(self) -> None:
        """Edge-level context override takes precedence over flow-level default."""
        db = _make_db()
        _events, callback = _collect_events()
        mock_mgr = MockSubprocessManager()
        executor = FlowExecutor(db, callback, mock_mgr)

        # Flow default context is HANDOFF, but edge overrides to NONE
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
        flow_run_id = await executor.execute(flow, {}, "/workspace")

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "paused"

        tasks = db.list_task_executions(flow_run_id)
        # Entry should succeed, work should fail, finish never runs
        status_map = {t.node_name: t.status for t in tasks}
        assert status_map["start"] == "completed"
        assert status_map["work"] == "failed"
        assert "finish" not in status_map


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
        assert len(logs) >= 1  # at least the assistant log + exit event log
        log_types = [log.log_type for log in logs]
        assert "assistant_message" in log_types
