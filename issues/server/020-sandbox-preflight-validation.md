# [SERVER-020] Validate openshell availability at run start

## Domain
server

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: DSL-008
- Blocks: —

## Spec References
- specs.md Section 3.3 — "Flow Declaration" (sandbox requires openshell installed)

## Summary
Add a pre-flight check when starting a flow run: if the flow or any of its nodes has `sandbox = true`, verify that `openshell` is on PATH before starting execution. Return a clear 400 error if it's missing, rather than letting the executor fail later with a cryptic subprocess error.

## Acceptance Criteria
- [ ] Starting a sandboxed flow without openshell installed returns HTTP 400 with clear error message
- [ ] Starting a sandboxed flow with openshell installed proceeds normally
- [ ] Starting a non-sandboxed flow is unaffected (no openshell check)
- [ ] Check covers both flow-level and node-level `sandbox = true`

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/routes.py` — add pre-flight check in `start_run()`
- `tests/server/test_routes.py` — test both paths

### Key Implementation Details

**`routes.py` — in `start_run()` after parsing the flow AST:**
```python
# Pre-flight: verify openshell is available if sandbox is enabled
needs_sandbox = flow_ast.sandbox or any(
    n.sandbox for n in flow_ast.nodes.values() if n.sandbox is not None
)
if needs_sandbox:
    import shutil
    if not shutil.which("openshell"):
        raise FlowstateError(
            "Flow requires sandbox but 'openshell' is not installed or not on PATH. "
            "Install it: curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh",
            status_code=400,
        )
```

Add the same check to `_create_restart_executor()` and the queue task execution path if they exist.

### Edge Cases
- Flow has `sandbox = false` but a node has `sandbox = true` → check triggers (correct)
- All nodes have `sandbox = false` overriding flow's `sandbox = true` → check triggers because flow-level is true (conservative, acceptable)
- openshell is installed but Docker not running → this check passes, error comes later from openshell itself

## Testing Strategy
- Mock `shutil.which` to return `None` → verify 400 response with helpful message
- Mock `shutil.which` to return a path → verify run starts normally
- Test with non-sandboxed flow → verify no check performed

## E2E Verification Plan

### Verification Steps
1. Start server: `uv run flowstate serve`
2. Create a flow with `sandbox = true`
3. If openshell not installed: `curl -X POST .../runs` → expect 400 with install instructions
4. If openshell installed: `curl -X POST .../runs` → expect 202 (run starts)

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
