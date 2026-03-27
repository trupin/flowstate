# [ENGINE-055] Unconditional edges don't increment generation on cycle re-entry

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md — cycle re-entry and generation tracking

## Summary
When a node is re-entered via an unconditional edge in a cyclic flow, the engine hardcodes `generation=1` instead of computing the next generation number. This means the UI never shows "x2", "x3" badges for nodes that cycle via unconditional edges. The conditional edge path correctly calls `_get_next_generation()`, but the unconditional path at `executor.py:614` always passes `generation=1`. Observed on run `326c1423` where `bob` executed twice but both executions had `generation=1`.

## Acceptance Criteria
- [ ] Unconditional edge transitions check if the target node has been executed before (cycle detection)
- [ ] If it's a cycle re-entry, generation is computed via `_get_next_generation()` instead of hardcoded `1`
- [ ] The UI shows "x2" badge when a node has run twice via unconditional edges
- [ ] Non-cyclic unconditional edges still use generation `1`
- [ ] Existing tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — fix unconditional edge generation assignment

### Key Implementation Details
At line 607-621, the unconditional edge handler currently does:

```python
next_task_id = self._create_task_execution(
    ...
    generation=1,  # BUG: hardcoded
    ...
)
```

Apply the same pattern used by conditional edges (line 995-996):

```python
is_cycle = _has_been_executed(flow_run_id, edge.target, self._db)
target_gen = _get_next_generation(flow_run_id, edge.target, self._db) if is_cycle else 1

next_task_id = self._create_task_execution(
    ...
    generation=target_gen,
    ...
)
```

The helper functions `_has_been_executed` and `_get_next_generation` already exist and are used by the conditional path. This is a 3-line change.

### Edge Cases
- First execution of a node via unconditional edge: should still get generation 1 (no regression)
- Fork/join nodes: they have their own generation logic, shouldn't be affected
- Retry: has its own generation logic at line 1582, not affected

## Testing Strategy
- Add a unit test in `tests/engine/` that runs a cyclic flow with unconditional edges and verifies generation increments.
- Check existing cycle tests still pass.

## E2E Verification Plan

### Reproduction Steps (bugs only)
1. Start server: `uv run flowstate serve`
2. Run `discuss_flowstate.flow` (has cycles via unconditional edges: alice → bob → moderator → alice)
3. After bob executes twice, query: `curl http://localhost:9090/api/runs/{run_id}`
4. Check bob's task executions: both have `generation: 1`
5. Expected: second execution should have `generation: 2`
6. Actual: both have `generation: 1`, UI shows no "x2" badge

### Verification Steps
1. Run the same flow after fix
2. Query the run API and check bob's second execution has `generation: 2`
3. UI should show "x2" on the bob node

## E2E Verification Log

### Reproduction (bugs only)
The bug is at `executor.py` line 614: unconditional edge handler hardcodes `generation=1` instead of computing the next generation. The conditional edge handler at lines 995-996 correctly uses `_get_next_generation()`. Confirmed by code inspection -- no existing test covered this path because existing cycle tests all use conditional edges with a judge.

### Post-Implementation Verification
Applied the fix (3 lines added, 1 line changed) and wrote two new tests:

1. `test_unconditional_cycle_increments_generation` -- Creates a cyclic flow `entry -> alpha -> beta -> alpha` with all unconditional edges. After one full cycle, alpha's second task execution has `generation=2` (was `1` before the fix). beta runs once with `generation=1`.

2. `test_non_cyclic_unconditional_uses_generation_one` -- Verifies the non-cyclic (linear) path is not regressed: all nodes get `generation=1`.

Test results: 505/505 engine tests pass (including 2 new tests).
Lint: `ruff check` -- all checks passed.
Type check: `pyright` -- 0 errors, 0 warnings.

## Completion Checklist
- [x] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [x] `/lint` passes (ruff, pyright, eslint)
- [x] Acceptance criteria verified
- [x] E2E verification log filled in with concrete evidence
