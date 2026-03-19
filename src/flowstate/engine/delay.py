"""Edge delay handling -- manages delayed task enqueueing and background wait checking.

When an edge has a `delay_seconds` or `schedule` (cron expression) configured, the
target task enters `waiting` status with a `wait_until` timestamp instead of immediately
becoming `pending`. A background asyncio task periodically checks for waiting tasks
whose `wait_until` has elapsed and transitions them to `pending`.

Wait time does NOT count toward the flow's budget -- only active task execution time
counts (BudgetGuard only receives elapsed_seconds from running tasks).

Usage from the executor:
    1. Call `enqueue_with_delay()` after creating a task execution to decide whether
       it should be immediately pending or placed in waiting status.
    2. Start `DelayChecker` as a background task alongside the main execution loop.
    3. When the delay checker finds elapsed waits, it puts their task IDs into the
       `pending_queue` for the executor to pick up.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from croniter import croniter

from flowstate.engine.events import EventType, FlowEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from flowstate.dsl.ast import Edge
    from flowstate.state.repository import FlowstateDB

logger = logging.getLogger(__name__)

# Default interval for the background delay checker (seconds).
DEFAULT_CHECK_INTERVAL: float = 30.0


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def compute_wait_until(
    edge: Edge,
) -> tuple[str, str] | None:
    """Compute the wait_until timestamp and reason for an edge's delay config.

    Returns:
        A (wait_until_iso, reason) tuple, or None if the edge has no delay.

    Raises:
        ValueError: If a cron expression is invalid.
    """
    if edge.config.delay_seconds is not None and edge.config.delay_seconds > 0:
        wait_until = datetime.now(UTC) + timedelta(seconds=edge.config.delay_seconds)
        return wait_until.isoformat(), "delay"

    if edge.config.schedule is not None:
        now = datetime.now(UTC)
        try:
            cron = croniter(edge.config.schedule, now)
            next_time: datetime = cron.get_next(datetime)
            return next_time.isoformat(), "schedule"
        except (ValueError, KeyError) as e:
            raise ValueError(f"Invalid cron expression '{edge.config.schedule}': {e}") from e

    return None


def enqueue_with_delay(
    task_execution_id: str,
    node_name: str,
    edge: Edge | None,
    flow_run_id: str,
    db: FlowstateDB,
    emit: Callable[[FlowEvent], None],
) -> bool:
    """Set a task to waiting if the edge has a delay/schedule, else leave it pending.

    Args:
        task_execution_id: The task execution to potentially delay.
        node_name: Name of the target node (for event payloads).
        edge: The edge that leads to this task (may be None for entry nodes).
        flow_run_id: The flow run ID (for event payloads).
        db: Database for updating task status.
        emit: Event callback for emitting waiting events.

    Returns:
        True if the task was placed in waiting status (caller should NOT add to
        pending set). False if no delay applies (caller should add to pending set).
    """
    if edge is None:
        return False

    result = compute_wait_until(edge)
    if result is None:
        return False

    wait_until_iso, reason = result

    # Transition task from pending to waiting with wait_until timestamp
    db.update_task_status(task_execution_id, "waiting", wait_until=wait_until_iso)

    emit(
        FlowEvent(
            type=EventType.TASK_WAITING,
            flow_run_id=flow_run_id,
            timestamp=_now_iso(),
            payload={
                "task_execution_id": task_execution_id,
                "node_name": node_name,
                "wait_until": wait_until_iso,
                "reason": reason,
            },
        )
    )

    return True


class DelayChecker:
    """Background task that checks for waiting tasks with elapsed wait_until times.

    Runs every `check_interval` seconds. For each waiting task whose `wait_until`
    has passed, transitions it to `pending` status and puts its ID into the
    `pending_queue` so the executor main loop can pick it up.

    The checker also sets the `wakeup_event` when new tasks become pending, allowing
    the executor's main loop to wake up and process them.
    """

    def __init__(
        self,
        db: FlowstateDB,
        flow_run_id: str,
        emit: Callable[[FlowEvent], None],
        pending_queue: asyncio.Queue[str],
        wakeup_event: asyncio.Event | None = None,
        check_interval: float = DEFAULT_CHECK_INTERVAL,
    ) -> None:
        self._db = db
        self._flow_run_id = flow_run_id
        self._emit = emit
        self._pending_queue = pending_queue
        self._wakeup_event = wakeup_event
        self._check_interval = check_interval
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background delay checker loop."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background delay checker."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_loop(self) -> None:
        """Main checker loop. Runs until stopped or cancelled."""
        while self._running:
            try:
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            try:
                await self._check_elapsed()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in delay checker loop")

    async def _check_elapsed(self) -> None:
        """Check for waiting tasks whose wait_until has elapsed."""
        now = datetime.now(UTC).isoformat()
        elapsed_tasks = self._db.get_waiting_tasks(self._flow_run_id, now)

        transitioned_any = False
        for task in elapsed_tasks:
            # Transition from waiting to pending
            self._db.update_task_status(task.id, "pending")
            await self._pending_queue.put(task.id)
            transitioned_any = True

            self._emit(
                FlowEvent(
                    type=EventType.TASK_WAIT_ELAPSED,
                    flow_run_id=self._flow_run_id,
                    timestamp=_now_iso(),
                    payload={
                        "task_execution_id": task.id,
                        "node_name": task.node_name,
                    },
                )
            )

        # Wake up the main loop if new tasks became pending
        if transitioned_any and self._wakeup_event is not None:
            self._wakeup_event.set()

    async def check_once(self) -> None:
        """Run a single check cycle. Useful for testing."""
        await self._check_elapsed()

    @property
    def is_running(self) -> bool:
        """True if the background loop is currently running."""
        return self._running and self._task is not None and not self._task.done()
