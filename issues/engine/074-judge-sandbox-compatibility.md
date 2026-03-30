# [ENGINE-074] Judge subprocess fails for sandboxed flows

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-072
- Blocks: —

## Spec References
- specs.md Section 7 — "Judge Protocol"
- specs.md Section 2.5 — "Routing (Judge vs Self-Report)"

## Summary
When a flow has `sandbox=true` and `judge=true`, the judge subprocess fails silently, causing the flow to pause. The judge uses the original (non-sandbox-wrapped) AcpHarness to spawn `claude-agent-acp` on the host. Two problems:

1. **Auth mismatch**: The sandbox agent authenticates via API key inside the container, but the host-side `claude-agent-acp` used by the judge may have different (or no) authentication. The judge gets "Authentication required" or fails to initialize.

2. **Workspace mismatch**: The judge receives `task_cwd` pointing to the host workspace, but for sandboxed flows the agent's actual work happened inside the container at `/sandbox`. The judge can't inspect the agent's output in the workspace because nothing was written there.

The E2E test `TestSandboxConditional` reproduces this: the `analyze` task completes and submits its `summary` artifact, but the judge subprocess fails, causing the flow to pause. The summary is available in the DB, so the judge prompt is correctly constructed — the failure is in the judge subprocess execution itself.

## Acceptance Criteria
- [ ] Judge subprocess works when the source task ran in a sandbox
- [ ] Judge reads task summary from DB artifact (already done in ENGINE-068)
- [ ] Judge subprocess uses the host's authentication (not sandbox auth)
- [ ] `TestSandboxConditional` E2E test passes
- [ ] Non-sandboxed judge flows continue to work

## Technical Design

### Root Cause Analysis

The `JudgeProtocol` at `src/flowstate/engine/judge.py:160` is initialized with the default harness:
```python
self._judge = judge or JudgeProtocol(harness)
```

This harness is `AcpHarness(command=["claude-agent-acp"])` — it spawns a host-side process. When the judge calls `run_judge(prompt, task_cwd)`, the ACP session runs on the host. For this to work:
- The host must have `claude-agent-acp` authenticated
- The `task_cwd` must be a valid host path

For sandboxed flows, `task_cwd` points to `~/.flowstate/workspaces/<flow>/<run>/` which exists on the host (auto-created) but may be empty. This is actually fine — the judge doesn't need to read files from the workspace anymore (summary comes from DB artifact).

The real issue is likely **host-side auth**. The `claude-agent-acp` on the host needs to be authenticated independently of the sandbox.

### Possible Fixes

**Option A: Ensure host-side `claude-agent-acp` is authenticated**
- Document that `claude login` must be run on the host too, not just in the sandbox
- Add a pre-flight check for host auth when judge=true + sandbox=true
- Minimal code change

**Option B: Run the judge through the sandbox too**
- When sandbox=true, wrap the judge's harness command through the connect-wrapper
- The judge would run inside the sandbox with sandbox auth
- More complex but ensures auth consistency

**Option C: Pass auth credentials to the judge harness**
- Forward `ANTHROPIC_API_KEY` env var to the judge's ACP subprocess
- Works if the API key is available in the host environment

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — either wrap judge harness for sandbox, or forward auth env
- `tests/engine/test_executor.py` — add test for judge + sandbox

### Recommended approach

**Option B**: Run the judge through the sandbox when the source task was sandboxed. The judge is evaluating work done inside the sandbox — it should run there too, with the same auth and environment. In `_acquire_routing_decision()`, when `use_sandbox=true`, wrap the judge harness the same way the task harness is wrapped (via `SandboxManager.wrap_command()` + `AcpHarness` with sandbox env).

## Testing Strategy
- E2E: `uv run pytest tests/e2e/test_sandbox.py::TestSandboxConditional -v`

## E2E Verification Plan

### Verification Steps
1. Ensure `claude-agent-acp` is authenticated on the host (`claude login`)
2. Start server: `uv run flowstate server --host 0.0.0.0`
3. Run: `uv run pytest tests/e2e/test_sandbox.py::TestSandboxConditional -v`
4. Verify flow completes (judge routes correctly)

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in
