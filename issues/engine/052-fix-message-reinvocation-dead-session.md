# [ENGINE-052] Fix message re-invocation after interrupt (dead ACP session)

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section 6.8 — "Cycle Re-entry"
- specs.md Section 9.8 — ACP harness lifecycle

## Summary
When a user interrupts a running agent task and then sends a message with new instructions, the executor's re-invocation loop tries to call `harness.prompt(session_id, combined_prompt)` on a dead ACP session, which crashes with "No active session with id '...'". The root cause is that `run_task()` is a one-shot wrapper whose `_run_acp_session()` removes the session from `self._sessions` in its `finally` block (line ~924 of `acp_client.py`). After the initial prompt completes (or is cancelled by interrupt), the session is gone, but the re-invocation loop still tries to use it.

## Acceptance Criteria
- [ ] After interrupting a running task, sending a message successfully re-invokes the agent with the new instructions
- [ ] The agent receives both its prior conversation context and the new message
- [ ] Multiple interrupt+message cycles work in sequence (interrupt → message → interrupt → message)
- [ ] Sending a message to a running task (non-interrupted) that has completed its current prompt also works
- [ ] Existing non-messaging flows are unaffected (no regressions)

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — fix re-invocation loop to use `run_task_resume()` when session is dead

### Key Implementation Details

In the re-invocation loop (around line 2294), the current code:
```python
re_stream = harness.prompt(session_id, combined_prompt)
```

This fails because the session was removed from `self._sessions` when `_run_acp_session()` exited.

**Fix**: Replace `harness.prompt()` with `harness.run_task_resume()`, which spawns a fresh subprocess, loads the persisted ACP session via `session/load`, and runs the new prompt:

```python
re_stream = harness.run_task_resume(
    combined_prompt,
    cwd,
    session_id,
    skip_permissions=skip_perms,
)
exit_code = await self._stream_events(re_stream, task_execution_id, flow_run_id, session_id)
```

The `cwd` and `skip_perms` values need to be captured from the enclosing scope (they're available from the initial task setup). `session_id` is already tracked in `self._task_session`.

ACP session data persists on disk after subprocess termination (Claude Code stores sessions in files), so `run_task_resume()` can load the conversation history into a new subprocess.

### Edge Cases
- If the ACP session data was corrupted or deleted, `run_task_resume()` should fail gracefully (task fails, on_error policy applies)
- Multiple rapid interrupt+message cycles: each re-invocation creates a new subprocess and loads session — this is correct but slightly slower than keeping a long-lived session
- The `_stream_events` call must update `self._task_session` if `run_task_resume()` produces a new session_id (verify it reuses the same one)

## Testing Strategy
- Unit test: mock AcpHarness, verify that after interrupt + message, `run_task_resume()` is called (not `prompt()`)
- Unit test: verify multiple interrupt+message cycles work in sequence
- Manual test: run a flow, interrupt a task, send a message, verify the agent resumes with the message

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
