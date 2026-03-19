# [E2E-008] Test: Fork-Join

## Domain
e2e

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: E2E-005, ENGINE-006
- Blocks: —

## Spec References
- specs.md Section 6 — "Execution Model" (fork-join coordination)
- specs.md Section 10.4 — "Graph Visualization" (fork/join edge rendering)

## Summary
E2E tests for fork-join execution: verifying parallel tasks are shown simultaneously, both complete independently, and the join node activates only after all fork members finish.

## Acceptance Criteria
- [ ] `tests/e2e/test_fork_join.py` exists with 2-3 test functions
- [ ] Tests verify both fork targets show as running/completed
- [ ] Tests verify join node activates after all fork members complete
- [ ] Tests verify flow reaches "Completed" status
- [ ] All tests pass: `uv run pytest tests/e2e/test_fork_join.py -v`

## Technical Design

### Tests

1. **test_fork_both_targets_execute** — Configure all nodes as success, start FORK_JOIN_FLOW, verify `node-test_unit` and `node-test_integration` both reach completed, verify `node-report` (join) reaches completed.

2. **test_fork_join_ordering** — Use gates on both fork targets, start flow, verify `node-analyze` completes first, release gates one at a time, verify join node only starts after both are done.

3. **test_fork_join_graph_structure** — Verify the graph shows fork edges (multiple edges from analyze) and join edges (multiple edges to report).

## Testing Strategy
Uses FORK_JOIN_FLOW (analyze→[test_unit, test_integration]→report). All nodes configured with NodeBehavior.success(). For ordering test, gates on fork targets give control over completion order.
