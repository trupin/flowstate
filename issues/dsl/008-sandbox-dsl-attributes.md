# [DSL-008] Add sandbox and sandbox_policy to grammar, parser, AST, and type checker

## Domain
dsl

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: ENGINE-059, SERVER-020, UI-064

## Spec References
- specs.md Section 3.3 — "Flow Declaration" (sandbox and sandbox_policy flow attributes)
- specs.md Section 3.4 — "Node Declarations" (per-node sandbox overrides)
- specs.md Section 11.1 — "AST" (Flow and Node dataclass fields)

## Summary
Add `sandbox` (boolean, default false) and `sandbox_policy` (optional string path) as DSL attributes at both flow-level and node-level. This follows the exact same override pattern used by `judge`, `harness`, and `subtasks`: flow-level default, node-level override with `None` meaning "inherit from flow". Includes a type checker rule SB1 that rejects `sandbox_policy` when `sandbox` is not enabled.

## Acceptance Criteria
- [x] `sandbox = true | false` parses at flow level (default: false)
- [x] `sandbox_policy = "<path>"` parses at flow level (default: None)
- [x] `sandbox = true | false` parses at node level (entry, task, exit, atomic) — default: None (inherit)
- [x] `sandbox_policy = "<path>"` parses at node level — default: None (inherit)
- [x] AST `Flow` dataclass has `sandbox: bool = False` and `sandbox_policy: str | None = None`
- [x] AST `Node` dataclass has `sandbox: bool | None = None` and `sandbox_policy: str | None = None`
- [x] Type checker rule SB1: error when `sandbox_policy` is set but `sandbox` is not true (flow and node level)
- [x] All existing tests still pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/ast.py` — add fields to Flow and Node dataclasses
- `src/flowstate/dsl/grammar.lark` — add grammar rules for sandbox attributes
- `src/flowstate/dsl/parser.py` — add transformer methods and update node/flow builders
- `src/flowstate/dsl/type_checker.py` — add SB1 validation rule
- `tests/dsl/fixtures/valid_sandbox.flow` — new fixture file
- `tests/dsl/test_parser.py` — parser tests for sandbox attributes
- `tests/dsl/test_type_checker.py` — type checker tests for SB1

### Key Implementation Details

**AST (`ast.py`):**
Add to `Flow` (after `subtasks`):
```python
sandbox: bool = False
sandbox_policy: str | None = None
```
Add to `Node` (after `subtasks`):
```python
sandbox: bool | None = None
sandbox_policy: str | None = None
```

**Grammar (`grammar.lark`):**
Add flow-level rules (after `flow_subtasks`):
```
| "sandbox" "=" BOOL_LIT -> flow_sandbox
| "sandbox_policy" "=" STRING -> flow_sandbox_policy
```
Add node-level rules (after `node_subtasks`):
```
| "sandbox" "=" BOOL_LIT -> node_sandbox
| "sandbox_policy" "=" STRING -> node_sandbox_policy
```

**Parser (`parser.py`):**
Add transformer methods following the exact patterns of `flow_judge`/`node_judge`:
- `flow_sandbox(self, items)` → `("sandbox", str(items[0]) == "true")`
- `flow_sandbox_policy(self, items)` → `("sandbox_policy", _strip_string(items[0]))`
- `node_sandbox(self, items)` → `("sandbox", str(items[0]) == "true")`
- `node_sandbox_policy(self, items)` → `("sandbox_policy", _strip_string(items[0]))`

Update `flow_decl` to extract and pass both fields to `Flow()`.
Update all node builders (`entry_node`, `task_node`, `exit_node`, `atomic_node`) to extract and pass both fields to `Node()`.

**Type Checker (`type_checker.py`):**
Add rule SB1 — check at both flow level and per-node:
- If `flow.sandbox_policy is not None and not flow.sandbox`: error "sandbox_policy requires sandbox = true"
- For each node: if `node.sandbox_policy is not None and node.sandbox is not None and not node.sandbox`: error
- Also check: if `node.sandbox_policy is not None and node.sandbox is None and not flow.sandbox`: error (inherited sandbox is false)

### Edge Cases
- `sandbox_policy` set on a node that inherits `sandbox = true` from flow → valid (no error)
- `sandbox_policy` set on a node with explicit `sandbox = false` → SB1 error
- `sandbox = false` explicitly at flow level with no `sandbox_policy` → valid (no-op)
- `sandbox_policy` set on a node, `sandbox` not set on node, flow has `sandbox = false` → SB1 error

## Testing Strategy
- Parser tests: verify all combinations of sandbox/sandbox_policy at flow and node levels
- Type checker tests: verify SB1 rule triggers and passes correctly
- Regression: run full test suite to ensure no existing tests break

## E2E Verification Plan

### Verification Steps
1. Create a test `.flow` file with `sandbox = true` and `sandbox_policy = "test.yaml"`
2. Run `/check` on the file — should parse and type-check successfully
3. Create a `.flow` file with `sandbox_policy = "test.yaml"` but no `sandbox = true`
4. Run `/check` — should report SB1 error

## E2E Verification Log

### Post-Implementation Verification

**Date**: 2026-03-27

#### Step 1: Parse and type-check valid_sandbox.flow fixture

Command:
```
uv run flowstate check tests/dsl/fixtures/valid_sandbox.flow
```

Output:
```
OK
```

Also verified programmatically that the parsed AST has correct field values:
```
flow.name = 'sandboxed_pipeline'
flow.sandbox = True
flow.sandbox_policy = 'policies/strict.yaml'

  node 'prepare': sandbox=True, sandbox_policy='node-policy.yaml'
  node 'build': sandbox=False, sandbox_policy=None
  node 'test_suite': sandbox=None, sandbox_policy=None
  node 'deploy': sandbox=True, sandbox_policy=None
```

Type check errors: 0 (PASS).

**Conclusion**: Flow-level and node-level sandbox/sandbox_policy attributes parse correctly, AST fields have expected values, and type checker accepts a valid flow with sandbox enabled.

#### Step 2: Verify SB1 error when sandbox_policy set without sandbox

Created `/tmp/sb1_error_test.flow` with `sandbox_policy = "policies/strict.yaml"` but no `sandbox = true` at flow level.

Command:
```
uv run flowstate check /tmp/sb1_error_test.flow
```

Output (exit code 1):
```
Type error: FlowTypeError(rule='SB1', message='sandbox_policy requires sandbox = true at flow level', location='sb1_test')
```

Also tested node-level SB1 with `/tmp/sb1_node_error_test.flow` (node has `sandbox = false` + `sandbox_policy = "node.yaml"`):

Command:
```
uv run flowstate check /tmp/sb1_node_error_test.flow
```

Output (exit code 1):
```
Type error: FlowTypeError(rule='SB1', message="Node 'start' sets sandbox_policy but sandbox is not enabled (sandbox must be true, either on the node or inherited from flow)", location='start')
```

**Conclusion**: SB1 correctly rejects sandbox_policy at both flow and node level when sandbox is not enabled.

#### Step 3: Full DSL test suite

Command:
```
uv run pytest tests/dsl/ -v
```

Output:
```
350 passed in 1.92s
```

All 350 tests pass, including 10 new SB1-specific tests in `TestSB1SandboxPolicyRequiresSandbox` and sandbox parser tests.

**Conclusion**: No regressions. All existing and new tests pass.

#### Step 4: Lint and type checks

Command:
```
uv run ruff check src/flowstate/dsl/ tests/dsl/
```

Output:
```
All checks passed!
```

Command:
```
uv run pyright src/flowstate/dsl/
```

Output:
```
0 errors, 0 warnings, 0 informations
```

**Conclusion**: All lint and type checks pass with zero issues.

## Completion Checklist
- [x] Unit tests written and passing
- [x] `/simplify` run on all changed code
- [x] `/lint` passes (ruff, pyright, eslint)
- [x] Acceptance criteria verified
- [x] E2E verification log filled in with concrete evidence
