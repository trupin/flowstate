# [E2E-012] Test: Budget Warnings

## Domain
e2e

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: E2E-005, ENGINE-002
- Blocks: —

## Spec References
- specs.md Section 5.6 — "Budget Guard"
- specs.md Section 10.6 — "Control Panel" (budget bar)

## Summary
E2E tests for budget tracking: verifying the budget progress bar updates as tasks consume time, and warning colors change at thresholds. Since mock tasks complete nearly instantly, the budget guard's elapsed time tracking must be exercised through the mock's simulated elapsed time.

## Acceptance Criteria
- [ ] `tests/e2e/test_budget.py` exists with 1-2 test functions
- [ ] Tests verify budget bar is visible during execution
- [ ] Tests verify budget bar shows progress
- [ ] All tests pass: `uv run pytest tests/e2e/test_budget.py -v`

## Technical Design

### Tests

1. **test_budget_bar_visible** — Start a linear flow with a short budget (e.g., 30m), verify `[data-testid="budget-bar"]` is visible during and after execution.

2. **test_budget_bar_updates** — Configure slow tasks (NodeBehavior.slow()) with a flow that has a short budget, verify budget bar percentage increases as tasks complete.

### Note
Budget warning thresholds (75%/90%/95%) are hard to test in E2E because mock tasks complete almost instantly. This test focuses on verifying the budget bar UI component renders and updates. Threshold color changes are better tested at the unit level (ENGINE-002).

## Testing Strategy
Uses LINEAR_FLOW with a short budget. The engine tracks elapsed time based on actual wall-clock time of subprocess execution. Since mock tasks have `line_delay=0.01`, elapsed time will be small. This test primarily verifies the UI component renders correctly.
