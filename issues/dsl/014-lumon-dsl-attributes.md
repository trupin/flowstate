# [DSL-014] Add lumon and lumon_config settings to flow and node declarations

## Domain
dsl

## Status
in_progress

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: ENGINE-061, ENGINE-062, SERVER-021, UI-065

## Spec References
- specs.md Section 3.2 — "Flow Declaration" (lumon and lumon_config flow attributes)
- specs.md Section 3.4 — "Node Declarations" (per-node lumon overrides)
- specs.md Section 9.9 — "Lumon Security Layer"
- specs.md Section 11.1 — "AST" (Flow and Node dataclass fields)

## Summary
Add `lumon` (boolean, default false) and `lumon_config` (optional string path to `.lumon.json`) as DSL attributes at both flow-level and node-level. This follows the exact same override pattern used by `sandbox`/`sandbox_policy`: flow-level default, node-level override with `None` meaning "inherit from flow". Includes a type checker rule LM1 that rejects `lumon_config` when `lumon` is not enabled (mirrors SB1 for sandbox).

## Acceptance Criteria
- [ ] `lumon = true | false` parses at flow level (default: false)
- [ ] `lumon_config = "<path>"` parses at flow level (default: None)
- [ ] `lumon = true | false` parses at node level (entry, task, exit, atomic) — default: None (inherit)
- [ ] `lumon_config = "<path>"` parses at node level — default: None (inherit)
- [ ] AST `Flow` dataclass has `lumon: bool = False` and `lumon_config: str | None = None`
- [ ] AST `Node` dataclass has `lumon: bool | None = None` and `lumon_config: str | None = None`
- [ ] Type checker rule LM1: error when `lumon_config` is set but `lumon` is not true (flow and node level)
- [ ] All existing tests still pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/ast.py` — add fields to Flow and Node dataclasses
- `src/flowstate/dsl/grammar.lark` — add grammar rules for lumon attributes
- `src/flowstate/dsl/parser.py` — add transformer methods and update node/flow builders
- `src/flowstate/dsl/type_checker.py` — add LM1 validation rule
- `tests/dsl/fixtures/valid_lumon.flow` — new fixture file
- `tests/dsl/test_parser.py` — parser tests for lumon attributes
- `tests/dsl/test_type_checker.py` — type checker tests for LM1

### Key Implementation Details

**AST (`ast.py`):**
Add to `Flow` (after `sandbox_policy`):
```python
lumon: bool = False
lumon_config: str | None = None
```
Add to `Node` (after `sandbox_policy`):
```python
lumon: bool | None = None
lumon_config: str | None = None
```

**Grammar (`grammar.lark`):**
Add flow-level rules (after `flow_sandbox_policy`):
```
| "lumon" "=" BOOL_LIT -> flow_lumon
| "lumon_config" "=" STRING -> flow_lumon_config
```
Add node-level rules (after existing sandbox rules in `node_attr`):
```
| "lumon" "=" BOOL_LIT
| "lumon_config" "=" STRING
```

**Parser (`parser.py`):**
Add transformer methods following the exact patterns of `flow_sandbox`/`flow_sandbox_policy`:
- `flow_lumon(self, items)` → `("lumon", str(items[0]) == "true")`
- `flow_lumon_config(self, items)` → `("lumon_config", _strip_string(items[0]))`

For node attributes, handle in the same way as `sandbox`/`sandbox_policy` — extract from the attrs dict in each node builder.

Update `flow_decl` to extract and pass both fields to `Flow()`.
Update all node builders (`entry_node`, `task_node`, `exit_node`, `atomic_node`) to extract and pass both fields to `Node()`.

**Type Checker (`type_checker.py`):**
Add rule LM1 — mirrors SB1 exactly, check at both flow level and per-node:
- If `flow.lumon_config is not None and not flow.lumon`: error "lumon_config requires lumon = true at flow level"
- For each node: if `node.lumon_config is not None and node.lumon is not None and not node.lumon`: error
- Also check: if `node.lumon_config is not None and node.lumon is None and not flow.lumon`: error (inherited lumon is false)

### Edge Cases
- `lumon_config` set on a node that inherits `lumon = true` from flow → valid (no error)
- `lumon_config` set on a node with explicit `lumon = false` → LM1 error
- `lumon = false` explicitly at flow level with no `lumon_config` → valid (no-op)
- `lumon_config` set on a node, `lumon` not set on node, flow has `lumon = false` → LM1 error
- Both `sandbox = true` and `lumon = true` on same flow/node → valid (layered security)

## Testing Strategy
- Parser tests: verify all combinations of lumon/lumon_config at flow and node levels
- Type checker tests: verify LM1 rule triggers and passes correctly
- Regression: run full test suite to ensure no existing tests break

## E2E Verification Plan

### Verification Steps
1. Create a test `.flow` file with `lumon = true` and `lumon_config = "security/strict.lumon.json"`
2. Run `/check` on the file — should parse and type-check successfully
3. Create a `.flow` file with `lumon_config = "security/strict.lumon.json"` but no `lumon = true`
4. Run `/check` — should report LM1 error

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in: server restarted, exact commands, observed output, confirmation fix/feature works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
