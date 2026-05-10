# [DSL-017] Add `worktree_persist` flow attribute

## Domain
dsl

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: ENGINE-088

## Spec References
- specs.md Section 3.2 — "Flow Declaration"
- specs.md Section 9.7 — "Worktree Isolation" (new "Persistence" subsection)
- specs.md Section 11.1 — "AST" (`Flow.worktree_persist`)

## Summary
Add a `worktree_persist` boolean attribute at the flow level (default `false`). When set, the engine merges the exit node's worktree branch back into the original workspace's source branch on successful flow completion (ENGINE-088). Without this flag, worktree branches are deleted at run end (existing behavior). Type checker rule WP1: `worktree_persist = true` requires `worktree = true` (the persist mechanism only applies when worktree isolation is on).

## Acceptance Criteria
- [ ] `worktree_persist = true | false` parses at flow level (default: false)
- [ ] AST `Flow` dataclass has `worktree_persist: bool = False`
- [ ] Type checker rule WP1: `worktree_persist = true` with `worktree = false` → error: "worktree_persist requires worktree = true"
- [ ] All existing tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/ast.py` — add `worktree_persist: bool = False` to `Flow`
- `src/flowstate/dsl/grammar.lark` — add `flow_worktree_persist` rule
- `src/flowstate/dsl/parser.py` — transformer method, thread through `flow_decl` builder
- `src/flowstate/dsl/type_checker.py` — WP1 rule
- `tests/dsl/fixtures/valid_worktree_persist.flow`
- `tests/dsl/fixtures/invalid_worktree_persist_no_worktree.flow`
- `tests/dsl/test_parser.py`
- `tests/dsl/test_type_checker.py`

### Key Implementation Details

**AST (`ast.py`):**
Add to `Flow` (next to `worktree`):
```python
worktree_persist: bool = False
```

**Grammar (`grammar.lark`):**
Add to `flow_attr`:
```lark
| "worktree_persist" "=" BOOL_LIT -> flow_worktree_persist
```

Add `worktree_persist` to the keyword list in spec 3.1.

**Parser (`parser.py`):**
```python
def flow_worktree_persist(self, items):
    return ("worktree_persist", str(items[0]) == "true")
```

Thread through `flow_decl` to `Flow(...)`.

**Type checker (`type_checker.py`):**
```python
def _check_worktree_persist(flow: Flow) -> list[FlowTypeError]:
    if flow.worktree_persist and not flow.worktree:
        return [FlowTypeError("WP1: worktree_persist = true requires worktree = true")]
    return []
```

### Edge Cases
- `worktree_persist = false` with `worktree = false` → valid (default state, no-op)
- `worktree_persist = false` with `worktree = true` → valid (worktrees used but cleaned up at run end, existing behavior)
- `worktree_persist = true` with `worktree = true` → valid (engine will merge on completion via ENGINE-088)

## Testing Strategy
- Parser tests: verify `worktree_persist` parses correctly
- WP1 test: flow with `worktree = false` and `worktree_persist = true` → error
- Regression: full test suite

## E2E Verification Plan

### Verification Steps
1. Create a `.flow` file with `worktree = true` and `worktree_persist = true`. Run `/check` → passes.
2. Change to `worktree = false`. Run `/check` → WP1 error.

## E2E Verification Log

### Post-Implementation Verification

**Step 1 — Valid combo `worktree = true` + `worktree_persist = true` passes `/check`.**

Command:
```
$ uv run flowstate check tests/dsl/fixtures/valid_worktree_persist.flow
```

Observed output:
```
OK
```

Exit code: `0`. Confirmed via `echo "exit=$?"`.

**Step 2 — Invalid combo `worktree = false` + `worktree_persist = true` fires WP1.**

Command:
```
$ uv run flowstate check tests/dsl/fixtures/invalid_worktree_persist_no_worktree.flow
```

Observed output (stderr):
```
Type error: FlowTypeError(rule='WP1', message='worktree_persist = true requires worktree = true (the persist mechanism only applies when worktree isolation is enabled)', location='persist_without_worktree')
```

Exit code: `1`. Confirmed via `echo "exit=$?"`.

**Conclusion.** Both fixtures behave exactly as the acceptance criteria require: the parser accepts the new attribute (default `false`), the AST exposes it, and the type checker fires `WP1` only when `worktree_persist = true` is paired with `worktree = false`. The error message references both attribute names per the sprint contract's wording requirement (TEST-37c.2).

## Completion Checklist
- [x] Unit tests written and passing (`tests/dsl/test_parser.py::TestWorktreePersistParameter`, `tests/dsl/test_type_checker.py::TestWP1WorktreePersistRequiresWorktree` — 406/406 dsl tests pass)
- [ ] `/simplify` run on all changed code
- [x] `/lint` passes (ruff + pyright clean on `src/flowstate/dsl/` and `tests/dsl/`)
- [x] Acceptance criteria verified
- [x] E2E verification log filled in with concrete evidence
