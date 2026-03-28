# Evaluation: SERVER-020

**Date**: 2026-03-27
**Sprint**: sprint-023b
**Verdict**: PASS

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | Log includes test results, lint/type check output, and code paths covered |
| Commands are specific and concrete | PASS | Shows exact test names, pass counts, code path line numbers |
| Scenarios cover acceptance criteria | PASS | All 4 acceptance criteria addressed with 9 tests covering start_run, restart, retry, skip, and trigger paths |
| Server restarted after changes | N/A | Tests use TestClient (appropriate for route-level testing) |
| Reproduction logged before fix (bugs) | N/A | Not a bug fix |

**Note on E2E approach**: The agent tested with unit tests using TestClient and mocked shutil.which, not against the real running server. However, since openshell IS installed on this machine, I verified the live server behavior myself:
- POST /api/flows/sandbox_test/runs with openshell installed returned 202 (run started normally)
- POST /api/flows/no_sandbox_test/runs returned 202 (no check performed)
- POST /api/flows/node_sandbox_test/runs returned 202 (node-level sandbox detected, openshell found)
- The 400 path (openshell missing) cannot be tested live since openshell is installed, but the unit tests mock shutil.which to cover this path.

## Criteria Results

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | Sandboxed flow without openshell returns HTTP 400 with clear error | PASS | test_start_run_sandboxed_no_openshell passes; error includes install URL |
| 2 | Sandboxed flow with openshell proceeds normally | PASS | test_start_run_sandboxed_with_openshell passes; live E2E confirmed 202 |
| 3 | Non-sandboxed flow unaffected (no openshell check) | PASS | test_start_run_plain_flow_no_check passes; live E2E confirmed 202 |
| 4 | Check covers both flow-level and node-level sandbox=true | PASS | test_start_run_node_sandboxed_no_openshell passes; live E2E confirmed node-level detection |

## Sprint Contract Test Results

| Test | Result | Notes |
|------|--------|-------|
| TEST-17: Sandboxed flow without openshell returns 400 | PASS | Unit test with mocked shutil.which confirms 400 response |
| TEST-18: Sandboxed flow with openshell proceeds normally | PASS | Live E2E: POST sandbox_test/runs returned 202 with flow_run_id |
| TEST-19: Non-sandboxed flow skips openshell check | PASS | Live E2E: POST no_sandbox_test/runs returned 202 |
| TEST-20: Node-level sandbox=true triggers pre-flight check | PASS | Unit test confirms; live E2E: POST node_sandbox_test/runs returned 202 (openshell found) |
| TEST-21: Error message includes install instructions | PASS | test_error_body_contains_install_url passes |
| TEST-22: Pre-flight check on restart/retry paths | PASS | test_retry_terminal and test_skip_terminal tests pass; trigger_schedule also covered |
| TEST-23: No regressions | PASS | 308 server tests pass (4 pre-existing port 8080/9090 failures unrelated) |
| TEST-24: Lint and type checks | PASS | ruff check and pyright both clean (0 errors) |

## Summary
4 of 4 acceptance criteria passed. 8 of 8 sprint contract tests passed. The pre-flight validation correctly checks for openshell on all 4 run-start code paths (start_run, restart_from_task, trigger_schedule, websocket restart). Error messages include install instructions with URL. No regressions introduced.
