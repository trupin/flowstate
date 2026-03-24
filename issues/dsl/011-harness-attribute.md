# [DSL-011] Add `harness` attribute to grammar, parser, and AST

## Domain
dsl

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: ENGINE-033

## Spec References
- specs.md Section 3.2 — "Flow Declaration"
- specs.md Section 3.4 — "Node Declarations"

## Summary
Add a `harness` string attribute to the DSL at flow level (default for all nodes) and per-node (override). This tells the engine which agent runtime to use for executing each node. Default is `"claude"`.

## Acceptance Criteria
- [ ] Grammar: `harness = "gemini"` parses as a flow-level attribute
- [ ] Grammar: `harness = "custom"` parses as a node-level attribute (entry, task, atomic)
- [ ] AST: `Flow.harness` is `str` with default `"claude"`
- [ ] AST: `Node.harness` is `str | None` with default `None`
- [ ] Parser: transformer methods follow the `flow_workspace` / `node_cwd` pattern
- [ ] No type-checker changes needed (harness name validated at runtime)
- [ ] All existing tests pass (no fixture changes needed — `harness` is optional with default)

## Technical Design

### Files to Modify
- `src/flowstate/dsl/grammar.lark` — Add `| "harness" "=" STRING -> flow_harness` to `flow_attr`; Add `| "harness" "=" STRING -> node_harness` to `node_attr`
- `src/flowstate/dsl/parser.py` — Add `flow_harness` and `node_harness` transformer methods; Add `harness=` to `flow_decl` and node constructors
- `src/flowstate/dsl/ast.py` — Add `harness: str = "claude"` to `Flow` (after `judge`); Add `harness: str | None = None` to `Node` (after `judge`)

### Key Implementation Details
Follow the exact pattern used by `workspace` (string-valued, optional):
```python
def flow_harness(self, items: list[Token]) -> tuple[str, str]:
    return ("harness", _strip_string(items[0]))

def node_harness(self, items: list[Token]) -> tuple[str, str]:
    return ("harness", _strip_string(items[0]))
```

In `flow_decl`: `harness=str(attrs["harness"]) if "harness" in attrs else "claude"`

### Edge Cases
- Flow without `harness` → defaults to `"claude"`
- Node without `harness` → `None` (inherit from flow)
- `wait` and `fence` nodes don't get `harness` (they have no prompt / don't spawn agents)

## Testing Strategy
- `tests/dsl/fixtures/valid_harness.flow` — New fixture with flow-level + node-level harness
- `tests/dsl/test_parser.py` — Parse flow with harness, verify AST fields
- `tests/dsl/test_parser.py` — Parse node with harness override, verify `node.harness`
- `tests/dsl/test_parser.py` — Parse flow without harness, verify default `"claude"`
- `uv run pytest tests/dsl/ -x`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
