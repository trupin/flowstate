# [SERVER-013] Harness config + server wiring

## Domain
server

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-033
- Blocks: —

## Spec References
- specs.md Section 13 — "Configuration"

## Summary
Add `[harnesses.*]` configuration section to `flowstate.toml` for defining named harness entries. Wire `HarnessManager` into the server app lifecycle so executors created by routes and QueueManager receive it.

## Acceptance Criteria
- [ ] `FlowstateConfig.harnesses` field: `dict[str, HarnessConfigEntry]` (default empty)
- [ ] TOML parsing: `[harnesses.gemini]` with `command` and optional `env` fields
- [ ] `create_app()` creates `HarnessManager` from config and stores on `app.state`
- [ ] Routes pass `harness_mgr` to `FlowExecutor` constructors
- [ ] `QueueManager` accepts and passes `harness_mgr` to executors
- [ ] Default config (no `[harnesses]` section) works with just the "claude" default
- [ ] API: `GET /api/flows` response includes `harness` field from flow AST

## Technical Design

### Files to Modify
- `src/flowstate/config.py` — Add `HarnessConfigEntry` dataclass; add `harnesses` field to `FlowstateConfig`; parse `[harnesses.*]` in `_parse_toml`
- `src/flowstate/server/app.py` — In `create_app()`: create `HarnessManager(default_harness=subprocess_manager, configs=config.harnesses)`; store on `app.state.harness_manager`; pass to QueueManager in lifespan
- `src/flowstate/server/routes.py` — In `start_run` and any route creating `FlowExecutor`: pass `harness_mgr=request.app.state.harness_manager`
- `src/flowstate/engine/queue_manager.py` — Accept `harness_mgr` param, pass to FlowExecutor

### Key Implementation Details

Config dataclass:
```python
@dataclass
class HarnessConfigEntry:
    command: list[str]
    env: dict[str, str] | None = None
```

TOML example:
```toml
[harnesses.gemini]
command = ["gemini"]
env = { GEMINI_API_KEY = "..." }
```

Parsing:
```python
harnesses = {}
for name, entry in data.get("harnesses", {}).items():
    harnesses[name] = HarnessConfigEntry(command=entry["command"], env=entry.get("env"))
kwargs["harnesses"] = harnesses
```

### Edge Cases
- No `[harnesses]` section → empty dict → only "claude" available
- Harness referenced in DSL but not in config → `HarnessNotFoundError` at runtime
- Malformed harness config (missing `command`) → error at config parse time

## Testing Strategy
- `tests/test_config.py` — Parse TOML with harness section, verify `FlowstateConfig.harnesses`
- `tests/server/test_run_management.py` — Verify FlowExecutor receives harness_mgr
- All existing tests pass (no harness config = empty dict = only "claude")
- `uv run pytest tests/ --ignore=tests/e2e/ -x`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
