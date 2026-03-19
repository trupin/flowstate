# [E2E-009] Test: Conditional Branching

## Domain
e2e

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: E2E-005, ENGINE-007
- Blocks: E2E-011

## Spec References
- specs.md Section 7 — "Judge Protocol"
- specs.md Section 6 — "Execution Model" (conditional edges)

## Summary
E2E tests for conditional branching: verifying the judge routes flow to the correct target based on mock decisions. Tests both the "approved" path (to exit) and the "needs work" path (cycle back).

## Acceptance Criteria
- [ ] `tests/e2e/test_conditional.py` exists with 2-3 test functions
- [ ] Tests verify judge decision routes to exit node when "approved"
- [ ] Tests verify judge decision routes back to entry when "needs work"
- [ ] All tests pass: `uv run pytest tests/e2e/test_conditional.py -v`

## Technical Design

### Tests

1. **test_conditional_to_exit** — Configure judge for "review" node to decide "ship" with confidence 0.9, start CONDITIONAL_FLOW, verify flow reaches "ship" node and completes.

2. **test_conditional_cycle_back** — Configure judge for "review" to decide "implement" first time, then "ship" second time, verify "implement" node is re-entered (generation badge), then flow completes via "ship".

3. **test_judge_decision_visible** — After judge decides, verify the edge transition is reflected in the graph (the chosen path is highlighted/active).

## Testing Strategy
Uses CONDITIONAL_FLOW (implement→review→{ship when "approved", implement when "needs work"}). Mock judge configured via `mock_subprocess.configure_judge("review", "ship")`. For cycle test, mock must support sequential decisions (first call returns "implement", second returns "ship").
