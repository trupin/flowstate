# [ENGINE-011] Recurring Flow Scheduling

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-005, STATE-006
- Blocks: none

## Spec References
- specs.md Section 2.10 — "Scheduling" (recurring flow runs, on_overlap policies)
- specs.md Section 5.6.1 — "Scheduler"
- specs.md Section 6.9 — "Recurring Flow Runs"
- specs.md Section 10.3 — "WebSocket Protocol" (schedule.triggered, schedule.skipped events)

## Summary
Implement recurring flow scheduling — a background asyncio task that checks `flow_schedules` for cron triggers every 30 seconds. When a schedule fires, the scheduler evaluates the `on_overlap` policy to decide whether to start a new run, skip it, or queue it. Uses the `croniter` library for cron expression evaluation. Emits `schedule.triggered` and `schedule.skipped` WebSocket events. This module enables flows declared with `schedule = "0 9 * * MON"` to run automatically on a recurring basis.

## Acceptance Criteria
- [ ] File `src/flowstate/engine/scheduler.py` exists and is importable
- [ ] `FlowScheduler` class is implemented with:
  - `__init__(self, db: FlowstateDB, executor: FlowExecutor, event_callback: Callable[[FlowEvent], None])`
  - `async start() -> None` — starts the background checker loop
  - `async stop() -> None` — stops the background checker
- [ ] Background loop runs every 30 seconds
- [ ] Each iteration: queries `flow_schedules` for enabled schedules where `next_trigger_at <= now()`
- [ ] For each triggered schedule, evaluates `on_overlap` policy:
  - `skip`: if any active run exists for this flow, skip (emit `schedule.skipped` event)
  - `queue`: create run with status `created`, start when previous finishes
  - `parallel`: create and start run immediately
- [ ] After triggering: updates `last_triggered_at` and computes `next_trigger_at` using `croniter`
- [ ] `schedule.triggered` event emitted with `flow_definition_id`, `flow_run_id`, `cron_expression`
- [ ] `schedule.skipped` event emitted with `flow_definition_id` and `reason`
- [ ] Uses `croniter` library for cron expression parsing and next-trigger computation
- [ ] Scheduler is resilient to individual schedule errors (one failing schedule does not block others)
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/scheduler.py` — recurring flow scheduler
- `tests/engine/test_scheduler.py` — tests

### Key Implementation Details

#### FlowScheduler Class

```python
import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from croniter import croniter

from flowstate.dsl.ast import Flow
from flowstate.engine.events import EventType, FlowEvent, make_event
from flowstate.engine.executor import FlowExecutor
from flowstate.state.repository import FlowstateDB

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30


class FlowScheduler:
    """Background scheduler for recurring flow runs.

    Checks flow_schedules every 30 seconds for cron triggers.
    Applies on_overlap policy and creates new flow runs.
    """

    def __init__(
        self,
        db: FlowstateDB,
        executor: FlowExecutor,
        event_callback: Callable[[FlowEvent], None],
    ) -> None:
        self._db = db
        self._executor = executor
        self._emit = event_callback
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background scheduler loop."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        """Main scheduler loop. Checks for triggers every 30 seconds."""
        while self._running:
            try:
                await self._check_schedules()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in scheduler loop")

            try:
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break
```

#### Schedule Checking Logic

```python
async def _check_schedules(self) -> None:
    """Check all enabled schedules for triggers."""
    now = datetime.now(timezone.utc)
    schedules = self._db.get_due_schedules(now.isoformat())

    for schedule in schedules:
        try:
            await self._process_schedule(schedule, now)
        except Exception:
            logger.exception(f"Error processing schedule {schedule.id}")
            # Continue to next schedule — one failure shouldn't block others

async def _process_schedule(self, schedule, now: datetime) -> None:
    """Process a single triggered schedule."""
    flow_def = self._db.get_flow_definition(schedule.flow_definition_id)
    if not flow_def:
        logger.warning(f"Flow definition {schedule.flow_definition_id} not found for schedule {schedule.id}")
        return

    # Check on_overlap policy
    active_runs = self._db.get_active_runs_for_flow(schedule.flow_definition_id)
    has_active = len(active_runs) > 0

    if schedule.on_overlap == "skip" and has_active:
        self._emit(make_event(
            EventType.SCHEDULE_SKIPPED,
            flow_run_id="",  # no run created
            flow_definition_id=schedule.flow_definition_id,
            reason=f"Active run exists (on_overlap=skip)",
        ))
        # Still update next_trigger_at
        self._advance_schedule(schedule, now)
        return

    if schedule.on_overlap == "queue" and has_active:
        # Create run with status 'created' (not started)
        flow_ast = self._load_flow_ast(flow_def)
        flow_run_id = self._db.create_flow_run(
            flow_definition_id=schedule.flow_definition_id,
            status="created",
            # ... other fields
        )
        self._emit(make_event(
            EventType.SCHEDULE_TRIGGERED,
            flow_run_id=flow_run_id,
            flow_definition_id=schedule.flow_definition_id,
            cron_expression=schedule.cron_expression,
        ))
        # The queued run will be started when the active run finishes.
        # This requires the executor to check for queued runs on flow completion.
        self._advance_schedule(schedule, now)
        return

    # on_overlap == "parallel" or no active runs — start immediately
    flow_ast = self._load_flow_ast(flow_def)
    workspace = flow_ast.workspace or ""
    params = {}  # recurring flows use default params

    # Start the flow
    flow_run_id = await self._executor.execute(flow_ast, params, workspace)

    self._emit(make_event(
        EventType.SCHEDULE_TRIGGERED,
        flow_run_id=flow_run_id,
        flow_definition_id=schedule.flow_definition_id,
        cron_expression=schedule.cron_expression,
    ))

    self._advance_schedule(schedule, now)
```

#### Schedule Advancement

```python
def _advance_schedule(self, schedule, now: datetime) -> None:
    """Update last_triggered_at and compute next_trigger_at."""
    cron = croniter(schedule.cron_expression, now)
    next_trigger = cron.get_next(datetime)

    self._db.update_schedule_trigger(
        schedule_id=schedule.id,
        last_triggered_at=now.isoformat(),
        next_trigger_at=next_trigger.isoformat(),
    )

def _load_flow_ast(self, flow_def) -> Flow:
    """Load and parse the Flow AST from a flow definition record."""
    import json as json_mod
    from flowstate.dsl.ast import Flow
    # The ast_json field contains the serialized AST
    # Deserialization depends on how the AST is stored
    # This may use a dedicated deserializer
    ast_data = json_mod.loads(flow_def.ast_json)
    return Flow(**ast_data)  # simplified — actual implementation depends on AST serialization
```

#### Queued Run Management

When a flow completes and there are queued runs for the same flow definition, start the next queued run:

```python
# In FlowExecutor._complete_flow or as a post-completion hook:
async def _check_queued_runs(self, flow_definition_id: str) -> None:
    """Start the next queued run if one exists."""
    queued = self._db.get_queued_runs_for_flow(flow_definition_id)
    if queued:
        oldest = queued[0]  # FIFO order
        flow_def = self._db.get_flow_definition(flow_definition_id)
        flow_ast = self._load_flow_ast(flow_def)
        workspace = flow_ast.workspace or ""
        # Start the queued run
        await self._executor.execute_existing_run(oldest.id, flow_ast, {}, workspace)
```

### Database Queries Required (from STATE-006)

The scheduler depends on these repository methods:

- `get_due_schedules(now_iso: str) -> list[FlowSchedule]` — returns enabled schedules where `next_trigger_at <= now`
- `get_active_runs_for_flow(flow_definition_id: str) -> list[FlowRun]` — returns runs with status `running` or `paused`
- `get_queued_runs_for_flow(flow_definition_id: str) -> list[FlowRun]` — returns runs with status `created`, ordered by `created_at`
- `update_schedule_trigger(schedule_id, last_triggered_at, next_trigger_at)` — updates trigger timestamps

### Edge Cases
- **Schedule fires while previous trigger is still being processed**: The 30-second interval provides natural batching. Two triggers within 30 seconds will be caught in the same or adjacent check.
- **Cron expression with high frequency (e.g., every minute)**: With a 30-second check interval, triggers may be delayed up to 30 seconds. The scheduler uses `next_trigger_at` from the DB, so it won't miss triggers — they just may fire slightly late.
- **Schedule fires but flow definition was deleted**: Log a warning and skip. Do not crash.
- **Multiple schedules trigger simultaneously**: Processed sequentially within the check loop. Each is independent.
- **on_overlap=queue with multiple missed triggers**: Only one queued run is created per check. If the schedule fires again before the queued run starts, only the latest trigger matters (since the check sets `next_trigger_at` forward).
- **Server restart**: On restart, the scheduler queries `next_trigger_at`. If triggers were missed during downtime, they will fire immediately on the first check. This is the desired behavior — missed triggers are caught up.
- **Invalid cron expression in DB**: `croniter` raises `ValueError`. The scheduler catches this and logs an error for that schedule, continuing with others.
- **Queued run cleanup**: If a flow is cancelled and there are queued runs, they remain in `created` status. The scheduler does not automatically clean them up — the user can cancel them via the UI.
- **Recurring flow with required params**: The scheduler uses default param values. Flows with required params (no defaults) cannot be scheduled — this should be caught during flow registration.

## Testing Strategy

Create `tests/engine/test_scheduler.py`:

1. **test_scheduler_starts_and_stops** — Start the scheduler, verify the background task is running. Stop it, verify it's cleaned up.

2. **test_cron_trigger_detection** — Create a schedule with `next_trigger_at` in the past. Run one check cycle. Verify the schedule is processed.

3. **test_cron_no_trigger** — Create a schedule with `next_trigger_at` 1 hour from now. Run one check cycle. Verify nothing happens.

4. **test_on_overlap_skip** — Schedule with `on_overlap=skip` and an active run exists. Verify: no new run created, `schedule.skipped` event emitted, `next_trigger_at` advanced.

5. **test_on_overlap_queue** — Schedule with `on_overlap=queue` and an active run exists. Verify: new run created with status `created` (not started), `schedule.triggered` event emitted.

6. **test_on_overlap_parallel** — Schedule with `on_overlap=parallel` and an active run exists. Verify: new run created and started immediately, `schedule.triggered` event emitted.

7. **test_no_active_runs** — Schedule fires with no active runs. Verify: new run started regardless of overlap policy.

8. **test_next_trigger_computed** — After a trigger fires, verify `next_trigger_at` is updated to the next cron match.

9. **test_last_triggered_updated** — After a trigger fires, verify `last_triggered_at` is set to the current time.

10. **test_schedule_error_isolation** — Two schedules due. First raises an error during processing. Verify the second still processes normally.

11. **test_disabled_schedule_ignored** — Schedule with `enabled=0`. Verify it is not processed.

12. **test_queued_run_starts_after_completion** — Create a queued run (status=created). Complete the active run. Verify the queued run starts.

13. **test_missing_flow_definition** — Schedule references a deleted flow definition. Verify: logged warning, no crash, schedule is skipped.

14. **test_invalid_cron_expression** — Schedule with cron `"not a cron"`. Verify: error logged, schedule skipped, others still process.

Mock the `FlowExecutor` (use `AsyncMock` for `execute`) and use in-memory SQLite for the DB. Mock `datetime.now` to control time for deterministic testing.
