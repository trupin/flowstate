# [E2E-015] Real sandbox E2E test suite (no mocking)

## Domain
e2e

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-068
- Blocks: —

## Spec References
- specs.md Section 9.6 — "API-Based Artifact Protocol"
- specs.md Section 3.3 — "Flow Declaration" (sandbox attribute)

## Summary
A comprehensive E2E test suite that runs real flows with `sandbox = true` against a live OpenShell sandbox with real Claude agents. No mocking. Tests verify that all flow patterns (linear, conditional, fork/join, context handoff, subtasks) work correctly when agents run inside the sandbox and communicate via the artifact API. The test file `tests/e2e/test_sandbox.py` already exists from the initial discovery phase and should be updated to verify the artifact API protocol.

## Acceptance Criteria
- [ ] Linear flow with sandbox=true completes (all tasks pass)
- [ ] Conditional flow with judge=true completes (judge reads summary from DB)
- [ ] Conditional flow with self-report completes (agent POSTs decision via API)
- [ ] Fork/join flow with sandbox=true completes (parallel branches in sandbox)
- [ ] Context handoff works (successor reads predecessor summary from DB)
- [ ] Subtask creation works inside sandbox
- [ ] Pre-flight check passes for valid sandbox, fails for invalid
- [ ] Error recovery (on_error=pause) works in sandbox
- [ ] Tests skip cleanly when sandbox is not available
- [ ] All tests use real server, real sandbox, real Claude agents — zero mocking

## Technical Design

### Files to Create/Modify
- `tests/e2e/test_sandbox.py` — update existing test file

### Key Implementation Details

The test file already exists with 10 tests (8 passing, 2 failing). After ENGINE-067 and ENGINE-068 are implemented:

1. **Update TestSandboxConditional**: Should now pass — judge reads summary from DB, no connect-wrapper corruption
2. **Update TestSandboxDecisionDownload**: Should now pass — agent POSTs decision to API, engine reads from DB
3. **Add artifact verification**: After runs complete, verify artifacts exist in DB by calling `GET /api/runs/{id}/tasks/{tid}/artifacts`
4. **Add TestSandboxNodeOverride**: Test that `sandbox=false` at flow level with `sandbox=true` on a specific node works correctly

### Edge Cases
- Sandbox not available: all tests skip with clear message
- Sandbox in bad state: test should not hang indefinitely (use TASK_TIMEOUT)
- Agent fails to POST artifact: flow should pause (not hang)

## Testing Strategy
This IS the test. Run with:
```bash
uv run pytest tests/e2e/test_sandbox.py -v
```

## E2E Verification Plan

### Verification Steps
1. Ensure sandbox is running: `openshell sandbox get flowstate-claude`
2. Run: `uv run pytest tests/e2e/test_sandbox.py -v`
3. All 10+ tests should pass
4. Verify no tests required more than 3 minutes each

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
