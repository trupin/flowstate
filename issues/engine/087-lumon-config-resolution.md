# [ENGINE-087] Adapt Lumon resolution to `LumonConfig` and synthesize `.lumon.json` from `plugins` list

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SHARED-012, DSL-016
- Blocks: —

## Spec References
- specs.md Section 9.9 — "Lumon Security Layer"
- specs.md Section 3 — block syntax for `lumon`

## Summary
Update `engine/lumon.py` to read `LumonConfig` (post-SHARED-012) and to honor the new `plugins` list syntax (post-DSL-016). When a node's effective `LumonConfig` has `plugins` set, the engine synthesizes a `.lumon.json` config in the worktree containing exactly those plugins (plus the always-included built-in `flowstate` plugin). When `config_path` is set, existing behavior (load and merge from disk) is preserved.

## Acceptance Criteria
- [ ] `_use_lumon(flow, node)` reads `LumonConfig.enabled` from the effective scope (node overrides flow; node `None` inherits)
- [ ] `_lumon_config(flow, node)` returns either the `config_path` or a synthesized in-memory representation when `plugins` is set
- [ ] `setup_lumon` synthesizes `.lumon.json` from a plugins list when no `config_path` is set
- [ ] When both `config_path` and `plugins` could be in scope (e.g. flow has `config_path`, node has `plugins`), the **node's** value wins entirely (full override, not merge)
- [ ] Built-in `flowstate` plugin is always added (existing behavior preserved)
- [ ] All existing lumon-using flows behave identically (no regression for flows that haven't migrated to block syntax)
- [ ] New fixture: a flow with `lumon { enabled = true, plugins = ["filesystem", "git"] }` produces a `.lumon.json` containing exactly those three plugins (the two listed + flowstate)

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/lumon.py` — refactor `_use_lumon`, `_lumon_config`, `setup_lumon`
- `tests/engine/test_lumon.py` — new tests for plugin-list synthesis
- `tests/engine/fixtures/plugins/filesystem/` — minimal plugin dir for testing
- `tests/engine/fixtures/plugins/git/`

### Key Implementation Details

**Effective config resolution:**

```python
def _effective_lumon_config(flow: Flow, node: Node) -> LumonConfig | None:
    """Node overrides flow entirely (not merged)."""
    if node.lumon is not None:
        return node.lumon
    return flow.lumon

def _use_lumon(flow: Flow, node: Node) -> bool:
    cfg = _effective_lumon_config(flow, node)
    return cfg is not None and cfg.enabled
```

**Setup with plugin synthesis:**

```python
async def setup_lumon(
    worktree_path: str, flow: Flow, node: Node, flow_file_dir: str | None = None,
) -> None:
    wt = Path(worktree_path)
    
    # 1. Plugin management (unchanged)
    plugins_dir = wt / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    global_plugins = _default_data_dir() / "plugins"
    _symlink_plugins_from(global_plugins, plugins_dir)
    if flow_file_dir:
        _symlink_plugins_from(Path(flow_file_dir) / "plugins", plugins_dir)
    builtin = _builtin_plugin_dir()
    if builtin.is_dir():
        target = plugins_dir / "flowstate"
        if not target.exists():
            target.symlink_to(builtin)

    # 2. Build .lumon.json
    cfg = _effective_lumon_config(flow, node)
    lumon_config: dict = {"plugins": {}}
    
    if cfg is not None:
        if cfg.plugins is not None:
            # Synthesize from plugin list
            for name in cfg.plugins:
                lumon_config["plugins"][name] = {}
        elif cfg.config_path and flow_file_dir:
            # Load from disk (existing path)
            src = Path(flow_file_dir) / cfg.config_path
            if src.exists():
                lumon_config = json.loads(src.read_text())
            else:
                logger.warning(
                    "Lumon config '%s' not found at '%s', using defaults",
                    cfg.config_path, src,
                )
    
    # Always register flowstate plugin (existing behavior)
    plugins = lumon_config.setdefault("plugins", {})
    if "flowstate" not in plugins:
        plugins["flowstate"] = {}
    
    (wt / ".lumon.json").write_text(json.dumps(lumon_config, indent=2))
    
    # 3. Run lumon deploy (unchanged)
    # 4. Create sandbox dir (unchanged)
    ...
```

**Drop the legacy `_lumon_config` function** (replaced by `_effective_lumon_config` returning the whole `LumonConfig`). Update any callers in `setup_lumon` and elsewhere.

### Edge Cases
- `LumonConfig(enabled=True, plugins=None, config_path=None)` (e.g. flow has `lumon { enabled = true }` only) → synthesizes `.lumon.json` with only the built-in `flowstate` plugin. Equivalent to today's "lumon enabled, no config" behavior.
- `LumonConfig(enabled=True, plugins=())` (empty tuple, explicit) → same as above (only flowstate plugin loaded).
- Node has `LumonConfig(enabled=False)` while flow has `enabled=True, plugins=[...]` → node fully overrides; lumon not used for that node.
- `cfg.plugins` set but a referenced plugin dir is missing at run-time → DSL-016's L3 should have caught this at parse time, but defense in depth: log warning and continue (the lumon CLI itself will surface a clear error).

## Testing Strategy
- Unit test: `_use_lumon` with various flow/node `LumonConfig` combinations
- Unit test: `setup_lumon` writes the expected `.lumon.json` for a plugins-list case (use `tmp_path` fixture, mock the `lumon deploy` subprocess call)
- Unit test: `setup_lumon` preserves existing behavior for `config_path`-based configs
- Regression: existing flows in `flows/` that use lumon work unchanged

## E2E Verification Plan

### Verification Steps
1. Create a flow with `lumon { enabled = true, plugins = ["filesystem"] }` and a node that exercises lumon
2. Submit a task; let it execute
3. Inspect the worktree's `.lumon.json` — should contain exactly `{"plugins": {"filesystem": {}, "flowstate": {}}}`
4. Modify the flow to use `lumon { enabled = true, config = "policy.json" }` with a `policy.json` next to the flow file
5. Submit again; verify `.lumon.json` is loaded from `policy.json` content (with flowstate plugin merged in)

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in: exact commands, observed output, confirmation fix/feature works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
