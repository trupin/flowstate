# Evaluation: DSL-016

**Date**: 2026-05-10
**Sprint**: sprint-037 (Phase 37b)
**Verdict**: PASS

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | Issue file has filled-in `E2E Verification Log > Post-Implementation Verification` section with timestamped output, not placeholder. |
| Commands are specific and concrete | PASS | Each fixture has an explicit `uv run flowstate check ...` invocation with the literal observed stdout, including exit codes and full rule-prefixed error strings. |
| Real E2E (no mocks/TestClient) | PASS | Evidence uses the real `flowstate check` CLI binary against on-disk `.flow` files in `tests/dsl/fixtures/`. No `TestClient`, no in-memory shortcuts. (DSL changes don't need a running server — the CLI is the production parse+type-check entrypoint.) |
| Scenarios cover acceptance criteria | PASS | Block parsing at flow + node, L1, L2, L3, mixed-syntax, backward compat all covered with one fixture each plus the unit-test layer. |
| Server restarted after changes | N/A | DSL-016 is a parser/type-check change exercised via the CLI; no long-running server state to flush. |
| Reproduction logged before fix (bugs) | N/A | DSL-016 is a feature, not a bug. |

## Criteria Results

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | Grammar rule `lumon_block` parses at flow level | PASS | `valid_lumon_block.flow` → `OK` (exit 0). Verified by `tests/dsl/test_parser.py::TestLumonBlockParsing::test_fixture_valid_lumon_block`. |
| 2 | Same block parses at node level (entry, task, exit, atomic) | PASS | `valid_lumon_block_node_override.flow` has a flow-level + node-level block and parses cleanly. Node-level override test in `TestLumonBlockParsing` passes. |
| 3 | `enabled = true/false` parses inside the block | PASS | Covered by all valid fixtures + parser tests. |
| 4 | `plugins = [name1, name2]` parses inside the block | PASS | `valid_lumon_block.flow` uses `plugins = ["sample"]`; parser tests cover the list shape. |
| 5 | `config = "<path>"` parses inside the block | PASS | `invalid_lumon_block_l2.flow` uses `config = "policy.json"` and it parses (it errors at L2, not at parse). |
| 6 | Parser populates `Flow.lumon` / `Node.lumon` from the block | PASS | Inferred from L1/L2/L3 type-check rules firing correctly on the parsed AST (they read `flow.lumon` and `node.lumon`). Also covered by `TestLumonBlockParsing` unit tests. |
| 7 | Block + flat in same scope → parse error | PASS | `invalid_lumon_block_mixed.flow` → `Parse error: ... cannot mix the lumon { ... } block with flat lumon/sandbox attributes (lumon). Use the lumon block or the flat attributes, not both, within the same scope.` Also confirmed for `sandbox` and `lumon_config` flat attrs (custom fixtures). |
| 8 | L1: plugins/config without enabled → error | PASS | `invalid_lumon_block_l1.flow` → `FlowTypeError(rule='L1', message="lumon.plugins/config at flow 'lumon_block_l1' require lumon.enabled = true", ...)`. Also verified at node scope via custom fixture. |
| 9 | L2: plugins and config in same block → error | PASS | `invalid_lumon_block_l2.flow` → `FlowTypeError(rule='L2', message="lumon.plugins and lumon.config are mutually exclusive at flow 'lumon_block_l2' ...", ...)`. |
| 10 | L3: plugin names resolve to known directories or error | PASS | `invalid_lumon_block_l3.flow` → `FlowTypeError(rule='L3', message="lumon plugin 'definitely_not_a_plugin_xyz' at flow 'lumon_block_l3' not found (looked in /Users/theophanerupin/code/flowstate/tests/dsl/fixtures/plugins/.../, /Users/theophanerupin/.flowstate/plugins/.../, and the built-in flowstate plugin)", ...)`. All three lookup locations are named in the error. Per-flow `<flow_dir>/plugins/<name>` resolution verified independently with a scratch plugin (`/tmp/dsl016-eval/plugins/local_only_plugin/`). User-global `~/.flowstate/plugins/<name>` resolution verified independently with a scratch global plugin. |
| 11 | Flat-syntax fixtures still pass (backward compat) | PASS | `tests/dsl/fixtures/valid_lumon.flow` and `valid_sandbox.flow` → both `OK`. Every flow in `flows/*.flow` (agent_delegation, discuss_flowstate, implement_flowstate, lumon_flat_off, lumon_flat_on, sandbox_alias) still `OK`. 446 dsl tests pass (40 new + 406 baseline). |
| 12 | Spec section 3 updated; block primary; flat deprecated | PASS | `specs.md` §3.1 keywords list includes `enabled`, `plugins`, `config`. §3.2 marks `sandbox`/`sandbox_policy`/`lumon`/`lumon_config` as `DEPRECATED — use lumon { ... }` and shows the new block as primary syntax. §3.4 lists `lumon { ... }` at entry, task, exit, and atomic nodes. §4.5 documents L1/L2/L3 with per-scope semantics explicitly called out. |

### TEST-37b.3 — Per-Scope Mixed-Syntax (Sprint-Planner Risk #4)

This was the critical risk: easy to over-correct and reject the cross-scope case. Tested explicitly with three custom flows:

1. **Flat at flow + block at node** (`flat_flow_block_node.flow`) — different scopes, must PASS. Result: `OK` (exit 0). ✓
2. **Block at flow + flat at node** (`block_flow_flat_node.flow`) — different scopes, must PASS. Result: `OK` (exit 0). ✓
3. **Mixed inside one node body** (`same_scope_node_mixed.flow`) — same scope, must FAIL. Result: `Parse error: in node 'work': cannot mix the lumon { ... } block with flat lumon/sandbox attributes (lumon). ...` (exit 1). ✓

The error attribution is also scope-precise: in a multi-node fixture where one node mixes and another doesn't (`multi_node_one_mixed.flow`), the error correctly identifies `node 'work_mixed'` rather than firing a generic flow-wide error.

### Lint / Type Check

- `uv run ruff check src/flowstate/dsl/ tests/dsl/` → `All checks passed!`
- `uv run pyright src/flowstate/dsl/` → `0 errors, 0 warnings, 0 informations`

### Test Counts

- `uv run pytest tests/dsl/ -v -k "lumon_block or L1 or L2 or L3 or mixed"` → 43 passed, 403 deselected, 0 failed.
- `uv run pytest tests/dsl/ tests/state/ --tb=no -q` → 675 passed (no regressions vs Phase 36 dsl/state baseline).

## Failures

None.

## Observations (non-blocking, outside DSL-016 scope)

- **Spec §11.1 still describes the old flat AST shape.** Section 11 (line 1797, 1799) lists `lumon = true|false`, `lumon_config = "<path>"`, `sandbox`, `sandbox_policy` as flow/node attributes, and §11.1 (line 1812, 1815) describes `Node`/`Flow` dataclasses with those flat fields. SHARED-012 migrated the AST to a single `LumonConfig` field, but §11/§11.1 wasn't updated to match. The sprint Done Criteria says §11.1 should be updated. This belongs to SHARED-012, not DSL-016 — DSL-016's ACs only call out §3 (done) and §4 (done). Flagging for the orchestrator to route to SHARED-012 follow-up. This does NOT block DSL-016.

## Summary

12 of 12 criteria PASS. The critical per-scope vs. cross-scope semantics (sprint-planner-flagged risk #4) is implemented correctly:

- Same-scope mixed (flat + block in the same flow body or in the same node body) → parse error with the correct scope name attributed.
- Cross-scope mixed (flat at one level, block at another level) → parses cleanly because node-level lumon fully overrides flow-level and the two scopes are evaluated independently.

L3 honors the documented lookup order (per-flow `<flow_dir>/plugins/<name>/`, user-global `~/.flowstate/plugins/<name>/`, built-in flowstate plugin), and the error message names all three. L1 and L2 fire at both flow and node scope with correctly scoped messages. Backward compat is fully preserved — every legacy flat-syntax fixture still parses and type-checks cleanly.

DSL-016 is done. Recommend follow-up issue under SHARED-012 to refresh §11/§11.1 of `specs.md` to match the new `LumonConfig`-based AST shape.
