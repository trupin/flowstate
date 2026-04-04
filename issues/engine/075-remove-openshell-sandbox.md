# [ENGINE-075] Remove SandboxManager and all OpenShell sandbox logic from engine

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: SERVER-024

## Spec References
- specs.md Section 9.7 — to be updated (remove OpenShell references)

## Summary
Remove all OpenShell sandbox infrastructure from the engine. OpenShell is unreliable (auth expires, network policies fragile, TTY corrupts ACP JSON-RPC). The `sandbox` DSL attribute is preserved for future Lumon-based sandboxing, but all OpenShell execution code is deleted.

## Acceptance Criteria
- [ ] `src/flowstate/engine/sandbox.py` deleted
- [ ] `src/flowstate/engine/sandbox/` directory deleted (connect-wrapper.sh, Dockerfile, policy.yaml)
- [ ] `SandboxManager` import removed from executor.py
- [ ] `sandbox_name` parameter removed from `FlowExecutor.__init__`
- [ ] `self._sandbox_mgr` removed from executor
- [ ] `_resolve_server_url()` method removed — artifact env vars always use `self._server_base_url`
- [ ] `_make_sandbox_judge()` method removed — judge always uses `self._judge`
- [ ] All `use_sandbox` conditionals removed from executor (3 locations: task execution, judge selection, worktree logic)
- [ ] The `if use_sandbox:` block in `_execute_single_task` that wraps harness with connect-wrapper removed
- [ ] Worktree logic simplified: remove `and not use_sandbox` guards
- [ ] `tests/engine/test_sandbox.py` deleted
- [ ] `tests/e2e/test_sandbox.py` deleted
- [ ] Sandbox test classes deleted from `tests/engine/test_executor.py`: TestSandboxEnabled, TestSandboxDisabled, TestSandboxNodeOverride, TestSandboxNoPertaskLifecycle, TestSandboxNewHarnessInstance, TestSandboxJudgeIntegration
- [ ] All remaining engine tests pass
- [ ] Lint and type checks pass

## Technical Design

### Files to Delete
- `src/flowstate/engine/sandbox.py`
- `src/flowstate/engine/sandbox/connect-wrapper.sh`
- `src/flowstate/engine/sandbox/Dockerfile`
- `src/flowstate/engine/sandbox/policy.yaml`
- `tests/engine/test_sandbox.py`
- `tests/e2e/test_sandbox.py`

### Files to Modify

**`src/flowstate/engine/executor.py`:**

1. Remove import: `from flowstate.engine.sandbox import SandboxManager`
2. Remove `sandbox_name` parameter from `__init__`
3. Remove `self._sandbox_mgr = SandboxManager(sandbox_name=sandbox_name)`
4. Delete `_resolve_server_url()` method entirely
5. Delete `_make_sandbox_judge()` method entirely
6. In `_acquire_routing_decision()`: replace `judge = self._make_sandbox_judge(...) if use_sandbox else self._judge` with just `self._judge.evaluate(judge_context)`
7. In `_execute_single_task()`: remove the entire `if use_sandbox:` block that creates a sandbox-wrapped AcpHarness. The artifact env vars (`FLOWSTATE_SERVER_URL` etc.) should still be set for ALL tasks (not just sandbox).
8. In worktree creation: remove `and not use_sandbox` guards — worktrees always apply when enabled.
9. Simplify artifact env: `_resolve_server_url` is gone, always use `self._server_base_url`

**`tests/engine/test_executor.py`:**

Delete these test classes entirely:
- TestSandboxEnabled
- TestSandboxDisabled
- TestSandboxNodeOverride
- TestSandboxNoPertaskLifecycle
- TestSandboxNewHarnessInstance
- TestSandboxJudgeIntegration

### Edge Cases
- `session_timeout` parameter on AcpHarness: keep it (useful for slow connections, not sandbox-specific)
- `init_timeout` parameter: keep it (same reason)
- Artifact env vars: keep the injection for ALL tasks (not just sandbox) — this is the artifact API protocol

## Testing Strategy
- `uv run pytest tests/engine/ -q` — all pass
- `uv run ruff check src/flowstate/engine/`
- `uv run pyright src/flowstate/engine/`

## E2E Verification Plan

### Verification Steps
1. Run full engine test suite
2. Verify no references to SandboxManager remain in engine code
3. Verify sandbox directory is gone

## E2E Verification Log
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
