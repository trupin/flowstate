# [SERVER-033] Adapt `routes.py` to the post-SHARED-012 nested `lumon` AST shape

## Domain
server

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SHARED-012
- Blocks: —

## Spec References
- specs.md Section 10 — Web API (flow listing endpoints)
- specs.md Section 11.1 — AST (post-SHARED-012 `LumonConfig` shape)

## Summary
After SHARED-012, `Flow.lumon` and `Node.lumon` serialize to nested dicts (`{"enabled": bool, "plugins": [...] | null, "config_path": str | null}`) instead of flat booleans / strings. `src/flowstate/server/routes.py` still reads the old flat keys (`ast_json.get("lumon")`, `n.get("sandbox_policy")`, etc.) and exposes them through `GET /api/flows` and `GET /api/flows/:id`. The flat reads now return `dict` objects (or `None`), which `bool()` coerces to `True` for any non-empty config — including `LumonConfig(enabled=False)`. This breaks the UI's lumon/sandbox badges and any API consumer that distinguishes lumon-enabled from lumon-disabled flows.

The server-side tests (`tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields`) pass only because their `ast_json` fixtures hardcode the old flat keys — the regression is invisible to the test suite but observable against the running app.

## Acceptance Criteria
- [ ] `GET /api/flows` and `GET /api/flows/:id` expose lumon/sandbox booleans + config paths that match what the AST actually contains (not the old flat-key reads)
- [ ] Backward-compat shape preserved: response JSON still includes top-level `lumon: bool`, `sandbox: bool`, and (in detail responses) `lumon_config: str | null`, `sandbox_policy: str | null` — UI consumers don't need to change
- [ ] Per-node entries in the flow detail response: `sandbox`, `sandbox_policy`, `lumon`, `lumon_config` reflect the node's effective `LumonConfig` (or `None` if the node has no override)
- [ ] Server test fixtures in `tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields` migrated to the nested shape so they exercise the real serialization path; the regression that hid this issue is closed
- [ ] All existing server tests pass

## Technical Design

### Files to Modify
- `src/flowstate/server/routes.py` — adapt per-node lumon reads (~lines 155-179) and flow-level lumon reads (~lines 207-216) to read the nested dict shape
- `tests/server/test_flow_discovery.py` — update `TestFlowLumonSandboxFields` fixtures to use the nested form

### Key Implementation Details

**Per-node read** (around lines 155-179, both `dict` and `list` branches):
```python
lumon_block = n.get("lumon") or {}
nodes_out.append(
    {
        "name": n.get("name", ""),
        "type": n.get("node_type", "task"),
        "prompt": n.get("prompt", ""),
        "cwd": n.get("cwd"),
        # Sandbox is an alias for lumon (post-SHARED-012)
        "sandbox": lumon_block.get("enabled"),
        "sandbox_policy": lumon_block.get("config_path"),
        "lumon": lumon_block.get("enabled"),
        "lumon_config": lumon_block.get("config_path"),
    }
)
```

`None` semantics: if a node has no `lumon` block at all, `n.get("lumon")` is `None`, so `lumon_block` is `{}`, and all four output fields are `None` — same as before. If a node has `lumon { enabled = false }` explicitly, `lumon_block.get("enabled")` is `False` — slightly different from "absent" but consistent with the explicit-override semantics.

**Flow-level read** (around lines 207-216):
```python
lumon_block = f.ast_json.get("lumon") or {}
lumon = bool(lumon_block.get("enabled", False))
sandbox = lumon
lumon_config = lumon_block.get("config_path")
sandbox_policy = lumon_config
```

(Sandbox is an alias for lumon post-SHARED-012; the flat alias keys no longer exist in the AST. Surface them as the same value.)

**Test fixture migration**:

Find each `TestFlowLumonSandboxFields` test that builds an `ast_json` dict like `{"lumon": True, "lumon_config": "x.json", ...}`. Replace with the nested form: `{"lumon": {"enabled": True, "plugins": None, "config_path": "x.json"}}`. The expected response assertions should not change (the API surface is preserved).

Add one new test that uses `{"lumon": {"enabled": False, "config_path": "x.json"}}` and asserts the response has `lumon: false, sandbox: false, lumon_config: "x.json"` — this is the case the old flat reads would have wrongly reported as `lumon: true`.

### Edge Cases
- `lumon: null` in `ast_json` (no block declared) → response `lumon: false, sandbox: false, lumon_config: null` (current behavior preserved)
- `lumon: {"enabled": true, "plugins": ["filesystem"]}` (block syntax, no config path) → response `lumon: true, sandbox: true, lumon_config: null`. UI doesn't surface plugins yet — that's a separate future enhancement
- `lumon: {"enabled": false}` (explicit disable) → response `lumon: false, sandbox: false, lumon_config: null`

## Testing Strategy
- Existing `TestFlowLumonSandboxFields` tests, migrated to nested fixtures, must still assert the same response shape
- New test for `{"enabled": false}` case — must report `lumon: false`
- Manual: start the dev server, hit `GET /api/flows` against a flow that uses `lumon = true` in source, confirm response has `lumon: true` and the `lumon_config` field reflects the actual path

## E2E Verification Plan

### Verification Steps
1. Start the dev server pointing at a directory containing `flows/agent_delegation.flow` (uses flat `lumon` syntax) and a new fixture with the block syntax
2. Hit `curl http://localhost:8000/api/flows` — expect `lumon: true, sandbox: true` for both fixtures
3. Hit `curl http://localhost:8000/api/flows/<id>` (detail) — expect `lumon_config` reflects the AST's `config_path`
4. Hit `curl http://localhost:8000/api/flows/<id>` for a flow with `lumon { enabled = false }` — expect `lumon: false`

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in: exact commands, observed output, confirmation fix works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
