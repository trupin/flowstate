# [E2E-011] Test: Cycles

## Domain
e2e

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: E2E-009
- Blocks: —

## Spec References
- specs.md Section 6 — "Execution Model" (cycle re-entry, generation tracking)
- specs.md Section 10.4 — "Graph Visualization" (generation badge)

## Summary
E2E tests for cycle execution: verifying nodes are re-entered with incrementing generation counts, generation badges are displayed in the graph, and the flow eventually exits the cycle.

## Acceptance Criteria
- [ ] `tests/e2e/test_cycles.py` exists with 2 test functions
- [ ] Tests verify node re-entry with generation badge visible (x2, x3)
- [ ] Tests verify flow completes after exiting the cycle
- [ ] All tests pass: `uv run pytest tests/e2e/test_cycles.py -v`

## Technical Design

### Tests

1. **test_cycle_generation_badge** — Configure judge for "verify" to return "more work needed" twice then "all done", start CYCLE_FLOW, verify "implement" node shows generation badge (x3 after 3 executions), verify flow completes via "complete" exit node.

2. **test_cycle_logs_per_generation** — After cycle completes, click "implement" node, verify logs show content from the latest generation (not mixed with earlier generations).

## Testing Strategy
Uses CYCLE_FLOW (plan→implement→verify→{complete, implement}). Mock judge must support sequential decisions. The MockSubprocessManager needs a way to return different decisions on successive calls for the same node — could be a list that pops, or a counter-based callback.
