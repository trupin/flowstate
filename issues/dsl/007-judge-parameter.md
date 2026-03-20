# [DSL-007] Add judge boolean parameter to grammar, AST, parser

## Domain
dsl

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: ENGINE-023

## Summary
Add a `judge` boolean parameter at flow level and node level. When `judge = true`, a separate judge subprocess evaluates routing (existing behavior). When `judge = false` (default), the task agent writes DECISION.json itself. Node-level overrides flow-level; `None` at node level means inherit from flow.

## Acceptance Criteria
- [ ] Grammar accepts `judge = true/false` as flow_attr and node_attr
- [ ] Flow dataclass has `judge: bool = False` field
- [ ] Node dataclass has `judge: bool | None = None` field
- [ ] Parser transforms produce correct AST values
- [ ] Existing flows without judge parameter still parse correctly (backward compatible)

## Technical Design

### Files to Modify
- `src/flowstate/dsl/grammar.lark` — add `judge` to flow_attr and node_attr rules
- `src/flowstate/dsl/ast.py` — add `judge` field to Flow and Node
- `src/flowstate/dsl/parser.py` — add flow_judge/node_judge transformers, update constructors

### Grammar Changes
```
flow_attr: ... | "judge" "=" BOOL_LIT -> flow_judge
node_attr: ... | "judge" "=" BOOL_LIT -> node_judge
```

### AST Changes
```python
@dataclass(frozen=True)
class Node:
    ...
    judge: bool | None = None  # None = inherit from flow

@dataclass(frozen=True)
class Flow:
    ...
    judge: bool = False  # default: task self-reports
```

## Testing Strategy
- tests/dsl/test_parser.py — test parsing judge = true/false at both levels
- Test default values when judge not specified
