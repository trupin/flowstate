# [SERVER-024] Remove sandbox preflight checks, config, and server-side sandbox code

## Domain
server

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-075
- Blocks: —

## Spec References
- specs.md Section 9.7 — to be updated

## Summary
Remove all OpenShell sandbox code from the server layer: preflight checks in routes.py, sandbox config in websocket.py and app.py, sandbox_name from FlowstateConfig. Delete the sandbox preflight test file.

## Acceptance Criteria
- [ ] `_check_sandbox_requirements()` function deleted from routes.py
- [ ] 3 calls to `_check_sandbox_requirements()` removed from routes.py (run, restart, trigger endpoints)
- [ ] Sandbox preflight validation block removed from websocket.py
- [ ] `self._sandbox_name` and `sandbox_name` parameter removed from websocket.py
- [ ] `sandbox_name=config.sandbox_name` removed from app.py ws_hub config
- [ ] `sandbox_name` field removed from FlowstateConfig in config.py
- [ ] Sandbox section parsing removed from `_parse_toml()` in config.py
- [ ] `sandbox_name` parameter removed from FlowExecutor constructor calls in routes.py (all 4 sites)
- [ ] `tests/server/test_sandbox_preflight.py` deleted
- [ ] All remaining server tests pass
- [ ] Lint and type checks pass

## Technical Design

### Files to Delete
- `tests/server/test_sandbox_preflight.py`

### Files to Modify

**`src/flowstate/server/routes.py`:**
- Delete `_check_sandbox_requirements()` function (~130 lines)
- Remove 3 calls: in `start_run`, `restart_from_task`, `trigger_schedule`
- Remove `sandbox_name=config.sandbox_name` from all FlowExecutor instantiations (4 sites)
- Remove `shutil` import if no longer needed

**`src/flowstate/server/websocket.py`:**
- Remove `self._sandbox_name` attribute
- Remove `sandbox_name` parameter from `set_executor_config()`
- Remove sandbox preflight validation block (~50 lines)
- Remove `sandbox_name=self._sandbox_name` from FlowExecutor instantiation

**`src/flowstate/server/app.py`:**
- Remove `sandbox_name=config.sandbox_name` from `ws_hub.set_executor_config()` call

**`src/flowstate/config.py`:**
- Remove `sandbox_name: str = "flowstate-claude"` from FlowstateConfig
- Remove sandbox section parsing (3 lines in `_parse_toml()`)

## Testing Strategy
- `uv run pytest tests/server/ -q` — all pass
- `uv run ruff check src/flowstate/server/ src/flowstate/config.py`
- `uv run pyright src/flowstate/server/ src/flowstate/config.py`

## E2E Verification Plan

### Verification Steps
1. Run full server test suite
2. Verify no references to sandbox_name remain in server code
3. Start server: `uv run flowstate server` — should start without errors

## E2E Verification Log
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
