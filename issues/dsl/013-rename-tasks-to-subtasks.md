# [DSL-013] Rename `tasks` attribute to `subtasks` in grammar, AST, and parser

## Domain
dsl

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: ENGINE-042

## Spec References
- specs.md Section 3.3 — "Flow Declaration Attributes"
- specs.md Section 3.4 — "Node Declarations"
- specs.md Section 14.2 — "DSL Attribute"

## Summary
Rename the `tasks` boolean attribute (added in DSL-012) to `subtasks` across the DSL layer. The name `tasks` is ambiguous — it conflicts with the concept of task nodes and task queue items. `subtasks` is clearer: it refers to the agent subtask management system (Section 14).

## Acceptance Criteria
- [ ] Grammar keyword is `subtasks` (not `tasks`) at both flow and node level
- [ ] AST field is `subtasks` on both `Flow` and `Node` dataclasses
- [ ] Parser transformer methods are `flow_subtasks` and `node_subtasks`
- [ ] All existing DSL tests updated and passing with new name
- [ ] `.flow` files in `flows/` updated to use `subtasks = true`

## Technical Design

### Files to Modify
- `src/flowstate/dsl/grammar.lark` — Rename keyword and rule names
- `src/flowstate/dsl/ast.py` — Rename field on `Node` (line 58) and `Flow` (line 98)
- `src/flowstate/dsl/parser.py` — Rename transformer methods and dict keys
- `tests/dsl/test_parser.py` — Update `TestTasksParameter` class and all test cases
- `flows/discuss_flowstate.flow` — Change `tasks = true` to `subtasks = true`

### Key Implementation Details

**grammar.lark:**
- Line 27: `| "tasks" "=" BOOL_LIT -> flow_tasks` → `| "subtasks" "=" BOOL_LIT -> flow_subtasks`
- Line 66: `| "tasks" "=" BOOL_LIT -> node_tasks` → `| "subtasks" "=" BOOL_LIT -> node_subtasks`

**ast.py:**
- `Node.tasks: bool | None = None` → `Node.subtasks: bool | None = None`
- `Flow.tasks: bool = False` → `Flow.subtasks: bool = False`

**parser.py:**
- Rename `node_tasks()` → `node_subtasks()`, `flow_tasks()` → `flow_subtasks()`
- All `body.get("tasks")` → `body.get("subtasks")`
- All `tasks=` keyword args → `subtasks=`

**tests/dsl/test_parser.py:**
- Rename class `TestTasksParameter` → `TestSubtasksParameter`
- Update all DSL strings: `tasks = true` → `subtasks = true`
- Update all assertions: `.tasks` → `.subtasks`

### Edge Cases
- No backward compatibility needed — `tasks` was added very recently (DSL-012) and is not yet in user-facing flows beyond the demo

## Testing Strategy
- Run existing `TestTasksParameter` tests (renamed) — all must pass with the new keyword
- Verify `flows/discuss_flowstate.flow` parses successfully after rename
- Run full DSL test suite: `uv run pytest tests/dsl/`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
