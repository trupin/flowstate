# Evaluation: ENGINE-063

**Date**: 2026-03-28
**Sprint**: N/A (standalone issue, Phase 23c)
**Verdict**: FAIL

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | FAIL | Log section contains only placeholder text: `_[Agent fills this in]_` |
| Commands are specific and concrete | FAIL | No commands present at all |
| Scenarios cover acceptance criteria | FAIL | Zero coverage |
| Server restarted after changes | FAIL | No evidence of any testing |
| Reproduction logged before fix (bugs) | FAIL | This is a bug fix. No reproduction log exists showing the ACP timeout before the fix was applied |

**Automatic FAIL**: The E2E Verification Log is completely empty (placeholder text only). The implementing agent produced zero proof-of-work. Per evaluation protocol, this is an automatic FAIL without needing to test further. However, independent testing was performed below and reveals the bug still persists.

## Criteria Results

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | `SandboxManager.create()` async method pre-creates sandbox and waits for ready | FAIL | Unit tests pass (32/32) but no E2E evidence. See behavioral test below. |
| 2 | `SandboxManager.wrap_command()` uses `openshell sandbox connect` | FAIL | Unit tests verify `connect` format but real behavior untested E2E |
| 3 | Executor calls `create()` before `harness.run_task()`, wraps with `connect` | FAIL | Unit tests pass but no E2E verification |
| 4 | Provisioning output does not reach ACP's stdout parser | FAIL | **BUG STILL PRESENT**: Server logs show JSON-RPC parse errors and 30s ACP init timeout when running a sandbox=true flow |
| 5 | ACP init timeout no longer expires during sandbox provisioning | FAIL | ACP init timeout still fires: "ACP initialize timed out after 30.0s" in server logs |
| 6 | Sandbox cleanup via `destroy()` still works | FAIL | No E2E evidence |
| 7 | Pre-flight check still validates gateway reachability | PASS | The flow run started (HTTP 202) with openshell on PATH, confirming pre-flight check allows it through |
| 8 | All existing sandbox tests updated and passing | PASS | 32/32 sandbox tests pass, 217/217 executor tests pass, ruff and pyright clean |

## Failures

### FAIL-1: E2E Verification Log is empty
**Criterion**: All acceptance criteria (E2E verification required per SDLC)
**Expected**: The E2E Verification Log section should contain concrete evidence of testing against the real running server -- exact commands, server logs, observed behavior before and after the fix.
**Observed**: The section contains only the placeholder text `_[Agent fills this in]_`. No reproduction of the original bug. No post-fix verification. Zero proof-of-work.
**Steps to reproduce**:
1. Open `issues/engine/063-sandbox-precreate-connect-pattern.md`
2. Scroll to "E2E Verification Log" section
3. Observe it contains only the unfilled placeholder

### FAIL-2: Bug still present -- ACP timeout occurs with sandbox=true flows
**Criterion**: Criteria 4 and 5 -- provisioning output must not reach ACP parser, ACP init timeout must not expire during provisioning
**Expected**: When running a sandbox=true flow, the pre-create step should complete separately, and the connect step should provide clean stdio for ACP JSON-RPC communication.
**Observed**: Running a sandbox=true flow produces immediate JSON-RPC parse errors in the ACP connection receive loop, followed by the 30-second ACP init timeout expiring. The flow run enters "paused" state with error "Task failed (on_error=pause): Task exited with code 1".
**Steps to reproduce**:
1. Start server: `uv run flowstate server` (the discuss_flowstate.flow has `sandbox = true`)
2. Ensure openshell is installed and gateway is running: `openshell gateway start`
3. Start a sandboxed flow run:
   ```
   curl -s -X POST http://localhost:9090/api/flows/discuss_flowstate/runs \
     -H "Content-Type: application/json" \
     -d '{"params": {"topic": "test topic"}}'
   ```
4. Wait 30 seconds
5. Check server logs -- observe JSON-RPC parse errors from `acp/connection.py` line 155
6. Observe: `ACP initialize timed out after 30.0s -- subprocess may not support ACP protocol`
7. Check run status -- it is "paused" with error about task failure

Server log excerpt showing the bug:
```
2026-03-28 09:19:19,115 ERROR root: Error parsing JSON-RPC message
json.decoder.JSONDecodeError: Expecting value: line 2 column 1 (char 1)
[... multiple JSON-RPC parse errors ...]
2026-03-28 09:19:51,202 ERROR flowstate.engine.acp_client: ACP agent error: ACP initialize timed out after 30.0s
```

### FAIL-3: No reproduction of original bug before fix
**Criterion**: SDLC requires reproduction before fix for bug issues
**Expected**: The issue's E2E Verification Log should have a "Reproduction" section showing the exact ACP timeout behavior observed before any code changes were made.
**Observed**: No reproduction section exists. The agent may have fixed the wrong thing since the bug was never confirmed against the running system before the fix.

### FAIL-4: Issue status is in_progress, not complete
**Criterion**: Issue completeness
**Expected**: A completed issue should have status "done" with all checklist items checked.
**Observed**: Issue status is "in_progress". The completion checklist has all items unchecked. The E2E verification log is unfilled.

## Summary
2 of 8 criteria passed. The implementation has passing unit tests (all 32 sandbox tests and 217 executor tests pass, lint and type checks clean), indicating the code changes are structurally sound. However, the E2E Verification Log is completely empty, the issue is still marked in_progress, and independent behavioral testing reveals the core bug (ACP JSON-RPC timeout on sandbox=true flows) still occurs when tested against the real running server. The domain agent must:

1. Reproduce the original bug against the real running server (with openshell + Docker available)
2. Verify the fix resolves the ACP timeout
3. Fill in the E2E Verification Log with concrete evidence
4. Mark the issue as done only after E2E verification passes
