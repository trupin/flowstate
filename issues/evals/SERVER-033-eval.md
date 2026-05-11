# Evaluation: SERVER-033

**Date**: 2026-05-10
**Sprint**: sprint-037 (Phase 37b — API parity follow-up to SHARED-012)
**Verdict**: PASS

## Summary

All 5 acceptance criteria PASS. The previously-observed SHARED-012 regression
(`GET /api/flows` returning `lumon=True, sandbox=False` for `discuss_flowstate`)
is fully fixed: both `lumon` and `sandbox` now correctly alias onto the same
underlying `LumonConfig.enabled` value, and the policy path is surfaced on the
detail endpoint through both `lumon_config` and `sandbox_policy` aliases. The
new regression tests (`test_flow_explicit_disabled_with_config_path`,
`test_flow_block_syntax_plugins_no_config_path`) exercise the exact `bool(dict)`
trap that hid the bug from the pre-SHARED-012 test suite, so any future
re-introduction of the flat-key read pattern will fail in CI.

The implementing agent's E2E Verification Log is concrete, specific, and matches
what I observe against the running server byte-for-byte. This effectively closes
the API-parity concern flagged during SHARED-012's evaluation; Phase 37b's
API/UI surface is now correct.

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | Sections 1-5 filled in, no placeholders. |
| Commands are specific and concrete | PASS | Exact `curl` invocations, exact `python3` parsers, exact `pytest -v` runs, exact PIDs (`PID 13708`), exact port (`9090`), all observed outputs reproduced verbatim. |
| Real E2E (no mocks/TestClient) | PASS | Section 1-3 hit `http://localhost:9090/api/flows[/<id>]` via `curl` against a real `uv run flowstate server` process. No `TestClient`, no fixtures, no in-memory shortcuts. |
| Scenarios cover acceptance criteria | PASS | AC1 (list + detail), AC2 (backward-compat shape), AC3 (per-node), AC4 (test fixture migration), AC5 (existing tests pass) — each has a corresponding section. |
| Server restarted after changes | PASS | Section "Post-Implementation Verification" describes the agent starting `uv run flowstate server --host 127.0.0.1 --port 9090` (a fresh server, not a stale one). I independently confirmed by starting my own fresh server and hitting the same endpoints. |
| Reproduction logged before fix (bugs) | PASS | The reproduction was logged in the parent `SHARED-012-eval.md` "FAIL-1" section, which the issue file references. The pre-fix observed behavior (`discuss_flowstate lumon=True sandbox=False`) is documented as the regression this issue closes, and I had previously reproduced it firsthand. |

## Criteria Results

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | `GET /api/flows` and `/api/flows/:id` expose lumon/sandbox booleans + config paths matching the AST | PASS | Verified against real server. `lumon_flat_on` reports `lumon=true, sandbox=true, lumon_config="policies/strict.json", sandbox_policy="policies/strict.json"`. `sandbox_alias` reports same shape with the alias's policy file. `discuss_flowstate` reports `lumon=true, sandbox=true`. `lumon_flat_off` reports `false/false/null/null`. |
| 2 | Backward-compat shape preserved (`lumon`, `sandbox` booleans on list; `lumon_config`, `sandbox_policy` on detail) | PASS | List response includes `lumon`, `sandbox` (booleans). Detail response includes all four fields. UI consumers don't need to change. Verified across all 6 flows. |
| 3 | Per-node entries surface `sandbox`, `sandbox_policy`, `lumon`, `lumon_config` from the node's effective `LumonConfig` | PASS | `GET /api/flows/discuss_flowstate` returns all 4 nodes with `sandbox=None, sandbox_policy=None, lumon=None, lumon_config=None` — correct, since no node has its own override. The "absent override = None" semantics are honored. The new unit test `test_per_node_lumon_sandbox_fields` exercises the case where a node DOES have an override and confirms the values are surfaced. |
| 4 | Server test fixtures migrated to nested shape; regression closed | PASS | `tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields` has 9 tests, all passing. Two are new regression tests: `test_flow_explicit_disabled_with_config_path` (the exact `bool(dict)` case) and `test_flow_block_syntax_plugins_no_config_path`. Fixtures now use the nested `{"lumon": {"enabled": ..., ...}}` shape. |
| 5 | All existing server tests pass | PASS | `uv run pytest tests/server/` → **392 passed in 10.73s**, no regressions. `uv run ruff check src/flowstate/server/routes.py tests/server/test_flow_discovery.py` → "All checks passed!". |

## Sprint Contract Cross-Reference

SERVER-033 is not listed in Sprint 37's acceptance test grid (TEST-37a.*, TEST-37b.*, TEST-37c.*) because it's a follow-up issue created mid-sprint to close the API-parity gap I flagged in SHARED-012's evaluation. The implicit sprint requirement it closes is the "no regression vs Phase 36 baseline" expectation in the "Done Criteria" section.

The SHARED-012 evaluation classified the API regression as PASS-WITH-FOLLOW-UP, gated on SERVER-033 landing. With SERVER-033 now PASS, the 37b API/UI surface is correct and the broader Phase 37b sprint's API-parity concern is fully discharged.

## Independent Verification (My Own Reproduction)

I started a fresh server (`uv run flowstate server --host 127.0.0.1 --port 9090`, PID 16103, watching `./flows/`) and independently exercised every endpoint claimed in the agent's log. Results matched exactly.

### List endpoint
```
$ curl -s http://localhost:9090/api/flows | uv run python -c "
import json, sys
for fl in json.load(sys.stdin):
    print(f\"id={fl['id']:25s} lumon={fl.get('lumon')!s:6s} sandbox={fl.get('sandbox')!s:6s}\")"
id=agent_delegation          lumon=False  sandbox=False
id=discuss_flowstate         lumon=True   sandbox=True
id=implement_flowstate       lumon=False  sandbox=False
id=lumon_flat_off            lumon=False  sandbox=False
id=lumon_flat_on             lumon=True   sandbox=True
id=sandbox_alias             lumon=True   sandbox=True
```

The pre-fix output (recorded during SHARED-012 evaluation) showed `discuss_flowstate` as `lumon=True, sandbox=False` — that asymmetry is gone. Both fields now consistently surface `LumonConfig.enabled`.

### Detail endpoint — DSL-to-API mapping

| Flow | DSL source | Observed `lumon` | Observed `sandbox` | Observed `lumon_config` | Observed `sandbox_policy` |
|------|------------|------------------|--------------------|--------------------------|----------------------------|
| `lumon_flat_on` | `lumon = true; lumon_config = "policies/strict.json"` | `true` | `true` | `"policies/strict.json"` | `"policies/strict.json"` |
| `sandbox_alias` | `sandbox = true; sandbox_policy = "policies/network-none.json"` | `true` | `true` | `"policies/network-none.json"` | `"policies/network-none.json"` |
| `discuss_flowstate` | `sandbox = true` (no policy path) | `true` | `true` | `null` | `null` |
| `lumon_flat_off` | (no lumon/sandbox) | `false` | `false` | `null` | `null` |
| `agent_delegation` | (no lumon/sandbox) | `false` | `false` | `null` | `null` |
| `implement_flowstate` | (no lumon/sandbox) | `false` | `false` | `null` | `null` |

This is the exact matrix the issue claimed. The `sandbox_alias` case is the strongest evidence the parser collapses both aliases onto a single `LumonConfig` block AND the route reads `config_path` from it correctly.

### Per-node fields
For `discuss_flowstate` (only flow-level `sandbox = true`, no node overrides):
```
moderator  sandbox=None lumon=None
alice      sandbox=None lumon=None
bob        sandbox=None lumon=None
done       sandbox=None lumon=None
```
Correct — absent overrides surface as `None`, not coerced booleans.

### Unit tests
```
$ uv run pytest tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields -v
9 passed in 0.43s
```
All 9 pass, including the two new regression tests.

### Full server suite
```
$ uv run pytest tests/server/
392 passed in 10.73s
```
No regressions.

### Lint
```
$ uv run ruff check src/flowstate/server/routes.py tests/server/test_flow_discovery.py
All checks passed!
```

## Failures

None.

## Notes & Out-of-Scope Observations

- **Block syntax (`lumon { enabled = false config = "x" }`) is not yet parseable.** I attempted to create an `.flow` file with the block form to exercise the explicit-disabled regression case end-to-end at the parser layer, and got a parse error: `No terminal matches '{' in the current parser context`. This is expected — DSL-016 (block grammar) is still `todo` and was not in SERVER-033's scope. The explicit-disabled regression is instead covered by the new unit test `test_flow_explicit_disabled_with_config_path`, which constructs the nested-dict `ast_json` directly. This is a fully acceptable testing strategy given SERVER-033's scope. The end-to-end repro will become possible once DSL-016 lands; the route is already prepared for it.
- **Performance / shape sanity**: Detail response sizes are reasonable (~600 bytes for `lumon_flat_on`); no response-bloat regression. List response is ~18KB for 6 flows including all pre-existing fields (params, nodes, edges, etc.) — same scale as Phase 36.
- **Backward-compat shape strictly preserved**: I diffed the response field set against the SHARED-012 pre-eval shape (recorded in `issues/evals/SHARED-012-eval.md`). All four keys (`lumon`, `sandbox`, `lumon_config`, `sandbox_policy`) are present in the same positions with the same types. UI consumers (FlowDetailPanel badges) do not need to change.

## Summary

**5 of 5 acceptance criteria PASS. 9 of 9 `TestFlowLumonSandboxFields` tests PASS. 392 of 392 server tests PASS. No lint failures.**

The regression I flagged during SHARED-012's evaluation is fully closed against the real running server, independently verified. The two new regression tests close the test-suite blind spot that hid the bug originally — the route now reads the post-SHARED-012 nested AST shape correctly for all four documented cases (flat-on with config, flat-off, sandbox alias, and explicit-disabled-with-config), and the API surface is byte-compatible with the pre-SHARED-012 contract.

**Verdict: PASS.** SERVER-033 may be marked done. The Phase 37b API-parity concern is fully discharged.
