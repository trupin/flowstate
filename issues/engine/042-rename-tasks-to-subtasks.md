# [ENGINE-042] Rename `tasks` references to `subtasks` in executor and context

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: DSL-013
- Blocks: none

## Spec References
- specs.md Section 14.5 — "Prompt Injection"

## Summary
Update the engine layer to use the renamed `subtasks` attribute from the AST. The `_use_tasks()` helper, executor injection logic, and all references to `flow.tasks` / `node.tasks` must use the new `subtasks` field name.

## Acceptance Criteria
- [ ] `_use_tasks()` renamed to `_use_subtasks()` in executor.py
- [ ] All `flow.tasks` → `flow.subtasks` and `node.tasks` → `node.subtasks` references updated
- [ ] Engine tests updated and passing with new name
- [ ] No references to the old `tasks` attribute remain in engine code

## Technical Design

### Files to Modify
- `src/flowstate/engine/executor.py` — Rename helper function and all attribute references
- `tests/engine/test_executor.py` — Update tests for renamed function and fields

### Key Implementation Details

**executor.py:**
- Lines 144-152: `_use_tasks(flow, node)` → `_use_subtasks(flow, node)` — update function name, `node.tasks` → `node.subtasks`, `flow.tasks` → `flow.subtasks`
- Lines ~2478-2489: `_use_tasks(flow, node)` call → `_use_subtasks(flow, node)`

**tests/engine/test_executor.py:**
- Update test fixtures: `Flow(tasks=True)` → `Flow(subtasks=True)`, `Node(tasks=True)` → `Node(subtasks=True)`
- Update function call tests: `_use_tasks()` → `_use_subtasks()`

### Edge Cases
- `build_task_management_instructions()` in `context.py` does NOT need renaming — it refers to the feature (task management), not the DSL attribute name

## Testing Strategy
- Run engine tests: `uv run pytest tests/engine/`
- Verify `_use_subtasks()` correctly resolves node-level override → flow-level default → false

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
