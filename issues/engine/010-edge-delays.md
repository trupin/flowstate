# [ENGINE-010] Edge Delay Scheduling

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
- specs.md Section 2.10 — "Scheduling" (edge delays and cron-based delays)
- specs.md Section 5.6.1 — "Scheduler"
- specs.md Section 6.3 — "Execution Algorithm" (enqueue_task with delay/schedule)
- specs.md Section 6.8 — "Edge Delays"
- specs.md Section 10.3 — "WebSocket Protocol" (task.waiting, task.wait_elapsed events)

## Summary
Implement edge delay scheduling — when an edge has a `delay` (duration) or `schedule` (cron expression) configured, the target task enters a `waiting` status with a `wait_until` timestamp instead of immediately becoming `pending`. A background asyncio task periodically checks for waiting tasks whose `wait_until` has elapsed and transitions them to `pending`. Wait time does NOT count toward the flow's budget — only active task execution time counts. This enables patterns like "retry in 5 minutes" (cycle + delay) and "run at 2 AM" (edge cron schedule).

## Acceptance Criteria
- [ ] When `enqueue_task` encounters an edge with `delay_seconds`: task is created with status `waiting` and `wait_until = now + delay_seconds`
- [ ] When `enqueue_task` encounters an edge with `schedule` (cron): task is created with status `waiting` and `wait_until = next cron match`
- [ ] `task.waiting` event is emitted with `task_execution_id`, `node_name`, `wait_until` (ISO timestamp), and `reason` ("delay" or "schedule")
- [ ] Background scheduler task (`_delay_checker`) runs every 30 seconds
- [ ] Scheduler queries DB for waiting tasks where `wait_until <= now()`
- [ ] Matching tasks are transitioned from `waiting` to `pending`
- [ ] `task.wait_elapsed` event is emitted for each transitioned task
- [ ] Wait time does NOT count toward the flow's budget (only running task elapsed_seconds counts)
- [ ] The background scheduler is started when the executor starts and stopped when it finishes
- [ ] Paused flows: waiting tasks remain waiting (the scheduler only transitions to pending, the main loop skips them if paused)
- [ ] Cancelled flows: waiting tasks are marked as failed (handled by cancel logic in ENGINE-008)
- [ ] Cron expression parsing uses `croniter` library
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — modify `enqueue_task` and add background scheduler
- `tests/engine/test_edge_delays.py` — tests

### Key Implementation Details

#### Modified Task Enqueueing

Extend the `_create_task_execution` method (or a wrapper around it) to handle delays:

```python
from datetime import datetime, timezone, timedelta
from croniter import croniter


def _enqueue_with_delay(
    self,
    task_execution_id: str,
    edge: Edge | None,
    flow_run_id: str,
) -> None:
    """Set task to waiting if the edge has a delay or schedule, else add to pending."""
    if edge and edge.config.delay_seconds is not None:
        wait_until = datetime.now(timezone.utc) + timedelta(seconds=edge.config.delay_seconds)
        self._db.update_task_waiting(task_execution_id, wait_until.isoformat())

        task = self._db.get_task_execution(task_execution_id)
        self._emit(FlowEvent(
            type=EventType.TASK_WAITING,
            flow_run_id=flow_run_id,
            timestamp=FlowEvent.now(),
            payload={
                "task_execution_id": task_execution_id,
                "node_name": task.node_name,
                "wait_until": wait_until.isoformat(),
                "reason": "delay",
            },
        ))

    elif edge and edge.config.schedule is not None:
        now = datetime.now(timezone.utc)
        cron = croniter(edge.config.schedule, now)
        next_time = cron.get_next(datetime)
        self._db.update_task_waiting(task_execution_id, next_time.isoformat())

        task = self._db.get_task_execution(task_execution_id)
        self._emit(FlowEvent(
            type=EventType.TASK_WAITING,
            flow_run_id=flow_run_id,
            timestamp=FlowEvent.now(),
            payload={
                "task_execution_id": task_execution_id,
                "node_name": task.node_name,
                "wait_until": next_time.isoformat(),
                "reason": "schedule",
            },
        ))

    else:
        # No delay — immediately pending
        self._pending_tasks.add(task_execution_id)
```

#### Background Delay Checker

```python
async def _run_delay_checker(self, flow_run_id: str) -> None:
    """Background task that checks for elapsed waits every 30 seconds."""
    while not self._cancelled:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            break

        if self._cancelled:
            break

        # Query for waiting tasks whose wait_until has elapsed
        now = datetime.now(timezone.utc).isoformat()
        elapsed_tasks = self._db.get_elapsed_waiting_tasks(flow_run_id, now)

        for task in elapsed_tasks:
            # Transition from waiting to pending
            self._db.update_task_status(task.id, "pending")
            self._pending_tasks.add(task.id)

            self._emit(FlowEvent(
                type=EventType.TASK_WAIT_ELAPSED,
                flow_run_id=flow_run_id,
                timestamp=FlowEvent.now(),
                payload={
                    "task_execution_id": task.id,
                    "node_name": task.node_name,
                },
            ))

        # If new tasks became pending, wake up the main loop
        if elapsed_tasks and self._paused is False:
            self._new_tasks_event.set()
```

#### Starting and Stopping the Checker

In the `execute` method, start the background task and ensure it's cleaned up:

```python
async def execute(self, flow: Flow, params: dict[str, str | float | bool], workspace: str) -> str:
    # ... existing setup ...

    # Start background delay checker
    delay_checker = asyncio.create_task(self._run_delay_checker(flow_run_id))

    try:
        # ... main loop ...
        pass
    finally:
        # Stop the delay checker
        delay_checker.cancel()
        try:
            await delay_checker
        except asyncio.CancelledError:
            pass
```

#### Main Loop Integration

The main loop needs to also consider waiting tasks that may become pending:

```python
# In the main loop, when checking for ready tasks:
while pending or self._running_tasks or self._has_waiting_tasks(flow_run_id):
    if not pending and not self._running_tasks:
        # No ready work — wait for delay checker or a running task to finish
        await self._new_tasks_event.wait()
        self._new_tasks_event.clear()
        # Transfer any newly-pending tasks
        pending.update(self._pending_tasks)
        self._pending_tasks.clear()
        continue
    # ... rest of loop
```

#### Database Query for Elapsed Waits

The state layer (STATE-006) should provide:

```python
# In FlowstateDB:
def get_elapsed_waiting_tasks(self, flow_run_id: str, now_iso: str) -> list[TaskExecution]:
    """Get waiting tasks whose wait_until has elapsed."""
    # Uses the idx_task_executions_waiting index
    # SELECT * FROM task_executions
    # WHERE flow_run_id = ? AND status = 'waiting' AND wait_until <= ?
```

### Edge Cases
- **Delay of 0 seconds**: Task should effectively become pending immediately. The delay checker will pick it up on the next cycle (within 30 seconds), or the initial enqueue can treat delay_seconds=0 as no delay.
- **Cron expression with no future match**: `croniter` always finds a next match, so this shouldn't happen. But if the cron library raises an error, treat it as invalid and fail the task.
- **Flow paused while tasks are waiting**: Waiting tasks remain in `waiting` status. The delay checker still transitions them to `pending`, but the main loop won't pick them up until resume.
- **Flow cancelled while tasks are waiting**: The cancel logic (ENGINE-008) marks all waiting tasks as `failed`.
- **Budget during waiting**: Wait time does NOT count toward the budget. Only the `elapsed_seconds` from actual task execution (running status) is added to `BudgetGuard`.
- **Multiple waiting tasks elapse simultaneously**: The delay checker picks up all of them in one pass and adds them all to pending.
- **Delay checker timing precision**: The checker runs every 30 seconds, so a task may wait up to 30 seconds longer than its `wait_until`. This is acceptable for the use cases (delays are typically minutes/hours, cron is typically minutes/hours).
- **Delay on first edge (entry -> task)**: Valid. The entry node completes, the task enters `waiting`, then eventually becomes `pending`.

## Testing Strategy

Create `tests/engine/test_edge_delays.py`:

1. **test_delay_creates_waiting_task** — Flow with edge `delay_seconds=300`. After source task completes, verify target task has status `waiting` and `wait_until` is approximately now + 300 seconds.

2. **test_delay_emits_waiting_event** — Verify `task.waiting` event is emitted with correct `wait_until` and `reason="delay"`.

3. **test_schedule_creates_waiting_task** — Flow with edge `schedule="0 2 * * *"`. Verify target task has status `waiting` and `wait_until` is the next 2 AM.

4. **test_schedule_emits_waiting_event** — Verify `task.waiting` event with `reason="schedule"`.

5. **test_delay_checker_transitions_to_pending** — Create a waiting task with `wait_until` in the past. Run the delay checker once. Verify task status is now `pending`.

6. **test_delay_checker_emits_elapsed_event** — After transition, verify `task.wait_elapsed` event emitted.

7. **test_delay_checker_ignores_future_waits** — Create a waiting task with `wait_until` 1 hour from now. Run delay checker. Verify task remains `waiting`.

8. **test_wait_time_not_in_budget** — Task completes in 10s, next edge has 300s delay, next task completes in 10s. Verify budget elapsed is 20s (not 320s).

9. **test_delay_checker_multiple_tasks** — Two waiting tasks, both with elapsed wait_until. Verify both are transitioned in one pass.

10. **test_cancel_clears_waiting_tasks** — Cancel flow while tasks are waiting. Verify waiting tasks are marked `failed`.

11. **test_pause_preserves_waiting_tasks** — Pause flow while tasks are waiting. Verify they remain `waiting` and are not lost.

12. **test_end_to_end_delay_flow** — Full flow: `entry -> task_a -> (delay 1s) -> task_b -> exit`. Mock subprocess for all tasks. Use short delay (1 second) and run with accelerated checker interval. Verify flow completes in order.

For timing-sensitive tests, mock `datetime.now` to control time progression rather than relying on actual wall-clock time. Alternatively, set the checker interval to 0 or 1 second for tests.
