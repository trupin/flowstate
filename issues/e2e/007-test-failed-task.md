# [E2E-007] Test: Failed Task

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
- specs.md Section 6.2 — "Task Execution Lifecycle"
- specs.md Section 10.6 — "Control Panel" (retry/skip buttons)

## Summary
E2E tests for failed task handling: verifying the UI shows failed status, retry and skip buttons appear, retry re-executes the task, and skip continues past it.

## Acceptance Criteria
- [ ] `tests/e2e/test_failed_task.py` exists with 3 test functions
- [ ] Tests verify failed node turns red and flow pauses (on_error=pause)
- [ ] Tests verify retry re-executes the task (configure success on second attempt)
- [ ] Tests verify skip marks node as skipped and flow continues
- [ ] All tests pass: `uv run pytest tests/e2e/test_failed_task.py -v`

## Technical Design

### Tests

1. **test_failed_task_shows_red** — Configure "risky" as NodeBehavior.failure(), start FAILING_TASK_FLOW, verify `[data-testid="node-risky"][data-status="failed"]` is visible, verify flow status is "Paused".

2. **test_retry_failed_task** — After failure, reconfigure "risky" as NodeBehavior.success(), click `[data-testid="btn-retry"]`, verify node re-executes and flow completes.

3. **test_skip_failed_task** — After failure, click `[data-testid="btn-skip"]`, verify node shows `data-status="skipped"`, verify flow continues to "done" node and completes.

## Testing Strategy
Uses FAILING_TASK_FLOW (entry→risky→exit) with on_error=pause. The "risky" node is configured to fail on first attempt. For retry test, reconfigure to success before clicking retry. The mock's reset is NOT called between configure_node calls within a test.
