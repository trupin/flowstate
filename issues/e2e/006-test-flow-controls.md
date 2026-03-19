# [E2E-006] Test: Flow Controls

## Domain
e2e

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: E2E-005, UI-007, ENGINE-008
- Blocks: —

## Spec References
- specs.md Section 10.6 — "Control Panel"

## Summary
E2E tests for flow control actions: pausing a running flow, resuming it, and cancelling. Uses mock gates to hold tasks at controllable points so the test can interact with control buttons while execution is in progress.

## Acceptance Criteria
- [ ] `tests/e2e/test_flow_controls.py` exists with 3 test functions
- [ ] Tests verify pause button stops execution after current task
- [ ] Tests verify resume button continues paused execution to completion
- [ ] Tests verify cancel button terminates the flow
- [ ] All tests pass: `uv run pytest tests/e2e/test_flow_controls.py -v`

## Technical Design

### Tests

1. **test_pause_and_resume** — Add gate on "work" node, start LINEAR_FLOW, wait for "start" completed + "work" running, click `[data-testid="btn-pause"]`, release gate so "work" completes, verify flow status is "Paused", click `[data-testid="btn-resume"]`, verify flow completes.

2. **test_cancel** — Add gate on "work" node, start flow, wait for "work" running, click `[data-testid="btn-cancel"]`, verify flow status is "Cancelled".

3. **test_pause_button_visibility** — Verify pause button is visible when flow is running, hidden/disabled when completed.

## Testing Strategy
Gates are the key mechanism: `mock_subprocess.add_gate("work")` returns a `threading.Event`. The task blocks until `gate.set()` is called. This gives the test time to click pause/cancel while the flow is mid-execution.
