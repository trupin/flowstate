# [ENGINE-036] Message queue + re-invocation loop + interrupt in executor

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-035, STATE-009
- Blocks: SERVER-014

## Spec References
- specs.md Section 6.2 — "Task Execution Lifecycle"
- specs.md Section 9.1 — "Task Subprocess Invocation"

## Summary
Implement the executor-level logic for interactive agent messaging: a per-task message queue, a re-invocation loop that keeps the agent working until all user messages are processed, and interrupt support that pauses the agent for user interaction. The orchestrator checks for unprocessed messages after each agent turn and re-invokes the agent with a structured prompt containing the pending messages.

## Acceptance Criteria
- [ ] Executor maintains a per-task message queue (backed by STATE-009 `task_messages` table)
- [ ] `send_message(task_execution_id, message)` enqueues a message for any active task (running or interrupted)
- [ ] After each agent turn (prompt completes), executor checks for unprocessed messages
- [ ] If messages exist, executor re-invokes the agent with a structured prompt containing all pending messages
- [ ] Re-invocation loop continues until no more messages are queued
- [ ] `interrupt(task_execution_id)` cancels the current agent turn via `harness.interrupt()` and sets task status to `interrupted`
- [ ] An interrupted task does NOT proceed to edge evaluation — it waits for a user message to resume
- [ ] When a message is sent to an interrupted task, the agent is re-invoked with the message and status returns to `running`
- [ ] `send_message()` to a completed/failed task raises `RuntimeError`
- [ ] Task cannot complete (edges not evaluated) while unprocessed messages exist
- [ ] WebSocket events emitted: `task.interrupted` when interrupted, status change back to `running` on resume

## Technical Design

### Files to Modify

- `src/flowstate/engine/executor.py` — Refactor `_execute_subprocess_task` to use the long-lived session API from ENGINE-035:

```python
async def _execute_subprocess_task(self, ...):
    await harness.start_session(workspace, session_id)
    try:
        # Initial prompt
        async for event in harness.prompt(session_id, task_prompt):
            self._emit_log(event)

        # Re-invocation loop: process queued messages
        while True:
            messages = self._db.get_unprocessed_messages(task_execution_id)
            if not messages:
                break
            self._db.mark_messages_processed(task_execution_id)
            combined_prompt = self._format_user_messages(messages)
            async for event in harness.prompt(session_id, combined_prompt):
                self._emit_log(event)
    finally:
        await harness.kill(session_id)
```

Add interrupt handling:
```python
async def interrupt(self, task_execution_id: str) -> None:
    task = self._db.get_task_execution(task_execution_id)
    if task.status != "running":
        raise RuntimeError(f"Task {task_execution_id} is not running")
    session_id = task.claude_session_id
    await self._harness_mgr.get(harness_name).interrupt(session_id)
    self._db.update_task_status(task_execution_id, "interrupted")
    self._emit(FlowEvent(type=EventType.TASK_INTERRUPTED, ...))
```

Add send_message:
```python
async def send_message(self, task_execution_id: str, message: str) -> None:
    task = self._db.get_task_execution(task_execution_id)
    if task.status not in ("running", "interrupted"):
        raise RuntimeError(f"Cannot send message to {task.status} task")
    self._db.insert_task_message(task_execution_id, message)
    if task.status == "interrupted":
        # Resume the agent with the message
        self._resume_interrupted_task(task_execution_id)
```

- `src/flowstate/engine/events.py` (or wherever EventType is defined) — Add `TASK_INTERRUPTED` event type.

### Re-invocation Prompt Format

```
The user sent you the following message(s) while you were working:

- "please also check the edge cases"
- "use pytest not unittest"

Address these messages, then continue your task.
```

### Interrupt + Resume Flow

1. User clicks Interrupt → `executor.interrupt(task_id)` → `harness.interrupt(session_id)` cancels prompt → task status = `interrupted`
2. The `_execute_subprocess_task` coroutine is waiting on `harness.prompt()` which gets cancelled → needs to handle `CancelledError` or ACP cancellation signal gracefully
3. Task enters a wait state (asyncio.Event or similar) — the execution coroutine doesn't exit, it waits for resume
4. User sends message → `executor.send_message(task_id, msg)` → message queued → signals the wait
5. Execution coroutine wakes up → task status = `running` → re-invocation loop continues with the message
6. If more messages arrive during re-invocation, they're picked up in the next loop iteration

### Edge Cases
- Interrupt while no prompt is active (between turns) → no-op, task already between turns
- Multiple interrupts → idempotent
- Message sent to running task (not interrupted) → queued, processed after current turn
- Multiple messages sent before processing → all delivered in one re-invocation prompt
- Agent subprocess crashes during re-invocation → task fails, unprocessed messages remain in DB
- Race: message sent at exact moment task completes → executor checks queue one final time before declaring done

## Regression Risks
- The `_execute_subprocess_task` refactor touches the core execution loop — extensive testing needed
- Existing pause/resume (flow-level) must not conflict with task-level interrupt
- Edge evaluation timing changes: edges are only evaluated after ALL messages processed
- Cancel (flow-level) must still work: kills subprocess regardless of interrupt state

## Testing Strategy
- Unit test: send_message queues to DB
- Unit test: re-invocation loop calls prompt() again with formatted messages
- Unit test: interrupt cancels prompt and sets status to interrupted
- Unit test: message to interrupted task resumes execution
- Unit test: task doesn't complete while unprocessed messages exist
- Unit test: message to completed task raises error
- Integration test: full cycle — start task, send message while running, verify re-invocation
- Integration test: interrupt → send message → verify resume
- `uv run pytest tests/engine/ && uv run ruff check . && uv run pyright`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
