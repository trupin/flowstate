# [DSL-012] Add `tasks` boolean attribute to grammar, parser, and AST

## Domain
dsl

## Status
done

## Priority
P1

## Dependencies
- Depends on: none
- Blocks: ENGINE-038

## Spec References
- specs.md Section 3.2 — "Flow Declaration"
- specs.md Section 3.4 — "Node Declarations"
- specs.md Section 11.1 — "AST Node Definitions"

## Summary
Add a `tasks` boolean attribute that controls whether agents running in a node have access to a local subtask management system. This follows the same pattern as `judge` — a flow-level default (default `false`) with per-node overrides. When enabled, the engine injects API instructions into the agent's prompt so it can create, list, and update subtasks tracked in the Flowstate database.

## Acceptance Criteria
- [ ] Grammar accepts `tasks = true | false` at the flow level (same position as `judge`, `harness`, etc.)
- [ ] Grammar accepts `tasks = true | false` at the node level for `entry`, `task`, `exit`, and `atomic` nodes
- [ ] Parser transforms `flow_tasks` and `node_tasks` rules into AST fields
- [ ] `Flow` dataclass has `tasks: bool = False` field
- [ ] `Node` dataclass has `tasks: bool | None = None` field (None = inherit from flow)
- [ ] Existing tests still pass (no regressions in grammar/parser)
- [ ] New tests cover parsing `tasks` at both flow and node levels

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/grammar.lark` — Add `tasks` flow attribute and node attribute rules
- `src/flowstate/dsl/ast.py` — Add `tasks` field to `Flow` and `Node` dataclasses
- `src/flowstate/dsl/parser.py` — Handle `flow_tasks` and `node_tasks` transformer methods
- `tests/dsl/test_parser.py` — Add tests for `tasks` attribute parsing

### Key Implementation Details
Follow the exact pattern used for `judge`:

**Grammar** (`grammar.lark`):
- Flow attribute: `| "tasks" "=" BOOL_LIT -> flow_tasks` (line ~26, alongside `flow_judge`)
- Node attribute: `| "tasks" "=" BOOL_LIT -> node_tasks` (line ~63, alongside `node_judge`)

**AST** (`ast.py`):
- `Flow`: add `tasks: bool = False` after `judge: bool = False` (line ~94)
- `Node`: add `tasks: bool | None = None` after `judge: bool | None = None` (line ~56)

**Parser** (`parser.py`):
- Add `flow_tasks` transformer method (same pattern as `flow_judge`)
- Add `node_tasks` transformer method (same pattern as `node_judge`)

**No type checker changes needed** — `tasks` is a simple boolean with no structural constraints.

### Edge Cases
- `wait` and `fence` nodes should NOT accept `tasks` (they don't spawn subprocesses). The grammar already restricts `node_body` to entry/task/exit/atomic, so this is handled naturally.
- Existing flows without `tasks` attribute default to `false` (backwards compatible).

## Testing Strategy
- Parse a flow with `tasks = true` at flow level → verify `flow.tasks == True`
- Parse a flow with `tasks = false` at flow level → verify `flow.tasks == False`
- Parse a flow with no `tasks` attribute → verify `flow.tasks == False` (default)
- Parse a node with `tasks = true` → verify `node.tasks == True`
- Parse a node with `tasks = false` → verify `node.tasks == False`
- Parse a node with no `tasks` → verify `node.tasks is None`
- Verify `wait` and `fence` nodes cannot have `tasks` attribute

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
