"""Recurring flow scheduler -- background task for cron-based flow execution.

Checks the `flow_schedules` table every 30 seconds for schedules whose
`next_trigger_at` has elapsed. When a trigger fires, the scheduler evaluates the
`on_overlap` policy to decide whether to start a new run, skip it, or queue it.

Uses the `croniter` library for cron expression parsing and next-trigger computation.

Usage:
    scheduler = FlowScheduler(db, executor, event_callback)
    await scheduler.start()
    # ... later ...
    await scheduler.stop()
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from croniter import croniter

from flowstate.engine.events import EventType, FlowEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from flowstate.state.models import FlowScheduleRow
    from flowstate.state.repository import FlowstateDB

logger = logging.getLogger(__name__)

# Default interval between schedule checks (seconds).
DEFAULT_CHECK_INTERVAL: float = 30.0


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


class FlowScheduler:
    """Background scheduler for recurring flow runs.

    Checks flow_schedules every `check_interval` seconds for cron triggers.
    Applies on_overlap policy and creates new flow runs via the provided
    executor callback.

    Attributes:
        check_interval: Seconds between schedule check cycles.
    """

    def __init__(
        self,
        db: FlowstateDB,
        emit: Callable[[FlowEvent], None],
        start_flow_callback: Callable[[str], str] | None = None,
        check_interval: float = DEFAULT_CHECK_INTERVAL,
    ) -> None:
        """Initialize the scheduler.

        Args:
            db: Database for querying schedules and creating runs.
            emit: Event callback for emitting schedule events.
            start_flow_callback: Optional callback to start a flow run. Receives
                flow_definition_id and returns flow_run_id. If None, the scheduler
                will only create runs with 'created' status without starting them.
            check_interval: Seconds between check cycles. Defaults to 30.
        """
        self._db = db
        self._emit = emit
        self._start_flow_callback = start_flow_callback
        self._check_interval = check_interval
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background scheduler loop."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background scheduler."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_loop(self) -> None:
        """Main scheduler loop. Checks for triggers every check_interval seconds."""
        while self._running:
            try:
                await self._check_schedules()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in scheduler loop")

            try:
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break

    async def _check_schedules(self) -> None:
        """Check all enabled schedules for due triggers."""
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        schedules = self._db.get_due_schedules(now_iso)

        for schedule in schedules:
            try:
                await self._process_schedule(schedule, now)
            except Exception:
                logger.exception(f"Error processing schedule {schedule.id}")
                # Continue to next schedule -- one failure should not block others

    async def _process_schedule(self, schedule: FlowScheduleRow, now: datetime) -> None:
        """Process a single triggered schedule."""
        # Verify the flow definition still exists
        flow_def = self._db.get_flow_definition(schedule.flow_definition_id)
        if flow_def is None:
            logger.warning(
                f"Flow definition {schedule.flow_definition_id} not found "
                f"for schedule {schedule.id}"
            )
            # Still advance the schedule to avoid re-triggering
            self._advance_schedule(schedule, now)
            return

        # Check for active runs of this flow
        active_runs = self._get_active_runs(schedule.flow_definition_id)
        has_active = len(active_runs) > 0

        overlap = schedule.on_overlap

        if overlap == "skip" and has_active:
            self._emit(
                FlowEvent(
                    type=EventType.SCHEDULE_SKIPPED,
                    flow_run_id="",
                    timestamp=_now_iso(),
                    payload={
                        "flow_definition_id": schedule.flow_definition_id,
                        "reason": "Active run exists (on_overlap=skip)",
                    },
                )
            )
            self._advance_schedule(schedule, now)
            return

        if overlap == "queue" and has_active:
            # Create a run with 'created' status (queued, not started)
            flow_run_id = self._db.create_flow_run(
                flow_definition_id=schedule.flow_definition_id,
                data_dir=f"~/.flowstate/runs/queued-{schedule.id}",
                budget_seconds=0,
                on_error="pause",
            )
            self._emit(
                FlowEvent(
                    type=EventType.SCHEDULE_TRIGGERED,
                    flow_run_id=flow_run_id,
                    timestamp=_now_iso(),
                    payload={
                        "flow_definition_id": schedule.flow_definition_id,
                        "flow_run_id": flow_run_id,
                        "cron_expression": schedule.cron_expression,
                        "queued": True,
                    },
                )
            )
            self._advance_schedule(schedule, now)
            return

        # on_overlap == "parallel" or no active runs -- start immediately
        flow_run_id: str | None = None
        if self._start_flow_callback is not None:
            flow_run_id = self._start_flow_callback(schedule.flow_definition_id)
        else:
            # No executor callback -- just create the run record
            flow_run_id = self._db.create_flow_run(
                flow_definition_id=schedule.flow_definition_id,
                data_dir=f"~/.flowstate/runs/scheduled-{schedule.id}",
                budget_seconds=0,
                on_error="pause",
            )

        self._emit(
            FlowEvent(
                type=EventType.SCHEDULE_TRIGGERED,
                flow_run_id=flow_run_id or "",
                timestamp=_now_iso(),
                payload={
                    "flow_definition_id": schedule.flow_definition_id,
                    "flow_run_id": flow_run_id or "",
                    "cron_expression": schedule.cron_expression,
                },
            )
        )
        self._advance_schedule(schedule, now)

    def _advance_schedule(self, schedule: FlowScheduleRow, now: datetime) -> None:
        """Update last_triggered_at and compute next_trigger_at from the cron expression."""
        try:
            cron = croniter(schedule.cron_expression, now)
            next_trigger: datetime = cron.get_next(datetime)
            self._db.update_flow_schedule(
                schedule.id,
                last_triggered_at=now.isoformat(),
                next_trigger_at=next_trigger.isoformat(),
            )
        except (ValueError, KeyError):
            logger.exception(
                f"Invalid cron expression '{schedule.cron_expression}' "
                f"for schedule {schedule.id}"
            )
            # Disable the schedule to prevent repeated errors
            self._db.update_flow_schedule(schedule.id, enabled=0)

    def _get_active_runs(self, flow_definition_id: str) -> list[object]:
        """Get active (running or paused) flow runs for a flow definition."""
        all_runs = self._db.list_flow_runs()
        return [
            r
            for r in all_runs
            if r.flow_definition_id == flow_definition_id and r.status in ("running", "paused")
        ]

    async def check_once(self) -> None:
        """Run a single check cycle. Useful for testing."""
        await self._check_schedules()

    @property
    def is_running(self) -> bool:
        """True if the background loop is currently running."""
        return self._running and self._task is not None and not self._task.done()
