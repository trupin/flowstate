# [ENGINE-043] Send message silently fails with SubprocessManager harness

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md Section 13 — "Interactive Agent Messaging"
- specs.md Section 14.5 — "Prompt Injection"

## Summary
The Send button in the UI appears to do nothing because user messages are silently swallowed by the SubprocessManager harness. The messaging feature (ENGINE-036) relies on `harness.prompt()` to re-invoke the agent with queued messages, but `SubprocessManager.prompt()` is a no-op that yields nothing. Messages are marked as processed in the DB but never delivered to the agent. This affects both the "Send" (running task) and "Interrupt + Send" (interrupted task) flows.

## Root Cause

In `src/flowstate/server/app.py:253-254`:
```python
harness_mgr = HarnessManager(
    default_harness=subprocess_manager,  # SubprocessManager is the default
)
```

In `src/flowstate/engine/subprocess_mgr.py:248-254`:
```python
async def prompt(self, session_id: str, message: str) -> AsyncGenerator[StreamEvent, None]:
    """Not supported by SubprocessManager -- use run_task() instead."""
    return  # no-op
    yield  # make this an async generator
```

In `src/flowstate/engine/executor.py:2257-2269` (re-invocation loop):
```python
messages = self._db.get_unprocessed_messages(task_execution_id)
self._db.mark_messages_processed(task_execution_id)  # ← message consumed
re_stream = harness.prompt(session_id, combined_prompt)  # ← no-op!
exit_code = await self._stream_events(re_stream, ...)  # ← returns None
# Loop breaks because exit_code != 0
```

## Acceptance Criteria
- [ ] When a user sends a message to a running task, the agent receives and responds to it after its current turn completes
- [ ] When a user sends a message to an interrupted task, the agent resumes and processes the message
- [ ] Messages are not silently discarded regardless of which harness is active
- [ ] Existing tests pass (no regressions)

## Technical Design

### Files to Modify
- `src/flowstate/engine/executor.py` — Re-invocation loop fallback

### Key Implementation Details

In the re-invocation loop (around line 2266), after `harness.prompt()` returns:
1. Check if `exit_code is None` (indicates `prompt()` yielded no events)
2. If so, fall back to `harness.run_task()` with the combined prompt to deliver the message via a new subprocess invocation
3. The fallback uses the task's workspace/cwd and creates a fresh subprocess

```python
re_stream = harness.prompt(session_id, combined_prompt)
exit_code = await self._stream_events(
    re_stream, task_execution_id, flow_run_id, session_id
)

# If prompt() was a no-op (harness doesn't support long-lived sessions),
# fall back to run_task() — spawns a new subprocess with the message.
if exit_code is None:
    logger.info(
        "harness.prompt() unsupported — falling back to run_task() "
        "for message delivery to %s",
        task_execution_id,
    )
    fallback_stream = harness.run_task(
        combined_prompt,
        task_exec.cwd,
        session_id,
        skip_permissions=skip_perms,
    )
    exit_code = await self._stream_events(
        fallback_stream, task_execution_id, flow_run_id, session_id
    )
```

Note: The `run_task()` fallback creates a fresh subprocess that lacks the previous conversation context. This is acceptable for the `handoff` context mode (each task is already a fresh session) and is better than silently discarding the message. For full multi-turn messaging fidelity, users should use an ACP-compatible harness.

### Edge Cases
- `prompt()` returns `None` exit code but the task was cancelled → check `self._cancelled` before fallback
- Multiple queued messages → all delivered in one combined prompt via fallback
- `run_task()` fallback fails → existing error handling marks the task as failed
- AcpHarness `prompt()` works correctly → exit_code is 0, fallback never triggers
- Session ID management: `run_task()` may generate a new session ID → update tracking accordingly

## Testing Strategy
- Add test: `SubprocessManager` harness + send_message → verify message is delivered via `run_task()` fallback
- Add test: `AcpHarness` (or mock with working `prompt()`) → verify `run_task()` fallback is NOT triggered
- Add test: interrupted task + send_message with SubprocessManager → verify resume + delivery
- Run existing engine tests: `uv run pytest tests/engine/`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
