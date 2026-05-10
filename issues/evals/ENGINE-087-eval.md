# Evaluation: ENGINE-087

**Date**: 2026-05-10
**Sprint**: sprint-037 (Phase 37b — closes the lumon block arc)
**Verdict**: PASS

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | "E2E Verification Log > Post-Implementation Verification" section in `issues/engine/087-lumon-config-resolution.md` is filled in (not placeholder text) |
| Commands are specific and concrete | PASS | Exact `uv run pytest` invocations, named tests, exact pass counts, ruff/pyright commands all listed |
| Real E2E (no mocks/TestClient) | PARTIAL → ACCEPTED | The `lumon deploy` subprocess is mocked (intentionally — it's a side-effecting external CLI), but `.lumon.json` is written by the real `setup_lumon` to real on-disk paths and the assertions inspect the real file content. This matches the issue's stated verification path. Evaluator independently re-ran the same exercise (see below) against the real `setup_lumon` with the same mocked subprocess and confirmed identical results. |
| Scenarios cover acceptance criteria | PASS | Every one of the 7 acceptance criteria has a corresponding named test; sprint TEST-37b.11 through TEST-37b.15 each have a direct test |
| Server restarted after changes | N/A | Engine-internal change; the proof exercises `setup_lumon` directly via pytest, which is the correct seam for this work |
| Reproduction logged before fix (bugs) | N/A | Feature work, not a bug fix |

## Independent Evaluator Reproduction

Beyond auditing the agent's log, I wrote and ran an independent script that imports `setup_lumon` from `flowstate.engine.lumon` and exercises it against four real temp worktrees with only `asyncio.create_subprocess_exec` patched (because `lumon deploy` isn't installed and isn't what's under test). For each case I parsed the resulting `.lumon.json` file with `json.loads` and compared `set(config["plugins"].keys())` to the expected set with `==` equality:

| Case | Input | Expected key set (EXACT) | Observed key set | Result |
|------|-------|---------------------------|------------------|--------|
| A | flow.lumon = `LumonConfig(enabled=True, plugins=("filesystem", "git"))`, node.lumon = None | `{filesystem, git, flowstate}` | `{git, filesystem, flowstate}` | PASS |
| B | flow.lumon = `LumonConfig(enabled=True, config_path="policy.json")` where `policy.json` contains `{"plugins": {"custom": {"foo": 1}}}`, node.lumon = None | `{custom, flowstate}` AND `custom` plugin retains `{"foo": 1}` | `{custom, flowstate}`, custom payload `{"foo": 1}` | PASS |
| C | flow.lumon = `LumonConfig(enabled=True, config_path="x.json")` where `x.json` contains `{"plugins": {"poisoned": {"leaks": True}}}`, node.lumon = `LumonConfig(enabled=True, plugins=("y",))` | `{y, flowstate}` AND `poisoned` MUST NOT appear | `{y, flowstate}`, `poisoned` not present | PASS |
| D | flow.lumon = `LumonConfig(enabled=True, plugins=())`, node.lumon = None | `{flowstate}` | `{flowstate}` | PASS |

Full Case A output (the load-bearing TEST-37b.11/12 result): the real file written to `<wt>/.lumon.json` parsed to `{'plugins': {'filesystem': {}, 'git': {}, 'flowstate': {}}}` — three plugins, no extras, no missing, each mapped to `{}` exactly as the spec requires.

## Test-File Audit (anti-"contains" check)

Sprint contract emphasizes that the assertion must be exact-set, not "contains." I read the new tests directly (`tests/engine/test_lumon.py:710-887`) and confirmed every plugin-synthesis test uses:

```python
assert set(config["plugins"].keys()) == {<expected>}
```

— not `>=`, not `issubset`, not `in`. Two tests additionally assert specific keys are NOT present (`assert "should_not_appear" not in config["plugins"]`, `assert "a" not in config["plugins"]`, `assert "from_file" not in config["plugins"]`). These are real exact-set assertions; extras cannot slip through.

## Criteria Results

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | `_use_lumon(flow, node)` reads `LumonConfig.enabled` from effective scope (node overrides flow; node None inherits) | PASS | `TestUseLumon` (11 tests) + `TestEffectiveLumonConfig::test_node_disabled_overrides_flow_enabled` |
| 2 | `_lumon_config` / effective resolver returns `config_path` or synthesized representation when `plugins` set | PASS | `TestEffectiveLumonConfig` (5 tests) — node fully overrides, no field-merging |
| 3 | `setup_lumon` synthesizes `.lumon.json` from a plugins list when no `config_path` is set | PASS | TEST-37b.11/12: evaluator Case A independently confirmed `{filesystem, git, flowstate}` exact-set |
| 4 | Node `lumon` fully overrides flow `lumon` (no merge) when both could apply | PASS | TEST-37b.14: evaluator Case C independently confirmed flow's `x.json` does NOT leak when node has `plugins` |
| 5 | Built-in `flowstate` plugin always added | PASS | Present in every observed `.lumon.json` across all four evaluator cases including empty-tuple Case D |
| 6 | Existing lumon-using flows behave identically (no regression for flat syntax) | PASS | `TestSetupLumonBackwardCompat` (2 tests pass); 35 pre-existing tests in `test_lumon.py` still pass; sprint TEST-37b.3 |
| 7 | Fixture: `lumon { enabled = true, plugins = ["filesystem", "git"] }` -> `.lumon.json` with exactly those plugins (the two listed + flowstate) | PASS | TEST-37b.11/12: evaluator Case A independently confirmed exact JSON `{"plugins": {"filesystem": {}, "git": {}, "flowstate": {}}}` |

## Sprint Phase 37b Acceptance Test Mapping

| Sprint test | Mapped test | Result |
|-------------|-------------|--------|
| TEST-37b.11 (single plugin -> {plugin, flowstate}) | `test_single_plugin_synthesizes_lumon_json` | PASS |
| TEST-37b.12 (multi-plugin -> {p1, p2, flowstate}) | `test_plugins_list_synthesizes_lumon_json` + evaluator Case A | PASS |
| TEST-37b.13 (config path loads from disk + flowstate merged) | `test_config_path_branch_preserves_existing_behavior` + evaluator Case B | PASS |
| TEST-37b.14 (node fully overrides flow, no merge) | `test_node_plugins_fully_override_flow_config_path` + evaluator Case C | PASS |
| TEST-37b.15 (node `enabled=false` disables despite flow `enabled=true`) | `test_node_disabled_overrides_flow_enabled` | PASS |

## Regression Checks

- `uv run pytest tests/engine/test_lumon.py -v` -> **50 passed in 0.11s** (35 pre-existing + 15 new)
- `uv run pytest tests/engine/ --ignore=tests/engine/test_executor.py -q` -> **505 passed in 69.55s** (no regressions; matches agent's claim; the `test_executor.py` hang is pre-existing and out of scope for this issue)
- `uv run pytest tests/dsl/ -q` -> **446 passed in 2.49s** (no DSL regression from SHARED-012/DSL-016 prior arc)
- `uv run ruff check src/flowstate/engine/lumon.py tests/engine/test_lumon.py` -> **All checks passed!**
- `uv run pyright src/flowstate/engine/lumon.py tests/engine/test_lumon.py` -> **0 errors, 0 warnings, 0 informations**

## Failures

None.

## Summary

7 of 7 acceptance criteria PASS. 5 of 5 mapped sprint Phase 37b ENGINE-087 acceptance tests (TEST-37b.11 through TEST-37b.15) PASS. Independent evaluator reproductions for all four behavioral classes (plugin synthesis, config-path load + flowstate merge, node-overrides-flow full override, empty-tuple flowstate-only) confirmed exact-set equality of `.lumon.json` plugin keys on real on-disk files. The tests use proper `set(...) == {...}` exact-equality assertions, not "contains" checks; the load-bearing fixture in `test_plugins_list_synthesizes_lumon_json` produces exactly `{"plugins": {"filesystem": {}, "git": {}, "flowstate": {}}}` — three plugins, no extras, no missing, each mapped to `{}`. Backward compatibility for flat-syntax flows is preserved. Lint and pyright clean. Phase 37b's behavioral arc (SHARED-012 -> DSL-016 -> ENGINE-087) is complete.
