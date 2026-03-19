# [ENGINE-008] Add subprocess watchdog to detect stuck tasks

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md Section 6 — Task execution lifecycle

## Summary
Claude Code subprocesses sometimes exit without the executor's async stream reader detecting EOF. This leaves tasks in "running" status indefinitely with no new log output. The executor needs a watchdog mechanism to detect and recover from stuck tasks.

## Acceptance Criteria
- [ ] Tasks that produce no log output for a configurable timeout (default 5 minutes) are detected as stuck
- [ ] Stuck tasks are automatically marked as failed with a descriptive error message
- [ ] The flow's on_error policy (pause/abort/skip) is applied after detecting a stuck task
- [ ] A TASK_FAILED event is emitted so the UI updates
- [ ] The watchdog does not interfere with legitimately long-running tasks that are actively producing output

## Technical Design

### Files to Modify
- `src/flowstate/engine/executor.py` — Add watchdog timer to `_execute_single_task`
- `src/flowstate/engine/subprocess_mgr.py` — Investigate why EOF detection fails

### Key Implementation Details
- Add an `asyncio.wait_for` or periodic check in `_execute_single_task` that monitors the time since the last stream event
- If no event received for 5 minutes, kill the subprocess and mark the task as failed
- Also check if the subprocess PID is still alive via `proc.returncode` — if it's not None, the process has exited and we should stop waiting
- The 10MB buffer limit may cause the readline to hang if a partial line exceeds memory — investigate if this is the root cause

### Root Cause Investigation
The `_run_streaming` method uses `proc.stdout.readline()` which blocks until a newline. If the subprocess exits mid-line (crashes without flushing), the readline may hang indefinitely waiting for more data. Solutions:
1. Use `asyncio.wait_for(proc.stdout.readline(), timeout=300)` with a 5-minute timeout
2. Monitor `proc.returncode` in parallel — if process exits, break the read loop
3. Use `proc.wait()` in a separate task and cancel the read loop when the process exits

### Edge Cases
- Task that legitimately takes >5min between outputs (e.g., long API call) — the watchdog should reset on each event
- Subprocess that produces partial output then hangs — readline timeout catches this
- Multiple stuck tasks simultaneously — each has its own watchdog

## Testing Strategy
- Mock a subprocess that exits without producing a final newline
- Verify the watchdog detects and fails the task within the timeout
- Verify the flow pauses/aborts/skips according to on_error policy
