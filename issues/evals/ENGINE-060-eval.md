# Evaluation: ENGINE-060

**Date**: 2026-03-28
**Sprint**: N/A (Phase 23c, no sprint contract for this issue)
**Verdict**: PARTIAL

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | Section is present and filled in |
| Commands are specific and concrete | PASS | Lists exact pytest commands with pass counts (28, 15, 588, 314) |
| Scenarios cover acceptance criteria | PARTIAL | Criteria 1-5 covered by unit tests; criterion 6 (ACP handshake completes in real sandbox) has no E2E evidence |
| Server restarted after changes | FAIL | No evidence of server restart or real server testing at all -- all evidence is pytest-based |
| Reproduction logged before fix (bugs) | N/A | Not a bug fix |

**Proof-of-work gap**: The verification log contains ONLY pytest results. There are no `curl` commands, no real server interactions, no evidence of starting the actual flowstate server and testing sandbox behavior through the API. The SDLC requires E2E verification against the real running server, not just unit tests. The implementing agent should have started the server, created a sandbox-enabled flow, attempted to start a run via `POST /api/flows/:id/runs`, and documented the results.

## Criteria Results

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | `wrap_command()` includes `--from claude` | PASS | Unit test `test_from_claude_flag` passes; 28/28 sandbox tests pass |
| 2 | `wrap_command()` includes `--auto-providers` | PASS | Unit test `test_auto_providers_flag` passes |
| 3 | `wrap_command()` includes `--no-tty` | PASS | Unit test `test_no_tty_flag` passes |
| 4 | Pre-flight check verifies gateway reachable (not just binary) | PASS | Unit tests `test_gateway_unreachable_returns_400`, `test_gateway_timeout_returns_400`, `test_gateway_os_error_returns_400` all pass; verified E2E: with real gateway running, `POST /api/flows/sandbox_test/runs` returns 202 (gateway check passed) |
| 5 | Helpful error when gateway unreachable | PASS | Unit test `test_gateway_unreachable_includes_stderr` passes; error message suggests `openshell gateway start` |
| 6 | Sandboxed flow run starts and ACP handshake completes | FAIL | E2E test showed ACP initialize timed out after 30s with JSON-RPC parse errors. The sandbox wrapping is applied (run proceeds through openshell), but the ACP handshake does NOT complete successfully. |

## Independent E2E Verification

I performed my own E2E testing:

1. Started server: `uv run flowstate serve --port 9090`
2. Created a sandbox-enabled flow (`sandbox_test.flow` with `sandbox = true`)
3. Verified flow was parsed correctly: `GET /api/flows` showed `sandbox_test` as valid with `ast_json.sandbox = true`
4. Started a run: `POST /api/flows/sandbox_test/runs` returned `202` with `flow_run_id`
5. Observed the run: task `start` entered `running` status, then `failed` after 30s
6. Task logs showed: `"ACP initialize timed out after 30.0s"` with multiple JSON-RPC parse errors
7. Run paused per `on_error = pause` policy
8. `openshell sandbox list` showed the sandbox was cleaned up (consistent with `--no-keep`)
9. All 28 engine sandbox unit tests pass
10. All 15 server preflight unit tests pass
11. 588/588 engine tests pass (no regressions)
12. 314/318 server tests pass (4 pre-existing port config failures unrelated to this change)
13. Pyright: 0 errors
14. Ruff: 12 pre-existing unused noqa warnings (not in ENGINE-060 files)

## Failures

### FAIL-1: ACP handshake does not complete in sandbox
**Criterion**: #6 -- "A sandboxed flow run successfully starts and the ACP handshake completes"
**Expected**: The sandbox-wrapped command should start the ACP agent inside the openshell container, and the ACP initialize handshake should succeed, allowing the task to execute.
**Observed**: The ACP initialize times out after 30 seconds. Server logs show repeated `json.decoder.JSONDecodeError` from the ACP connection's `_receive_loop`, indicating the subprocess is producing non-JSON output on stdout before/instead of the expected JSON-RPC messages. The task fails with exit code 1.
**Steps to reproduce**:
1. Ensure openshell gateway is running (`openshell gateway start`)
2. Start server: `uv run flowstate serve --port 9090`
3. Create a flow file with `sandbox = true` (e.g., `flows/sandbox_test.flow`)
4. `curl -X POST http://localhost:9090/api/flows/sandbox_test/runs -H "Content-Type: application/json" -d '{"params": {"message": "hello"}}'`
5. Wait 30 seconds
6. `curl http://localhost:9090/api/runs/<run_id>` -- task status is "failed"
7. Check task logs: "ACP initialize timed out after 30.0s"

**Note**: This may be an infrastructure/environment issue (e.g., the Claude community image needs to be pulled first, which takes 2-5 minutes, exceeding the 30s ACP init timeout). The issue's "Edge Cases" section acknowledges this: "Claude community image pull is slow (~2-5 min first time) -- openshell handles this, ACP init timeout (30s) may need increasing for first sandbox creation." However, the acceptance criterion states the handshake should complete, and it does not.

### FAIL-2: E2E proof-of-work is unit-test only
**Criterion**: SDLC requirement -- E2E verification must test against real running server
**Expected**: The E2E Verification Log should contain evidence of real server testing: server start commands, curl requests to localhost, observed API responses.
**Observed**: The verification log contains only pytest results. No real server was started, no HTTP requests were made, no real sandbox execution was attempted.
**Impact**: The implementing agent's own proof-of-work is insufficient per project SDLC standards. All verification was via mocked unit tests, which cannot catch issues like the ACP handshake timeout observed in real E2E testing.

## Summary

5 of 6 acceptance criteria passed. Criteria 1-5 (wrap_command flags and pre-flight gateway check) are solidly implemented and verified through both unit tests and independent E2E testing. Criterion 6 (ACP handshake completes) fails -- the sandbox wrapping is correctly applied but the actual ACP communication inside the sandbox does not succeed. The E2E proof-of-work is also inadequate, containing only unit test evidence with no real server testing.

The implementation of the core wrap_command changes and pre-flight gateway check is correct. The remaining issue is that sandbox execution does not actually work end-to-end (ACP handshake timeout), though this may be related to environment setup (image pull time) rather than code bugs.
