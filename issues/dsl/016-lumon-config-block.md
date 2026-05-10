# [DSL-016] `lumon { ... }` config block syntax

## Domain
dsl

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: SHARED-012
- Blocks: ENGINE-087

## Spec References
- specs.md Section 3.2 — "Flow Declaration"
- specs.md Section 3.4 — "Node Declarations"
- specs.md Section 4 — Type System (new rules L1, L2, L3)

## Summary
Add a nested `lumon { ... }` block at both flow and node level. Inside the block: `enabled = bool`, `plugins = [name1, name2, ...]`, `config = "<path>"`. Replaces the flat `lumon = true` / `lumon_config = "..."` / `sandbox = ...` / `sandbox_policy = ...` syntax (which keeps parsing for backward compatibility — see SHARED-012). Adds three new type-checker rules: L1 (`plugins` requires `enabled = true`), L2 (`plugins` and `config` mutex within the same block), L3 (each plugin name resolves to an existing plugin directory).

## Acceptance Criteria
- [ ] Grammar rule `lumon_block: "lumon" "{" lumon_attr* "}"` parses at flow level
- [ ] Same block parses at node level (entry, task, exit, atomic)
- [ ] `enabled = true | false` parses inside the block
- [ ] `plugins = [name1, name2]` parses inside the block (list of bare identifiers)
- [ ] `config = "<path>"` parses inside the block
- [ ] Parser populates `Flow.lumon: LumonConfig | None` and `Node.lumon: LumonConfig | None` from the block
- [ ] When both block and flat syntax are present in the same scope → parse error: "use the lumon block or the flat attributes, not both"
- [ ] L1: setting `plugins` (or `config`) without `enabled = true` (in scope) → error
- [ ] L2: setting both `plugins` and `config` in the same block → error
- [ ] L3: each plugin name in `plugins` resolves to `<flow_dir>/plugins/<name>/`, `~/.flowstate/plugins/<name>/`, or the bundled flowstate plugin → otherwise error
- [ ] Flat-syntax fixtures still pass (backward compat preserved by SHARED-012's parser layer)
- [ ] Spec section 3 is updated to describe the block as primary syntax, flat marked deprecated

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/grammar.lark` — new `lumon_block`, `lumon_attr` rules
- `src/flowstate/dsl/parser.py` — transformer for `lumon_block`; merge with flat-syntax fallback from SHARED-012
- `src/flowstate/dsl/type_checker.py` — rules L1, L2, L3 (replace prior LM1 since L1 supersedes it)
- `specs.md` — section 3.2 / 3.4 update; section 4 add L1/L2/L3
- `tests/dsl/fixtures/valid_lumon_block.flow`
- `tests/dsl/fixtures/valid_lumon_block_node_override.flow`
- `tests/dsl/fixtures/invalid_lumon_block_mixed.flow` — block + flat attrs in same scope
- `tests/dsl/fixtures/invalid_lumon_block_l1.flow` — plugins without enabled
- `tests/dsl/fixtures/invalid_lumon_block_l2.flow` — plugins and config together
- `tests/dsl/fixtures/invalid_lumon_block_l3.flow` — unknown plugin name
- `tests/dsl/fixtures/plugins/<sample>/` — minimal plugin dir for L3 test
- `tests/dsl/test_parser.py` — block-parsing tests
- `tests/dsl/test_type_checker.py` — L1, L2, L3 tests

### Key Implementation Details

**Grammar (`grammar.lark`):**

```lark
flow_attr: ...
         | lumon_block

node_attr: ...
         | lumon_block

lumon_block: "lumon" "{" lumon_attr_inner* "}"

lumon_attr_inner: "enabled" "=" BOOL_LIT          -> lumon_enabled
                | "plugins" "=" "[" name_list "]" -> lumon_plugins
                | "config" "=" STRING             -> lumon_config_path
```

`name_list` already exists in the grammar (used for fork/join targets).

Add `enabled`, `plugins`, `config` to the keyword list in spec 3.1.

**Parser (`parser.py`):**

```python
def lumon_block(self, items):
    cfg_dict: dict = {}
    for item in items:
        if item is None:
            continue
        key, val = item
        cfg_dict[key] = val
    return ("__lumon_block__", LumonConfig(
        enabled=cfg_dict.get("enabled", False),
        plugins=cfg_dict.get("plugins"),
        config_path=cfg_dict.get("config_path"),
    ))

def lumon_enabled(self, items):
    return ("enabled", str(items[0]) == "true")

def lumon_plugins(self, items):
    # name_list returns a list of NAME tokens
    return ("plugins", tuple(str(n) for n in items[0]))

def lumon_config_path(self, items):
    return ("config_path", _strip_string(items[0]))
```

In each scope's builder (flow_decl, entry_node, task_node, exit_node, atomic_node):
- Collect attrs as before
- Detect mixed-syntax: if `__lumon_block__` is in attrs AND any of `lumon`/`lumon_config`/`sandbox`/`sandbox_policy` is present, raise a parse error "use the lumon block or the flat attributes, not both"
- If block present, use it as the `LumonConfig`. Otherwise fall back to `_build_lumon_from_flat` (from SHARED-012).

**Type checker (`type_checker.py`):**

Replace LM1 with L1, L2, L3. L1 is a generalization of LM1 — `enabled` must be true at the *effective* scope (node or inherited from flow) for either `plugins` or `config_path` to be set.

```python
def _check_lumon_rules(flow: Flow, flow_file_dir: Path | None) -> list[FlowTypeError]:
    errors = []
    
    # Flow scope
    if flow.lumon:
        errors += _validate_lumon_config(flow.lumon, "flow", flow_file_dir, parent_enabled=False)
    
    # Node scopes
    for node in flow.nodes.values():
        if node.lumon is None:
            continue
        parent_enabled = flow.lumon.enabled if flow.lumon else False
        errors += _validate_lumon_config(
            node.lumon, f"node '{node.name}'", flow_file_dir, parent_enabled=parent_enabled
        )
    return errors

def _validate_lumon_config(
    cfg: LumonConfig, scope: str, flow_file_dir: Path | None, parent_enabled: bool
) -> list[FlowTypeError]:
    errors = []
    effective_enabled = cfg.enabled or (parent_enabled and cfg.enabled is not False)
    has_plugins = cfg.plugins is not None and len(cfg.plugins) > 0
    has_config = cfg.config_path is not None
    
    # L1: plugins or config require enabled
    if (has_plugins or has_config) and not effective_enabled:
        errors.append(FlowTypeError(
            f"L1: lumon.plugins/config at {scope} requires lumon.enabled = true"
        ))
    
    # L2: plugins and config mutex
    if has_plugins and has_config:
        errors.append(FlowTypeError(
            f"L2: lumon.plugins and lumon.config are mutually exclusive at {scope}"
        ))
    
    # L3: plugin names resolve
    if has_plugins:
        for name in cfg.plugins:
            if not _plugin_exists(name, flow_file_dir):
                errors.append(FlowTypeError(
                    f"L3: lumon plugin '{name}' at {scope} not found "
                    f"(looked in <flow_dir>/plugins/, ~/.flowstate/plugins/, and built-in)"
                ))
    return errors

def _plugin_exists(name: str, flow_file_dir: Path | None) -> bool:
    candidates = []
    if flow_file_dir:
        candidates.append(flow_file_dir / "plugins" / name)
    candidates.append(_default_data_dir() / "plugins" / name)
    candidates.append(lumon_plugin_dir() / name)  # built-in
    candidates.append(lumon_plugin_dir().parent / name)  # if structure differs
    return any(p.is_dir() for p in candidates)
```

(Use the same data-dir resolution logic that `lumon.py` uses, to stay consistent — import from `flowstate.config` and `flowstate.engine.context`.)

### Edge Cases
- Empty block `lumon { }` → `LumonConfig(enabled=False, plugins=None, config_path=None)` → equivalent to no block. Valid but no-op.
- `plugins = []` (empty list) → `plugins = ()` empty tuple → L1 doesn't fire (no plugins to validate), but `enabled` still required if anything else is set in the block.
- Block at node level with only `enabled = false`, flow has `enabled = true` → effectively disables lumon for the node. No L1/L2/L3 errors.
- Plugin name with hyphens or digits → allowed (matches `NAME` token pattern).

## Testing Strategy
- Parser: each new fixture parses to expected `LumonConfig`
- L1 test: `lumon { plugins = [a] }` (no enabled) → error
- L2 test: `lumon { enabled = true, plugins = [a], config = "x.json" }` → error
- L3 test: `lumon { enabled = true, plugins = [definitely_not_a_plugin] }` → error
- Mixed-syntax test: `lumon = true` plus `lumon { ... }` in the same flow → parse error
- Backward compat: every existing flat-syntax test still passes

## E2E Verification Plan

### Verification Steps
1. Convert `flows/agent_delegation.flow` (or write a new fixture) to use `lumon { enabled = true, plugins = ["filesystem"] }`. Add a `plugins/filesystem/` directory next to the flow file with a minimal plugin manifest.
2. Run `/check <file>` → passes.
3. Remove `enabled = true` from the block, re-run → L1 error.
4. Add `config = "x.json"` alongside `plugins`, re-run → L2 error.
5. Change a plugin name to `bogus`, re-run → L3 error.

## E2E Verification Log

### Post-Implementation Verification

Exercised the full parse + type-check path via `uv run flowstate check` against
each fixture under `tests/dsl/fixtures/` (with `tests/dsl/fixtures/plugins/sample/`
and `plugins/second_sample/` populated so L3 has real directories to resolve).
Output captured 2026-05-10.

**Valid block at flow level (TEST-37b.5 spirit):**
```
$ uv run flowstate check tests/dsl/fixtures/valid_lumon_block.flow
OK
exit=0
```

**Valid node-level block override (TEST-37b.6 spirit):**
```
$ uv run flowstate check tests/dsl/fixtures/valid_lumon_block_node_override.flow
OK
exit=0
```

**Mixed flat + block in the same scope is a parse error (TEST-37b.10):**
```
$ uv run flowstate check tests/dsl/fixtures/invalid_lumon_block_mixed.flow
Parse error: Parse error: in flow 'lumon_block_mixed': cannot mix the lumon { ... } block with flat lumon/sandbox attributes (lumon). Use the lumon block or the flat attributes, not both, within the same scope.
exit=1
```

**L1 — plugins without enabled (TEST-37b.7):**
```
$ uv run flowstate check tests/dsl/fixtures/invalid_lumon_block_l1.flow
Type error: FlowTypeError(rule='L1', message="lumon.plugins/config at flow 'lumon_block_l1' require lumon.enabled = true", location="flow 'lumon_block_l1'")
exit=1
```

**L2 — plugins and config in the same block (TEST-37b.8):**
```
$ uv run flowstate check tests/dsl/fixtures/invalid_lumon_block_l2.flow
Type error: FlowTypeError(rule='L2', message="lumon.plugins and lumon.config are mutually exclusive at flow 'lumon_block_l2' (pick the curated plugin allowlist OR the hand-written .lumon.json file)", location="flow 'lumon_block_l2'")
exit=1
```

**L3 — unknown plugin name (TEST-37b.9):**
```
$ uv run flowstate check tests/dsl/fixtures/invalid_lumon_block_l3.flow
Type error: FlowTypeError(rule='L3', message="lumon plugin 'definitely_not_a_plugin_xyz' at flow 'lumon_block_l3' not found (looked in /Users/theophanerupin/code/flowstate/tests/dsl/fixtures/plugins/definitely_not_a_plugin_xyz/, /Users/theophanerupin/.flowstate/plugins/definitely_not_a_plugin_xyz/, and the built-in flowstate plugin)", location="flow 'lumon_block_l3'")
exit=1
```

The L3 error message mentions all three lookup locations (per-flow plugins
dir, the global flowstate plugins dir, and the built-in flowstate plugin),
matching the sprint-contract requirement.

**Backward compat — every legacy flat-syntax fixture still parses and
type-checks cleanly:**
```
$ uv run flowstate check tests/dsl/fixtures/valid_lumon.flow
OK
$ uv run flowstate check tests/dsl/fixtures/valid_sandbox.flow
OK
```

**Unit-test totals:**
- `uv run pytest tests/dsl/` — 446 passed (previous baseline 406; +40 new tests for block parsing, L1, L2, L3, fixtures).
- `uv run ruff check src/flowstate/dsl/ tests/dsl/` — `All checks passed!`
- `uv run pyright src/flowstate/dsl/` — `0 errors, 0 warnings, 0 informations`.

## Completion Checklist
- [x] Unit tests written and passing
- [x] `/lint` passes (ruff, pyright)
- [x] Acceptance criteria verified
- [x] E2E verification log filled in with concrete evidence
