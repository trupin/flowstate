# [ENGINE-062] Adapt task output reading for lumon sandbox directory

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-061
- Blocks: —

## Spec References
- specs.md Section 9.9 — "Lumon Security Layer" (task output directory)

## Summary
When lumon is active, the agent operates inside `<task-dir>/sandbox/`. The engine currently reads `DECISION.json`, `SUMMARY.md`, and `OUTPUT.json` from the task directory root. This issue adapts the output reading logic to check `<task-dir>/sandbox/` first when lumon is enabled, falling back to `<task-dir>/` for backwards compatibility.

## Acceptance Criteria
- [ ] When lumon is active, DECISION.json is read from `<task-dir>/sandbox/` (self-report routing)
- [ ] When lumon is active, SUMMARY.md is read from `<task-dir>/sandbox/` (context handoff)
- [ ] When lumon is active, OUTPUT.json is read from `<task-dir>/sandbox/` (flow output)
- [ ] Falls back to `<task-dir>/` if files not found in sandbox/ (graceful degradation)
- [ ] Non-lumon tasks are completely unaffected
- [ ] All existing tests still pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — update DECISION.json, SUMMARY.md, OUTPUT.json read paths
- `tests/engine/test_lumon_output_paths.py` — unit tests

### Key Implementation Details

Add a helper to resolve the output file path based on whether lumon is active:

```python
def _task_output_path(task_dir: Path, filename: str, lumon_active: bool) -> Path:
    if lumon_active:
        sandbox_path = task_dir / "sandbox" / filename
        if sandbox_path.exists():
            return sandbox_path
    return task_dir / filename
```

Apply this in every location that reads DECISION.json, SUMMARY.md, or OUTPUT.json from the task directory. The `lumon_active` flag comes from the task context set by ENGINE-061.

Locations to update (search for these filenames in executor.py):
1. `DECISION.json` reading in the self-report routing logic
2. `SUMMARY.md` reading in the context assembly logic
3. `OUTPUT.json` reading in the flow output collection logic

### Edge Cases
- Agent writes DECISION.json to both sandbox/ and task root → sandbox/ takes priority
- Agent doesn't write to sandbox/ at all (lumon misconfiguration) → falls back to task root
- sandbox/ directory doesn't exist → falls back to task root (no error)

## Testing Strategy
- Unit test: mock filesystem, verify sandbox/ path is preferred when lumon_active=True
- Unit test: verify fallback to task root when sandbox/ file doesn't exist
- Unit test: verify non-lumon tasks use task root directly (no sandbox/ check)

## E2E Verification Plan

### Verification Steps
1. Create a lumon-enabled flow with conditional routing
2. Run it and verify the agent writes DECISION.json to sandbox/
3. Verify the engine reads it correctly and routes to the right node
4. Verify SUMMARY.md from sandbox/ is used in context handoff

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in: server restarted, exact commands, observed output, confirmation fix/feature works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
