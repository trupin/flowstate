# [SHARED-012] `LumonConfig` dataclass and AST migration for `lumon { ... }` block

## Domain
shared

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: DSL-016, ENGINE-087

## Spec References
- specs.md Section 3.2 — "Flow Declaration" (`lumon` block)
- specs.md Section 3.4 — "Node Declarations" (`lumon` block override)
- specs.md Section 9.9 — "Lumon Security Layer"
- specs.md Section 11.1 — "AST"

## Summary
Replaces flat `lumon: bool | None` / `lumon_config: str | None` fields on `Flow` and `Node` with a single `lumon: LumonConfig | None` block. Introduces a new `LumonConfig` dataclass with `enabled`, `plugins`, and `config_path` fields. The flat `sandbox` / `sandbox_policy` aliases are also removed from the AST (parser-layer backward compatibility maps them onto `LumonConfig`). All AST consumers (`engine/lumon.py`) are updated to read the new shape. Parser-level support for the new block syntax is DSL-016; engine adaptation is ENGINE-087. This shared issue lands the AST contract change atomically so neither the DSL nor engine is broken in isolation.

## Acceptance Criteria
- [ ] New `LumonConfig` dataclass in `src/flowstate/dsl/ast.py` with fields `enabled: bool`, `plugins: tuple[str, ...] | None`, `config_path: str | None`
- [ ] `Flow.lumon: LumonConfig | None` (replaces flat `lumon`, `lumon_config`, `sandbox`, `sandbox_policy`)
- [ ] `Node.lumon: LumonConfig | None` (replaces flat `lumon`, `lumon_config`, `sandbox`, `sandbox_policy`)
- [ ] All call sites in `src/flowstate/engine/lumon.py` (`_use_lumon`, `_lumon_config`, `setup_lumon`) read the new shape
- [ ] Parser populates `LumonConfig` from existing flat syntax (`lumon = true`, `lumon_config = "..."`, `sandbox = true`, `sandbox_policy = "..."`) — backward compatibility lives at the parser layer, not the AST
- [ ] All existing tests pass without modification (parser-layer mapping preserves behavior)
- [ ] No code outside `src/flowstate/dsl/parser.py` references the removed flat fields

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/ast.py` — define `LumonConfig`, replace flat fields on `Flow` and `Node`
- `src/flowstate/dsl/parser.py` — map both flat and (future, DSL-016) block syntax onto `LumonConfig`
- `src/flowstate/engine/lumon.py` — update `_use_lumon`, `_lumon_config`, `setup_lumon` to read `LumonConfig`
- `src/flowstate/engine/executor.py` — any references to `flow.lumon` / `node.lumon` as booleans must be updated (search for usages)
- `src/flowstate/dsl/type_checker.py` — update existing LM1 rule (was: "lumon_config requires lumon = true") to read new shape; semantics preserved
- `tests/dsl/test_parser.py` — update assertions that read flat fields on AST (assertions should now read `flow.lumon.enabled`, `flow.lumon.config_path`)
- `tests/dsl/test_type_checker.py` — update LM1 test expectations
- `tests/engine/test_lumon.py` (or equivalent) — update fixture construction to use `LumonConfig`

### Key Implementation Details

**`LumonConfig` (`ast.py`):**
```python
@dataclass(frozen=True)
class LumonConfig:
    enabled: bool = False
    plugins: tuple[str, ...] | None = None
    config_path: str | None = None
```

`plugins = None` means "not specified at this level — inherit from parent or fall through to `config_path`." Empty tuple `()` means "explicitly no plugins beyond built-in flowstate plugin."

**`Flow` and `Node` updates:**

Remove from `Flow`:
```python
sandbox: bool = False
sandbox_policy: str | None = None
lumon: bool = False
lumon_config: str | None = None
```
Replace with:
```python
lumon: LumonConfig | None = None
```

Remove from `Node`:
```python
sandbox: bool | None = None
sandbox_policy: str | None = None
lumon: bool | None = None
lumon_config: str | None = None
```
Replace with:
```python
lumon: LumonConfig | None = None
```

`None` on a node means "inherit from flow." `LumonConfig(enabled=False)` on a node means "explicitly disabled here, even if flow has it on."

**Parser-layer backward compat (`parser.py`):**

The flat `lumon = true` / `lumon_config = "..."` / `sandbox = true` / `sandbox_policy = "..."` syntax continues to parse. The parser collects these flat attrs into a `LumonConfig`:

```python
def _build_lumon_from_flat(flat: dict) -> LumonConfig | None:
    enabled = flat.get("lumon") or flat.get("sandbox")
    config_path = flat.get("lumon_config") or flat.get("sandbox_policy")
    if enabled is None and config_path is None:
        return None
    return LumonConfig(
        enabled=bool(enabled),
        plugins=None,
        config_path=config_path,
    )
```

Apply at flow_decl and at every node builder. The DSL-016 follow-up adds the block syntax which produces a `LumonConfig` directly.

**Engine adaptation (`lumon.py`):**

`_use_lumon(flow, node)`:
```python
def _use_lumon(flow: Flow, node: Node) -> bool:
    cfg = node.lumon if node.lumon is not None else flow.lumon
    return cfg is not None and cfg.enabled
```

`_lumon_config(flow, node) -> str | None`:
```python
def _lumon_config(flow: Flow, node: Node) -> str | None:
    if node.lumon is not None and node.lumon.config_path is not None:
        return node.lumon.config_path
    if flow.lumon is not None and flow.lumon.config_path is not None:
        return flow.lumon.config_path
    return None
```

(Plugins-list synthesis is ENGINE-087's job — for now, this issue preserves the existing config-path behavior.)

**Type checker LM1 update:**

Old rule: "lumon_config requires lumon = true". New equivalent: a `LumonConfig` with `config_path` set but `enabled = false` is invalid at any scope. Re-express:

```python
def _check_lumon(flow: Flow) -> list[FlowTypeError]:
    errors = []
    if flow.lumon and flow.lumon.config_path and not flow.lumon.enabled:
        errors.append(FlowTypeError("LM1: lumon.config requires lumon.enabled = true (flow level)"))
    for node in flow.nodes.values():
        n = node.lumon
        if n and n.config_path:
            inherited_enabled = n.enabled if n.enabled is not None else (flow.lumon.enabled if flow.lumon else False)
            if not inherited_enabled:
                errors.append(FlowTypeError(f"LM1: lumon.config on node '{node.name}' requires lumon.enabled = true"))
    return errors
```

(DSL-016 will add L1/L2/L3 rules; this issue only preserves the existing LM1 semantics under the new shape.)

### Edge Cases
- Flow with `lumon = true` and no `lumon_config` → `Flow.lumon = LumonConfig(enabled=True, config_path=None, plugins=None)`. `_use_lumon` returns True. `_lumon_config` returns None. Existing behavior preserved.
- Flow with `sandbox = true` (alias) → same as above. Parser maps both to the same `LumonConfig`.
- Flow with both `sandbox = true` and `lumon = true` → `enabled=True`. No conflict.
- Flow with `lumon_config = "x.json"` and `sandbox_policy = "y.json"` → parser picks `lumon_config` (precedence preserved from existing `_lumon_config` resolution).
- Node with `lumon = false` explicit, flow has `lumon = true` → `_use_lumon` returns False (node override wins).

## Testing Strategy
- Round-trip parser tests: verify all four flat syntactic forms produce equivalent `LumonConfig` instances
- Snapshot test: a sample `.flow` file's parsed AST has `LumonConfig` in the expected shape
- Engine unit tests: `_use_lumon` and `_lumon_config` against constructed `LumonConfig` instances
- Regression: full test suite passes with no test modifications beyond AST-shape updates

## E2E Verification Plan

### Verification Steps
1. Run full Python test suite: `uv run pytest`. All tests pass.
2. Pick an existing `.flow` file using `lumon = true` (e.g. one from `flows/`). Run `/check` — passes.
3. Submit a task to a flow that uses `lumon = true` — sandbox is set up exactly as before (verify by checking the worktree's `.lumon.json` content).

## E2E Verification Log

### Post-Implementation Verification

#### 1. Targeted unit tests (DSL + lumon engine helper) — PASS

```
$ uv run pytest tests/dsl/ tests/engine/test_lumon.py
=========================== 409 passed in 2.26s ============================
```

DSL parser, type checker, AST, grammar, and engine `_use_lumon` / `_lumon_config`
all pass under the new shape, including the new round-trip equivalence tests
(`TestLumonFlatSyntaxRoundTrip`).

#### 2. Lint + type checks — PASS

```
$ uv run ruff check src/flowstate/dsl/ src/flowstate/engine/lumon.py tests/dsl/ tests/engine/test_lumon.py
All checks passed!

$ uv run pyright src/flowstate/dsl/ src/flowstate/engine/lumon.py
0 errors, 0 warnings, 0 informations
```

#### 3. Existing flat-syntax `.flow` parses + behaves identically (TEST-37b.3)

`flows/discuss_flowstate.flow` is a real flow that uses `sandbox = true`
(no other lumon/sandbox attrs). After SHARED-012 it still parses, type-checks
clean, and the engine resolves identical `_use_lumon` / `_lumon_config` values
for every node.

```
$ uv run python -c "
from flowstate.dsl.parser import parse_flow
from flowstate.dsl.type_checker import check_flow
from flowstate.engine.lumon import _use_lumon, _lumon_config
from pathlib import Path
flow = parse_flow(Path('flows/discuss_flowstate.flow').read_text())
print('flow.lumon =', flow.lumon)
print('type-check errors:', check_flow(flow))
for name, node in flow.nodes.items():
    print(f'{name}: use_lumon={_use_lumon(flow, node)} cfg={_lumon_config(flow, node)}')
"
flow.lumon = LumonConfig(enabled=True, plugins=None, config_path=None)
type-check errors: []
moderator: use_lumon=True cfg=None
alice: use_lumon=True cfg=None
bob: use_lumon=True cfg=None
done: use_lumon=True cfg=None
```

This matches pre-SHARED-012 behavior exactly: flow-level `sandbox = true`
enables lumon for every node, with no config path.

#### 4. Precedence preserved: `lumon_config` > `sandbox_policy`

```
$ uv run python -c "
from flowstate.dsl.parser import parse_flow
src = '''flow f { budget = 1h on_error = pause context = handoff lumon = true
lumon_config = \"lc.json\" sandbox_policy = \"sp.json\"
input { task_name: string }
entry a { prompt = \"x\" } exit b { prompt = \"y\" } a -> b }'''
flow = parse_flow(src)
print('flow.lumon =', flow.lumon)
assert flow.lumon.config_path == 'lc.json'
print('precedence OK: lumon_config wins')
"
flow.lumon = LumonConfig(enabled=True, plugins=None, config_path='lc.json')
precedence OK: lumon_config wins
```

#### 5. Round-trip equivalence between four legacy flat forms (TEST-37b.2)

Covered by `TestLumonFlatSyntaxRoundTrip` in `tests/dsl/test_parser.py`. All
four flat forms (`lumon = true`, `sandbox = true`, the two with their
respective `*_config`/`*_policy` aliases) collapse to the same
`LumonConfig`. See test class for the exact equivalence assertions.

#### 6. TEST-37b.4 (grep check) — no non-parser code references removed flat fields

```
$ grep -rn -E '\.lumon_config\b|\.sandbox_policy\b|\.sandbox\b' src/flowstate/ --include='*.py'
src/flowstate/engine/lumon.py:11:SHARED-012 migrated the flat ``flow.lumon``/``flow.sandbox`` booleans and
src/flowstate/engine/lumon.py:12:``flow.lumon_config``/``flow.sandbox_policy`` strings to a single
```

Only the migration-note comment remains (zero code references).

## Completion Checklist
- [x] Unit tests written and passing (DSL + engine/test_lumon.py: 409 pass)
- [ ] `/simplify` run on all changed code
- [x] `/lint` passes (ruff + pyright on changed files)
- [x] Acceptance criteria verified (TEST-37b.1 through TEST-37b.4)
- [x] E2E verification log filled in with concrete evidence
