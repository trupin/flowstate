"""Tests for edge delay scheduling (ENGINE-010).

Covers:
- Delay-based waiting task creation
- Cron-schedule-based waiting task creation
- Background delay checker transitions
- Event emissions for waiting and wait_elapsed
- Budget exclusion of wait time
- Multiple simultaneous elapsed tasks
- Zero-delay edge case
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from flowstate.dsl.ast import (
    Edge,
    EdgeConfig,
    EdgeType,
)
from flowstate.engine.delay import (
    DelayChecker,
    compute_wait_until,
    enqueue_with_delay,
)
from flowstate.engine.events import EventType, FlowEvent
from flowstate.state.repository import FlowstateDB

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db() -> FlowstateDB:
    """Create an in-memory FlowstateDB for testing."""
    return FlowstateDB(":memory:")


def _collect_events() -> tuple[list[FlowEvent], object]:
    """Return a list to collect events and the callback function."""
    events: list[FlowEvent] = []

    def callback(event: FlowEvent) -> None:
        events.append(event)

    return events, callback


def _setup_flow_and_task(
    db: FlowstateDB,
    status: str = "pending",
    wait_until: str | None = None,
    node_name: str = "task_b",
) -> tuple[str, str]:
    """Create a flow definition, flow run, and task execution. Returns (flow_run_id, task_id)."""
    flow_def_id = db.create_flow_definition(
        name="test-flow", source_dsl="", ast_json='{"name": "test-flow"}'
    )
    flow_run_id = db.create_flow_run(
        flow_definition_id=flow_def_id,
        data_dir="/tmp/test-run",
        budget_seconds=3600,
        on_error="pause",
    )
    task_id = db.create_task_execution(
        flow_run_id=flow_run_id,
        node_name=node_name,
        node_type="task",
        generation=1,
        context_mode="handoff",
        cwd="/workspace",
        task_dir="/tmp/test-run/tasks/task_b-1",
        prompt_text="Do the task_b step",
    )

    if status != "pending":
        kwargs: dict[str, object] = {}
        if wait_until is not None:
            kwargs["wait_until"] = wait_until
        db.update_task_status(task_id, status, **kwargs)

    return flow_run_id, task_id


# ---------------------------------------------------------------------------
# Tests: compute_wait_until
# ---------------------------------------------------------------------------


class TestComputeWaitUntil:
    def test_delay_seconds_returns_future_timestamp(self) -> None:
        """Edge with delay_seconds=300 returns a timestamp ~300s in the future."""
        edge = Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="a",
            target="b",
            config=EdgeConfig(delay_seconds=300),
        )
        result = compute_wait_until(edge)
        assert result is not None
        wait_until_iso, reason = result
        assert reason == "delay"
        wait_until = datetime.fromisoformat(wait_until_iso)
        now = datetime.now(UTC)
        # Should be approximately 300s in the future (with 5s tolerance)
        diff = (wait_until - now).total_seconds()
        assert 295 <= diff <= 305

    def test_schedule_returns_next_cron_match(self) -> None:
        """Edge with schedule='0 2 * * *' returns the next 2 AM."""
        edge = Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="a",
            target="b",
            config=EdgeConfig(schedule="0 2 * * *"),
        )
        result = compute_wait_until(edge)
        assert result is not None
        wait_until_iso, reason = result
        assert reason == "schedule"
        wait_until = datetime.fromisoformat(wait_until_iso)
        assert wait_until.minute == 0
        assert wait_until.hour == 2
        assert wait_until > datetime.now(UTC)

    def test_no_delay_returns_none(self) -> None:
        """Edge with no delay/schedule returns None."""
        edge = Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="a",
            target="b",
            config=EdgeConfig(),
        )
        result = compute_wait_until(edge)
        assert result is None

    def test_zero_delay_returns_none(self) -> None:
        """delay_seconds=0 returns None (no delay)."""
        edge = Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="a",
            target="b",
            config=EdgeConfig(delay_seconds=0),
        )
        result = compute_wait_until(edge)
        assert result is None

    def test_invalid_cron_raises_error(self) -> None:
        """Invalid cron expression raises ValueError."""
        edge = Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="a",
            target="b",
            config=EdgeConfig(schedule="not a cron"),
        )
        with pytest.raises(ValueError, match="Invalid cron expression"):
            compute_wait_until(edge)


# ---------------------------------------------------------------------------
# Tests: enqueue_with_delay
# ---------------------------------------------------------------------------


class TestEnqueueWithDelay:
    def test_delay_creates_waiting_task(self) -> None:
        """Flow with edge delay_seconds=300. Task should be set to waiting."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_run_id, task_id = _setup_flow_and_task(db)

        edge = Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="task_a",
            target="task_b",
            config=EdgeConfig(delay_seconds=300),
        )

        was_delayed = enqueue_with_delay(
            task_execution_id=task_id,
            node_name="task_b",
            edge=edge,
            flow_run_id=flow_run_id,
            db=db,
            emit=callback,
        )

        assert was_delayed is True
        task = db.get_task_execution(task_id)
        assert task is not None
        assert task.status == "waiting"
        assert task.wait_until is not None

        # Verify wait_until is approximately 300s from now
        wait_until = datetime.fromisoformat(task.wait_until)
        now = datetime.now(UTC)
        diff = (wait_until - now).total_seconds()
        assert 295 <= diff <= 305

    def test_delay_emits_waiting_event(self) -> None:
        """Verify task.waiting event is emitted with correct payload."""
        db = _make_db()
        events, callback = _collect_events()

        flow_run_id, task_id = _setup_flow_and_task(db)

        edge = Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="task_a",
            target="task_b",
            config=EdgeConfig(delay_seconds=300),
        )

        enqueue_with_delay(
            task_execution_id=task_id,
            node_name="task_b",
            edge=edge,
            flow_run_id=flow_run_id,
            db=db,
            emit=callback,
        )

        waiting_events = [e for e in events if e.type == EventType.TASK_WAITING]
        assert len(waiting_events) == 1
        event = waiting_events[0]
        assert event.payload["task_execution_id"] == task_id
        assert event.payload["node_name"] == "task_b"
        assert event.payload["reason"] == "delay"
        assert "wait_until" in event.payload

    def test_schedule_creates_waiting_task(self) -> None:
        """Edge with schedule='0 2 * * *'. Task should be set to waiting."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_run_id, task_id = _setup_flow_and_task(db)

        edge = Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="task_a",
            target="task_b",
            config=EdgeConfig(schedule="0 2 * * *"),
        )

        was_delayed = enqueue_with_delay(
            task_execution_id=task_id,
            node_name="task_b",
            edge=edge,
            flow_run_id=flow_run_id,
            db=db,
            emit=callback,
        )

        assert was_delayed is True
        task = db.get_task_execution(task_id)
        assert task is not None
        assert task.status == "waiting"
        assert task.wait_until is not None

        # Verify next 2 AM
        wait_until = datetime.fromisoformat(task.wait_until)
        assert wait_until.hour == 2
        assert wait_until.minute == 0

    def test_schedule_emits_waiting_event(self) -> None:
        """Verify task.waiting event with reason='schedule'."""
        db = _make_db()
        events, callback = _collect_events()

        flow_run_id, task_id = _setup_flow_and_task(db)

        edge = Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="task_a",
            target="task_b",
            config=EdgeConfig(schedule="*/5 * * * *"),
        )

        enqueue_with_delay(
            task_execution_id=task_id,
            node_name="task_b",
            edge=edge,
            flow_run_id=flow_run_id,
            db=db,
            emit=callback,
        )

        waiting_events = [e for e in events if e.type == EventType.TASK_WAITING]
        assert len(waiting_events) == 1
        assert waiting_events[0].payload["reason"] == "schedule"

    def test_no_delay_returns_false(self) -> None:
        """Edge with no delay config should return False (not delayed)."""
        db = _make_db()
        events, callback = _collect_events()

        flow_run_id, task_id = _setup_flow_and_task(db)

        edge = Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="task_a",
            target="task_b",
            config=EdgeConfig(),
        )

        was_delayed = enqueue_with_delay(
            task_execution_id=task_id,
            node_name="task_b",
            edge=edge,
            flow_run_id=flow_run_id,
            db=db,
            emit=callback,
        )

        assert was_delayed is False
        # Task should remain pending
        task = db.get_task_execution(task_id)
        assert task is not None
        assert task.status == "pending"
        assert len(events) == 0

    def test_none_edge_returns_false(self) -> None:
        """None edge (entry node) should return False."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_run_id, task_id = _setup_flow_and_task(db)

        was_delayed = enqueue_with_delay(
            task_execution_id=task_id,
            node_name="task_b",
            edge=None,
            flow_run_id=flow_run_id,
            db=db,
            emit=callback,
        )

        assert was_delayed is False


# ---------------------------------------------------------------------------
# Tests: DelayChecker
# ---------------------------------------------------------------------------


class TestDelayChecker:
    async def test_transitions_elapsed_waiting_task_to_pending(self) -> None:
        """Waiting task with past wait_until should be transitioned to pending."""
        db = _make_db()
        _events, callback = _collect_events()

        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        flow_run_id, task_id = _setup_flow_and_task(db, status="waiting", wait_until=past_time)

        pending_queue: asyncio.Queue[str] = asyncio.Queue()
        checker = DelayChecker(
            db=db,
            flow_run_id=flow_run_id,
            emit=callback,
            pending_queue=pending_queue,
        )

        await checker.check_once()

        # Task should now be pending
        task = db.get_task_execution(task_id)
        assert task is not None
        assert task.status == "pending"

        # Task ID should be in the pending queue
        assert not pending_queue.empty()
        queued_id = await pending_queue.get()
        assert queued_id == task_id

    async def test_emits_wait_elapsed_event(self) -> None:
        """After transition, task.wait_elapsed event should be emitted."""
        db = _make_db()
        events, callback = _collect_events()

        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        flow_run_id, task_id = _setup_flow_and_task(db, status="waiting", wait_until=past_time)

        pending_queue: asyncio.Queue[str] = asyncio.Queue()
        checker = DelayChecker(
            db=db,
            flow_run_id=flow_run_id,
            emit=callback,
            pending_queue=pending_queue,
        )

        await checker.check_once()

        elapsed_events = [e for e in events if e.type == EventType.TASK_WAIT_ELAPSED]
        assert len(elapsed_events) == 1
        event = elapsed_events[0]
        assert event.payload["task_execution_id"] == task_id
        assert event.payload["node_name"] == "task_b"

    async def test_ignores_future_waits(self) -> None:
        """Waiting task with future wait_until should NOT be transitioned."""
        db = _make_db()
        events, callback = _collect_events()

        future_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        flow_run_id, task_id = _setup_flow_and_task(db, status="waiting", wait_until=future_time)

        pending_queue: asyncio.Queue[str] = asyncio.Queue()
        checker = DelayChecker(
            db=db,
            flow_run_id=flow_run_id,
            emit=callback,
            pending_queue=pending_queue,
        )

        await checker.check_once()

        # Task should still be waiting
        task = db.get_task_execution(task_id)
        assert task is not None
        assert task.status == "waiting"

        # No events emitted
        assert len(events) == 0
        assert pending_queue.empty()

    async def test_multiple_elapsed_tasks(self) -> None:
        """Two waiting tasks with past wait_until should both be transitioned."""
        db = _make_db()
        events, callback = _collect_events()

        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

        # Create flow setup
        flow_def_id = db.create_flow_definition(
            name="test-flow", source_dsl="", ast_json='{"name": "test-flow"}'
        )
        flow_run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/test-run",
            budget_seconds=3600,
            on_error="pause",
        )

        task_id_1 = db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name="task_a",
            node_type="task",
            generation=1,
            context_mode="handoff",
            cwd="/workspace",
            task_dir="/tmp/test-run/tasks/task_a-1",
            prompt_text="Do task_a",
        )
        db.update_task_status(task_id_1, "waiting", wait_until=past_time)

        task_id_2 = db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name="task_b",
            node_type="task",
            generation=1,
            context_mode="handoff",
            cwd="/workspace",
            task_dir="/tmp/test-run/tasks/task_b-1",
            prompt_text="Do task_b",
        )
        db.update_task_status(task_id_2, "waiting", wait_until=past_time)

        pending_queue: asyncio.Queue[str] = asyncio.Queue()
        checker = DelayChecker(
            db=db,
            flow_run_id=flow_run_id,
            emit=callback,
            pending_queue=pending_queue,
        )

        await checker.check_once()

        # Both tasks should be pending
        task1 = db.get_task_execution(task_id_1)
        task2 = db.get_task_execution(task_id_2)
        assert task1 is not None and task1.status == "pending"
        assert task2 is not None and task2.status == "pending"

        # Both task IDs in queue
        queued_ids = set()
        while not pending_queue.empty():
            queued_ids.add(await pending_queue.get())
        assert queued_ids == {task_id_1, task_id_2}

        # Two wait_elapsed events
        elapsed_events = [e for e in events if e.type == EventType.TASK_WAIT_ELAPSED]
        assert len(elapsed_events) == 2

    async def test_sets_wakeup_event(self) -> None:
        """When tasks become pending, the wakeup_event should be set."""
        db = _make_db()
        _events, callback = _collect_events()

        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        flow_run_id, _task_id = _setup_flow_and_task(db, status="waiting", wait_until=past_time)

        pending_queue: asyncio.Queue[str] = asyncio.Queue()
        wakeup = asyncio.Event()
        checker = DelayChecker(
            db=db,
            flow_run_id=flow_run_id,
            emit=callback,
            pending_queue=pending_queue,
            wakeup_event=wakeup,
        )

        assert not wakeup.is_set()
        await checker.check_once()
        assert wakeup.is_set()

    async def test_no_wakeup_when_no_tasks(self) -> None:
        """Wakeup event should NOT be set when no tasks elapsed."""
        db = _make_db()
        _events, callback = _collect_events()

        future_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        flow_run_id, _task_id = _setup_flow_and_task(db, status="waiting", wait_until=future_time)

        pending_queue: asyncio.Queue[str] = asyncio.Queue()
        wakeup = asyncio.Event()
        checker = DelayChecker(
            db=db,
            flow_run_id=flow_run_id,
            emit=callback,
            pending_queue=pending_queue,
            wakeup_event=wakeup,
        )

        await checker.check_once()
        assert not wakeup.is_set()

    async def test_start_and_stop(self) -> None:
        """Start the checker, verify it is running, then stop it."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_def_id = db.create_flow_definition(
            name="test-flow", source_dsl="", ast_json='{"name": "test-flow"}'
        )
        flow_run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/test-run",
            budget_seconds=3600,
            on_error="pause",
        )

        pending_queue: asyncio.Queue[str] = asyncio.Queue()
        checker = DelayChecker(
            db=db,
            flow_run_id=flow_run_id,
            emit=callback,
            pending_queue=pending_queue,
            check_interval=0.1,  # Fast interval for testing
        )

        await checker.start()
        assert checker.is_running

        # Let it run a couple cycles
        await asyncio.sleep(0.3)

        await checker.stop()
        assert not checker.is_running

    async def test_wait_time_not_in_budget(self) -> None:
        """Wait time should not count toward the budget.

        This test verifies the conceptual correctness: a task that was waiting
        has no elapsed_seconds attributed to it during the wait period. Only
        actual running time counts. The budget guard only receives
        elapsed_seconds from running tasks (set by the executor).
        """
        db = _make_db()
        _events, callback = _collect_events()

        past_time = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        flow_run_id, task_id = _setup_flow_and_task(db, status="waiting", wait_until=past_time)

        pending_queue: asyncio.Queue[str] = asyncio.Queue()
        checker = DelayChecker(
            db=db,
            flow_run_id=flow_run_id,
            emit=callback,
            pending_queue=pending_queue,
        )

        await checker.check_once()

        # After transitioning, the task is pending with no elapsed_seconds
        task = db.get_task_execution(task_id)
        assert task is not None
        assert task.status == "pending"
        assert task.elapsed_seconds is None  # No running time attributed

    async def test_only_processes_matching_flow_run(self) -> None:
        """Checker should only process tasks for its configured flow_run_id."""
        db = _make_db()
        _events, callback = _collect_events()

        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

        # Create two flow runs
        flow_def_id = db.create_flow_definition(
            name="test-flow", source_dsl="", ast_json='{"name": "test-flow"}'
        )
        flow_run_id_1 = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/test-run-1",
            budget_seconds=3600,
            on_error="pause",
        )
        flow_run_id_2 = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir="/tmp/test-run-2",
            budget_seconds=3600,
            on_error="pause",
        )

        # Task in flow run 1 (should be processed)
        task_id_1 = db.create_task_execution(
            flow_run_id=flow_run_id_1,
            node_name="task_a",
            node_type="task",
            generation=1,
            context_mode="handoff",
            cwd="/workspace",
            task_dir="/tmp/test-run-1/tasks/task_a-1",
            prompt_text="Do task_a",
        )
        db.update_task_status(task_id_1, "waiting", wait_until=past_time)

        # Task in flow run 2 (should NOT be processed by checker for flow_run_1)
        task_id_2 = db.create_task_execution(
            flow_run_id=flow_run_id_2,
            node_name="task_b",
            node_type="task",
            generation=1,
            context_mode="handoff",
            cwd="/workspace",
            task_dir="/tmp/test-run-2/tasks/task_b-1",
            prompt_text="Do task_b",
        )
        db.update_task_status(task_id_2, "waiting", wait_until=past_time)

        pending_queue: asyncio.Queue[str] = asyncio.Queue()
        checker = DelayChecker(
            db=db,
            flow_run_id=flow_run_id_1,
            emit=callback,
            pending_queue=pending_queue,
        )

        await checker.check_once()

        # Only task from flow_run_1 should be transitioned
        task1 = db.get_task_execution(task_id_1)
        assert task1 is not None and task1.status == "pending"

        task2 = db.get_task_execution(task_id_2)
        assert task2 is not None and task2.status == "waiting"

        # Only one task in the queue
        assert pending_queue.qsize() == 1
