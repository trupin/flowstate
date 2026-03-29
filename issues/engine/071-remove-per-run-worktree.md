# [ENGINE-071] Remove per-run worktree logic and clean up executor

## Domain
engine

## Status
todo

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: ENGINE-070
- Blocks: —

## Spec References
- specs.md Section 9.7 — "Worktree Isolation"

## Summary
Remove the legacy per-run worktree code from the executor now that per-node worktrees (ENGINE-070) have replaced it. This is cleanup: delete `self._worktree_info`, `self._worktree_cleanup`, `_apply_worktree_mapping()`, `_cleanup_worktree()`, the `setup_worktree_if_needed()` call in `execute()`, and the `worktree_cleanup` config parameter. The `flow_runs.worktree_path` column becomes unused (keep for backwards compat).

## Acceptance Criteria
- [ ] `self._worktree_info` removed from executor
- [ ] `self._worktree_cleanup` removed from executor
- [ ] `_apply_worktree_mapping()` removed from executor
- [ ] `_cleanup_worktree()` removed from executor
- [ ] `setup_worktree_if_needed()` call removed from `execute()`
- [ ] `worktree_cleanup` parameter removed from `FlowExecutor.__init__`
- [ ] `worktree_cleanup` removed from `FlowstateConfig`
- [ ] `update_flow_run_worktree()` call removed
- [ ] Old per-run worktree tests removed/updated
- [ ] All existing tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — remove per-run worktree state and methods
- `src/flowstate/engine/worktree.py` — remove `setup_worktree_if_needed()` (keep `create_worktree`, `cleanup_worktree` which are reused by per-node)
- `src/flowstate/config.py` — remove `worktree_cleanup` field
- `src/flowstate/server/routes.py` — remove `worktree_cleanup` from executor creation
- `src/flowstate/server/app.py` — remove `worktree_cleanup` from ws_hub config
- `tests/engine/test_executor.py` — update tests

### Key Implementation Details

This is pure deletion. Every reference to per-run worktree in the executor is replaced by the per-node worktree artifact reads in ENGINE-070.

## Testing Strategy
- Verify all tests pass after removal
- Verify no references to removed functions remain

## E2E Verification Plan

### Verification Steps
1. Run the full test suite
2. Verify fork/join flows still work with per-node worktrees

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
