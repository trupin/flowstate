# [ENGINE-050] Stream ACP events in real-time instead of batch-after-completion

## Domain
engine

## Status
done

## Priority
P1

## Dependencies
- Depends on: ENGINE-049
- Blocks: —

## Spec References
- specs.md Section 9.2 — "Log Streaming Protocol"

## Summary
The log viewer shows all events at once when a task finishes, instead of streaming them in real-time during execution. The ACP library correctly delivers `session/update` notifications as they arrive (confirmed by reading the SDK source at `acp/connection.py:148-162`), and our `_AcpBridgeClient.session_update()` enqueues them immediately. But both `_run_acp_session()` (~line 867) and `prompt()` (~line 562) in `acp_client.py` block on `await conn.prompt()` and only drain the queue AFTER it returns — yielding all accumulated events in one batch.

The UI already handles incremental log events correctly (`useFlowRun.ts` appends via `task.log` WebSocket events). The executor's `_stream_events()` inserts each event into the DB + emits a WebSocket event immediately. The bottleneck is entirely in `acp_client.py`.

## Acceptance Criteria
- [ ] Log events appear in the UI console incrementally during task execution (not all at once at the end)
- [ ] Both `_run_acp_session()` (one-shot) and `prompt()` (long-lived session) paths stream events
- [ ] Cancel/interrupt during streaming works correctly (no dangling tasks or leaked events)
- [ ] All existing ACP client tests pass
- [ ] No regression in flow execution behavior

## Technical Design

### New method: `_prompt_and_stream()`

Add to `AcpHarness` — runs `conn.prompt()` as a concurrent `asyncio.Task` while draining the queue via `await queue.get()`:

- `asyncio.create_task(conn.prompt(**prompt_args))` runs prompt in background
- `add_done_callback` enqueues `None` sentinel when prompt finishes
- `while True: event = await queue.get()` drains in real-time until sentinel
- `finally` block cancels the task if generator is closed (prevents leaks)
- After sentinel: drain stragglers, get result, yield RESULT + SYSTEM events

### Files to Modify
- `src/flowstate/engine/acp_client.py` — Add `_prompt_and_stream()`, refactor `_run_acp_session()` and `prompt()`
- `tests/engine/test_acp_client.py` — Add streaming behavior tests

### Key Design Decisions
- Sentinel via `add_done_callback`: fires synchronously on task completion, no race window
- `finally` block cancels task on GeneratorExit: prevents dangling tasks
- Double sentinel from cancel is harmless: first breaks the loop, second sits in abandoned queue
- Stale-queue cleanup at start of `prompt()`: drain leftover sentinels from previous cancel/interrupt

### Edge Cases
- `cancel_and_terminate()` called during streaming: produces two sentinels (harmless)
- `conn.prompt()` raises exception: sentinel still fired via callback, events drained, exception re-raised
- Generator closed by consumer: `finally` cancels the prompt task

## Testing Strategy
- Test that events are yielded DURING prompt execution (not after)
- Test error propagation when prompt() fails mid-stream
- Test cancel during streaming
- Verify all existing ACP client tests pass

## Completion Checklist
- [ ] Implementation complete
- [ ] Unit tests written and passing
- [ ] `/lint` passes (ruff, pyright)
- [ ] Visual verification: logs stream incrementally in UI
