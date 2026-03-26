# [ENGINE-051] Interrupt causes flow failure instead of waiting for user input

## Domain
engine

## Status
done

## Priority
P1

## Dependencies
- Depends on: —
- Blocks: —

## Summary
Clicking "Interrupt" in the UI causes the entire flow to fail (on_error=pause triggers) instead of pausing the task and waiting for user input. The re-invocation loop in `_execute_single_task()` checks `while exit_code == 0`, but `harness.interrupt()` sends an ACP cancel which returns `stop_reason="cancelled"` → `exit_code=-1`. This causes the loop to exit immediately without checking `self._interrupted_tasks`, so the interrupt-wait block (lines 2247-2269) never executes.

## Root Cause
`executor.py` line 2245: `while exit_code == 0 and not self._cancelled:`

When interrupted:
1. `interrupt_task()` → `harness.interrupt()` → ACP cancel → `stop_reason="cancelled"` → `exit_code=-1`
2. Loop condition `exit_code == 0` is FALSE → loop body never runs
3. The `if task_execution_id in self._interrupted_tasks` check inside the loop never executes
4. Task falls through to error handling → marked "failed"

## Acceptance Criteria
- [ ] Clicking Interrupt stops the current agent prompt (ACP cancel)
- [ ] After interrupt, the task enters "interrupted" status and waits for user input
- [ ] User can type a message and submit it → agent resumes with the message
- [ ] Flow does NOT fail or pause on interrupt

## Technical Design

### File to Modify
- `src/flowstate/engine/executor.py` — re-invocation loop in `_execute_single_task()` (~line 2245)

### Fix
Move the interrupt check BEFORE the exit_code check. Also allow the loop to continue when exit_code is -1 (cancelled) and the task is in the interrupted set:

```python
# Before (buggy):
while exit_code == 0 and not self._cancelled:
    if task_execution_id in self._interrupted_tasks:
        ...

# After (fixed):
while not self._cancelled:
    # Check for interrupt FIRST — interrupt returns exit_code=-1 but should wait
    if task_execution_id in self._interrupted_tasks:
        # Wait for user message, then re-invoke
        ...
        # After re-invoke, update exit_code and continue
        continue
    if exit_code != 0:
        break
    # Normal message check for non-interrupted tasks
    ...
```

## Testing Strategy
- Unit test: mock interrupt during task execution, verify task enters "interrupted" not "failed"
- E2E: run a flow, click Interrupt, type a message, verify agent resumes

## Completion Checklist
- [ ] Fix applied
- [ ] Unit tests passing
- [ ] `/lint` passes
