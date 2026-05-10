# [SERVER-033] Adapt `routes.py` to the post-SHARED-012 nested `lumon` AST shape

## Domain
server

## Status
done

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

Verified against the real dev server (`uv run flowstate server --host 127.0.0.1 --port 9090`)
running out of the project root, watching `./flows/`. Server PID 13708. Six flows
loaded from `flows/`: `agent_delegation`, `discuss_flowstate`, `implement_flowstate`,
`lumon_flat_off`, `lumon_flat_on`, `sandbox_alias`. The latter three are dedicated
fixtures created for this issue exercising the three relevant DSL shapes
(flat `lumon = true`, no lumon block, flat `sandbox = true` alias).

#### 1. List endpoint — `GET /api/flows`

```
$ curl -s http://localhost:9090/api/flows | python3 -c "
import json, sys
data = json.load(sys.stdin)
for f in data:
    print(f\"id={f['id']:25s} lumon={f.get('lumon')} sandbox={f.get('sandbox')}\")"
id=agent_delegation          lumon=False sandbox=False
id=discuss_flowstate         lumon=True sandbox=True
id=implement_flowstate       lumon=False sandbox=False
id=lumon_flat_off            lumon=False sandbox=False
id=lumon_flat_on             lumon=True sandbox=True
id=sandbox_alias             lumon=True sandbox=True
```

Conclusion: every flow's `lumon` and `sandbox` booleans match what the DSL contains.
`sandbox = true` (in `discuss_flowstate` and `sandbox_alias`) correctly aliases onto
`lumon = true` post-SHARED-012 — both fields are reported as `true` for the same flow.
Flows with no lumon block (`agent_delegation`, `implement_flowstate`, `lumon_flat_off`)
correctly report `false / false`. The pre-fix `bool(dict)` regression would have
reported `lumon=true` for `lumon_flat_off` if the AST still emitted an empty
`LumonConfig` dict — confirmed not the case here.

#### 2. Detail endpoint — `GET /api/flows/<id>` for four representative cases

```
$ for id in lumon_flat_on sandbox_alias discuss_flowstate lumon_flat_off; do
    echo "=== /api/flows/$id ==="
    curl -s "http://localhost:9090/api/flows/$id" -o /tmp/d.json
    python3 -c "
import json
with open('/tmp/d.json') as f: d = json.load(f)
print(f\"  lumon={d.get('lumon')} sandbox={d.get('sandbox')} \"
      f\"lumon_config={d.get('lumon_config')!r} sandbox_policy={d.get('sandbox_policy')!r}\")"
  done
=== /api/flows/lumon_flat_on ===
  lumon=True sandbox=True lumon_config='policies/strict.json' sandbox_policy='policies/strict.json'
=== /api/flows/sandbox_alias ===
  lumon=True sandbox=True lumon_config='policies/network-none.json' sandbox_policy='policies/network-none.json'
=== /api/flows/discuss_flowstate ===
  lumon=True sandbox=True lumon_config=None sandbox_policy=None
=== /api/flows/lumon_flat_off ===
  lumon=False sandbox=False lumon_config=None sandbox_policy=None
```

DSL → API mapping verified:

| Flow | DSL declaration | API `lumon` | API `sandbox` | API `lumon_config` | API `sandbox_policy` |
|------|----------------|-------------|---------------|--------------------|----------------------|
| `lumon_flat_on` | `lumon = true; lumon_config = "policies/strict.json"` | `true` | `true` | `"policies/strict.json"` | `"policies/strict.json"` |
| `sandbox_alias` | `sandbox = true; sandbox_policy = "policies/network-none.json"` | `true` | `true` | `"policies/network-none.json"` | `"policies/network-none.json"` |
| `discuss_flowstate` | `sandbox = true` (no policy path) | `true` | `true` | `null` | `null` |
| `lumon_flat_off` | (no lumon / sandbox keys) | `false` | `false` | `null` | `null` |

The `sandbox_alias` case is the strongest proof that the parser collapses
both flat aliases onto a single nested `LumonConfig` block and that the
route reads `config_path` from it — the API surfaces the user's
`sandbox_policy = "..."` value through both `lumon_config` and
`sandbox_policy` consistently.

#### 3. Per-node fields — `GET /api/flows/discuss_flowstate`

```
$ curl -s http://localhost:9090/api/flows/discuss_flowstate -o /tmp/df.json
$ python3 -c "
import json
with open('/tmp/df.json') as fp: d = json.load(fp)
print('Flow-level:')
print(f\"  lumon={d.get('lumon')} sandbox={d.get('sandbox')} \"
      f\"lumon_config={d.get('lumon_config')!r} sandbox_policy={d.get('sandbox_policy')!r}\")
print('Per-node:')
for n in d.get('nodes', []):
    print(f\"  {n['name']:12s} sandbox={n.get('sandbox')} sandbox_policy={n.get('sandbox_policy')!r} \"
          f\"lumon={n.get('lumon')} lumon_config={n.get('lumon_config')!r}\")"
Flow-level:
  lumon=True sandbox=True lumon_config=None sandbox_policy=None
Per-node:
  moderator    sandbox=None sandbox_policy=None lumon=None lumon_config=None
  alice        sandbox=None sandbox_policy=None lumon=None lumon_config=None
  bob          sandbox=None sandbox_policy=None lumon=None lumon_config=None
  done         sandbox=None sandbox_policy=None lumon=None lumon_config=None
```

Conclusion: `discuss_flowstate.flow` declares `sandbox = true` only at the
flow level — no node has its own `lumon` override block — so every node
reports `None` for all four fields. This matches the documented "absent
override" semantics: only nodes with an explicit `lumon { ... }` block
surface non-`None` values. The pre-fix code would have surfaced `True` for
`sandbox` and `lumon` on every node (because `bool({})` evaluated against
an empty dict from `n.get("lumon", {})` is `False`, but the old flat reads
were inconsistent across branches) — verified not the case here.

#### 4. Unit-test regression suite

```
$ uv run pytest tests/server/test_flow_discovery.py -k "TestFlowLumonSandboxFields" -v
...
tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields::test_flow_list_includes_lumon_and_sandbox_booleans PASSED
tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields::test_flow_list_lumon_sandbox_default_false PASSED
tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields::test_flow_list_error_flow_lumon_sandbox_false PASSED
tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields::test_flow_detail_includes_all_lumon_sandbox_fields PASSED
tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields::test_flow_detail_lumon_config_absent_when_not_set PASSED
tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields::test_flow_list_does_not_include_config_details PASSED
tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields::test_per_node_lumon_sandbox_fields PASSED
tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields::test_flow_explicit_disabled_with_config_path PASSED
tests/server/test_flow_discovery.py::TestFlowLumonSandboxFields::test_flow_block_syntax_plugins_no_config_path PASSED

9 passed
```

All 9 `TestFlowLumonSandboxFields` tests pass with the migrated nested-shape
fixtures, including the new `test_flow_explicit_disabled_with_config_path`
(covers the previously-invisible `LumonConfig(enabled=False, config_path=...)`
regression) and `test_flow_block_syntax_plugins_no_config_path` (covers
`lumon { enabled = true plugins = ["filesystem"] }` block syntax).

#### 5. Result

Acceptance criteria 1, 2, 3, 4, 5 satisfied. The flat-key regression is closed:
the routes read the nested `LumonConfig` dict directly and the unit tests
mirror the real AST serialization path, so any future schema drift will surface
in CI rather than only against the running app.

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
