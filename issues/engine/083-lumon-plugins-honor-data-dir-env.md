# [ENGINE-083] Lumon: global plugins path honors `FLOWSTATE_DATA_DIR`

## Domain
engine

## Status
done

**Eval verdict: PASS (issues/evals/sprint-phase-32-eval.md, batch-level)**

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: SHARED-007
- Blocks: —

## Spec References
- specs.md §13.3 Project Layout — `FLOWSTATE_DATA_DIR` env var

## Summary
`src/flowstate/engine/lumon.py:111` reads global Lumon plugins from `Path.home() / ".flowstate" / "plugins"`. This bypasses the `FLOWSTATE_DATA_DIR` env var override, so a user who relocates their Flowstate data directory (for testing, CI, containerized deployments, multi-user dev machines) still gets plugins from `~/.flowstate/plugins/` instead of `<FLOWSTATE_DATA_DIR>/plugins/`. Trivial fix: replace the hardcoded path with `_default_data_dir() / "plugins"`, which already honors the env var.

## Acceptance Criteria
- [ ] `src/flowstate/engine/lumon.py:111` uses `_default_data_dir() / "plugins"` (imported from `flowstate.config`).
- [ ] No other `Path.home() / ".flowstate"` literals remain in `src/flowstate/`.
- [ ] New unit test in `tests/engine/test_lumon.py`: with `FLOWSTATE_DATA_DIR=/tmp/fs-custom-data`, `setup_lumon` looks for global plugins under `/tmp/fs-custom-data/plugins/`, not `~/.flowstate/plugins/`.
- [ ] All 33 existing `test_lumon.py` tests still pass.

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/lumon.py` — one import line + one path expression.
- `tests/engine/test_lumon.py` — new test asserting the env var is honored.

### Key Implementation Details
```python
# Top of lumon.py
from flowstate.config import _default_data_dir

# In setup_lumon():
global_plugins = _default_data_dir() / "plugins"
_symlink_plugins_from(global_plugins, plugins_dir)
```

`_default_data_dir` is already exported from `config.py` (it's the underscore-prefixed helper used by `build_project`). Re-exporting from `lumon.py`'s module namespace would be inappropriate — keep the import explicit.

### Edge Cases
- `_default_data_dir()` is called per `setup_lumon` invocation rather than cached, so it picks up env var changes between test cases. This matches how `build_project` already uses it.
- If `<data_root>/plugins/` doesn't exist, `_symlink_plugins_from` already no-ops — no new error path needed.

## Testing Strategy
- New test: set `FLOWSTATE_DATA_DIR` via `monkeypatch.setenv`, create `<custom_dir>/plugins/myplugin/` with a marker file, call `setup_lumon` against a tmp_path worktree, assert the plugin was symlinked. Negate by also creating `~/.flowstate/plugins/wrongplugin/` and asserting it was **not** picked up.
- Regression: full `tests/engine/test_lumon.py` run.

## E2E Verification Plan

Skip — this is a P2 housekeeping fix on a code path that requires a real Lumon CLI binary on the host. Unit test coverage is sufficient.

## E2E Verification Log
_Not applicable — unit tests only._

## Completion Checklist
- [ ] `lumon.py` uses `_default_data_dir() / "plugins"`
- [ ] No `Path.home() / ".flowstate"` literals remain in `src/`
- [ ] New unit test passing
- [ ] `tests/engine/test_lumon.py` 33/33 passing
- [ ] `/lint` passes
