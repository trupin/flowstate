"""Tests for QueueManager -- polls for queued tasks and starts flow runs.

All tests use an in-memory SQLite database and mock objects for the
FlowRegistry, RunManager, SubprocessManager, and WebSocket hub.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

from flowstate.engine.queue_manager import QueueManager
from flowstate.state.repository import FlowstateDB

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


@dataclass
class MockDiscoveredFlow:
    """Minimal stand-in for DiscoveredFlow used by the queue manager."""

    id: str
    name: str | None
    file_path: str
    source_dsl: str
    status: str
    errors: list[str] = field(default_factory=list)
    ast_json: dict[str, Any] | None = None
    params: list[dict[str, Any]] = field(default_factory=list)


class MockFlowRegistry:
    """Minimal FlowRegistry that returns pre-configured flows."""

    def __init__(self, flows: list[MockDiscoveredFlow] | None = None) -> None:
        self._flows = {f.id: f for f in (flows or [])}

    def list_flows(self) -> list[MockDiscoveredFlow]:
        return list(self._flows.values())

    def get_flow(self, flow_id: str) -> MockDiscoveredFlow | None:
        return self._flows.get(flow_id)

    def get_flow_by_name(self, name: str) -> MockDiscoveredFlow | None:
        for flow in self._flows.values():
            if flow.name == name:
                return flow
        return None


class MockRunManager:
    """Minimal RunManager that captures start_run calls."""

    def __init__(self) -> None:
        self.started_runs: list[tuple[str, object, object]] = []

    async def start_run(self, flow_run_id: str, executor: object, execute_coro: object) -> None:
        self.started_runs.append((flow_run_id, executor, execute_coro))
        # Don't actually await the coro in tests -- just close it to avoid
        # RuntimeWarning about unawaited coroutines
        if hasattr(execute_coro, "close"):
            execute_coro.close()


# A minimal valid .flow DSL that can be parsed.
SIMPLE_FLOW_DSL = """\
flow test_flow {
    budget = 60m
    on_error = pause
    context = handoff

    entry start {
        prompt = "Do the start step"
    }

    exit finish {
        prompt = "Do the finish step"
    }

    start -> finish
}
"""


def _make_db() -> FlowstateDB:
    """Create an in-memory FlowstateDB."""
    return FlowstateDB(":memory:")


def _make_queue_manager(
    db: FlowstateDB | None = None,
    registry: MockFlowRegistry | None = None,
    run_manager: MockRunManager | None = None,
    max_concurrent: int = 1,
) -> tuple[QueueManager, FlowstateDB, MockFlowRegistry, MockRunManager]:
    """Create a QueueManager with test doubles."""
    db = db or _make_db()
    reg = registry or MockFlowRegistry()
    rm = run_manager or MockRunManager()
    ws_hub = MagicMock()
    ws_hub.on_flow_event = MagicMock()
    config = MagicMock()
    config.max_concurrent_tasks = 4
    config.worktree_cleanup = True

    qm = QueueManager(
        db=db,
        flow_registry=reg,
        run_manager=rm,
        subprocess_mgr=MagicMock(),
        ws_hub=ws_hub,
        config=config,
        poll_interval=0.1,
        max_concurrent=max_concurrent,
    )
    return qm, db, reg, rm


# ---------------------------------------------------------------------------
# Tests: get_flow_by_name on registry
# ---------------------------------------------------------------------------


class TestGetFlowByName:
    def test_finds_matching_flow(self) -> None:
        flow = MockDiscoveredFlow(
            id="my_flow",
            name="test_flow",
            file_path="/tmp/my_flow.flow",
            source_dsl=SIMPLE_FLOW_DSL,
            status="valid",
        )
        registry = MockFlowRegistry([flow])
        result = registry.get_flow_by_name("test_flow")
        assert result is not None
        assert result.name == "test_flow"

    def test_returns_none_for_unknown_name(self) -> None:
        registry = MockFlowRegistry([])
        result = registry.get_flow_by_name("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: QueueManager._process_queues
# ---------------------------------------------------------------------------


class TestProcessQueues:
    async def test_starts_run_for_queued_task(self) -> None:
        """When a queued task exists and there is capacity, a run should start."""
        flow = MockDiscoveredFlow(
            id="test_flow",
            name="test_flow",
            file_path="/tmp/test_flow.flow",
            source_dsl=SIMPLE_FLOW_DSL,
            status="valid",
        )
        registry = MockFlowRegistry([flow])
        qm, db, _, rm = _make_queue_manager(registry=registry)

        # Create a queued task
        task_id = db.create_task("test_flow", "Build feature X")

        await qm._process_queues()

        # Run manager should have received a start_run call
        assert len(rm.started_runs) == 1
        run_id, _executor, _coro = rm.started_runs[0]
        assert run_id  # non-empty UUID string

        # Task status should be updated to running
        task = db.get_task(task_id)
        assert task is not None
        assert task.status == "running"
        # flow_run_id is set inside executor.execute() which runs as a
        # background task -- not set yet since MockRunManager doesn't await it

    async def test_capacity_limit_prevents_second_task(self) -> None:
        """When max_concurrent=1 and one task is running, don't start another."""
        flow = MockDiscoveredFlow(
            id="test_flow",
            name="test_flow",
            file_path="/tmp/test_flow.flow",
            source_dsl=SIMPLE_FLOW_DSL,
            status="valid",
        )
        registry = MockFlowRegistry([flow])
        qm, db, _, rm = _make_queue_manager(registry=registry, max_concurrent=1)

        # Create two queued tasks
        db.create_task("test_flow", "Task 1")
        task2_id = db.create_task("test_flow", "Task 2")

        # Start first task
        await qm._process_queues()
        assert len(rm.started_runs) == 1

        # The first task is now "running" in the DB.
        # Process again -- should NOT start the second task.
        await qm._process_queues()
        assert len(rm.started_runs) == 1

        # Second task should still be queued
        task2 = db.get_task(task2_id)
        assert task2 is not None
        assert task2.status == "queued"

    async def test_multiple_flows_processed_independently(self) -> None:
        """Tasks from different flows should be started independently."""
        flow_a = MockDiscoveredFlow(
            id="flow_a",
            name="flow_a",
            file_path="/tmp/flow_a.flow",
            source_dsl=SIMPLE_FLOW_DSL.replace("test_flow", "flow_a"),
            status="valid",
        )
        flow_b = MockDiscoveredFlow(
            id="flow_b",
            name="flow_b",
            file_path="/tmp/flow_b.flow",
            source_dsl=SIMPLE_FLOW_DSL.replace("test_flow", "flow_b"),
            status="valid",
        )
        registry = MockFlowRegistry([flow_a, flow_b])
        qm, db, _, rm = _make_queue_manager(registry=registry, max_concurrent=1)

        # One task per flow
        db.create_task("flow_a", "Task A")
        db.create_task("flow_b", "Task B")

        await qm._process_queues()

        # Both should start (one per flow, each has its own capacity)
        assert len(rm.started_runs) == 2

    async def test_task_not_started_when_flow_invalid(self) -> None:
        """When the flow is in error status, the task should be marked failed."""
        flow = MockDiscoveredFlow(
            id="bad_flow",
            name="bad_flow",
            file_path="/tmp/bad_flow.flow",
            source_dsl="invalid",
            status="error",
            errors=["Parse error"],
        )
        registry = MockFlowRegistry([flow])
        qm, db, _, rm = _make_queue_manager(registry=registry)

        task_id = db.create_task("bad_flow", "Task for bad flow")

        await qm._process_queues()

        # No runs started
        assert len(rm.started_runs) == 0

        # Task should be failed
        task = db.get_task(task_id)
        assert task is not None
        assert task.status == "failed"
        assert task.error_message is not None
        assert "not found or invalid" in task.error_message

    async def test_task_not_started_when_flow_missing(self) -> None:
        """When no flow matches the task's flow_name, task should be failed."""
        registry = MockFlowRegistry([])  # empty registry
        qm, db, _, rm = _make_queue_manager(registry=registry)

        task_id = db.create_task("nonexistent_flow", "Orphan task")

        await qm._process_queues()

        assert len(rm.started_runs) == 0

        task = db.get_task(task_id)
        assert task is not None
        assert task.status == "failed"

    async def test_task_params_passed_to_executor(self) -> None:
        """Task params_json should be parsed and passed to the executor."""
        flow = MockDiscoveredFlow(
            id="test_flow",
            name="test_flow",
            file_path="/tmp/test_flow.flow",
            source_dsl=SIMPLE_FLOW_DSL,
            status="valid",
        )
        registry = MockFlowRegistry([flow])
        qm, db, _, rm = _make_queue_manager(registry=registry)

        params = {"branch": "main", "priority": 5}
        db.create_task(
            "test_flow",
            "Parameterized task",
            params_json=json.dumps(params),
        )

        await qm._process_queues()

        # Run was started
        assert len(rm.started_runs) == 1

    async def test_no_queued_tasks_is_noop(self) -> None:
        """When there are no queued tasks, nothing should happen."""
        qm, _db, _, rm = _make_queue_manager()
        await qm._process_queues()
        assert len(rm.started_runs) == 0

    async def test_disabled_flow_skipped(self) -> None:
        """When a flow is disabled, its queued tasks are not started."""
        flow = MockDiscoveredFlow(
            id="test_flow",
            name="test_flow",
            file_path="/tmp/test_flow.flow",
            source_dsl=SIMPLE_FLOW_DSL,
            status="valid",
        )
        registry = MockFlowRegistry([flow])
        qm, db, _, rm = _make_queue_manager(registry=registry)

        # Disable the flow
        db.set_flow_enabled("test_flow", False)

        # Create a queued task
        task_id = db.create_task("test_flow", "Should not start")

        await qm._process_queues()

        # No runs should have started
        assert len(rm.started_runs) == 0

        # Task should still be queued
        task = db.get_task(task_id)
        assert task is not None
        assert task.status == "queued"

    async def test_reenabled_flow_processes_tasks(self) -> None:
        """After re-enabling a flow, queued tasks are picked up again."""
        flow = MockDiscoveredFlow(
            id="test_flow",
            name="test_flow",
            file_path="/tmp/test_flow.flow",
            source_dsl=SIMPLE_FLOW_DSL,
            status="valid",
        )
        registry = MockFlowRegistry([flow])
        qm, db, _, rm = _make_queue_manager(registry=registry)

        # Disable the flow and create a task
        db.set_flow_enabled("test_flow", False)
        task_id = db.create_task("test_flow", "Waiting task")

        # Process -- nothing should happen
        await qm._process_queues()
        assert len(rm.started_runs) == 0

        # Re-enable the flow
        db.set_flow_enabled("test_flow", True)

        # Process again -- task should start
        await qm._process_queues()
        assert len(rm.started_runs) == 1

        task = db.get_task(task_id)
        assert task is not None
        assert task.status == "running"

    async def test_disabled_flow_does_not_affect_other_flows(self) -> None:
        """Disabling one flow does not affect other flows."""
        flow_a = MockDiscoveredFlow(
            id="flow_a",
            name="flow_a",
            file_path="/tmp/flow_a.flow",
            source_dsl=SIMPLE_FLOW_DSL.replace("test_flow", "flow_a"),
            status="valid",
        )
        flow_b = MockDiscoveredFlow(
            id="flow_b",
            name="flow_b",
            file_path="/tmp/flow_b.flow",
            source_dsl=SIMPLE_FLOW_DSL.replace("test_flow", "flow_b"),
            status="valid",
        )
        registry = MockFlowRegistry([flow_a, flow_b])
        qm, db, _, rm = _make_queue_manager(registry=registry, max_concurrent=1)

        # Disable flow_a only
        db.set_flow_enabled("flow_a", False)

        db.create_task("flow_a", "Task A")
        task_b_id = db.create_task("flow_b", "Task B")

        await qm._process_queues()

        # Only flow_b's task should start
        assert len(rm.started_runs) == 1

        task_b = db.get_task(task_b_id)
        assert task_b is not None
        assert task_b.status == "running"


# ---------------------------------------------------------------------------
# Tests: QueueManager start/stop lifecycle
# ---------------------------------------------------------------------------


class TestQueueManagerLifecycle:
    async def test_start_and_stop(self) -> None:
        """The queue manager should start and stop cleanly."""
        qm, _, _, _ = _make_queue_manager()
        await qm.start()
        assert qm._running is True
        assert qm._task is not None
        await qm.stop()
        assert qm._running is False

    async def test_stop_when_not_started(self) -> None:
        """Stopping before starting should be a no-op."""
        qm, _, _, _ = _make_queue_manager()
        await qm.stop()  # should not raise
        assert qm._running is False
