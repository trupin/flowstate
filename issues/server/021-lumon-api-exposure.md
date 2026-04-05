# [SERVER-021] Expose lumon settings in flow and node API responses

## Domain
server

## Status
superseded (by SERVER-025)

## Priority
P1 (important)

## Dependencies
- Depends on: DSL-014
- Blocks: UI-065

## Spec References
- specs.md Section 9.9 — "Lumon Security Layer"
- specs.md Section 10.2 — "REST API"

## Summary
Expose `lumon` and `lumon_config` in the REST API responses for flows and nodes. The `_flow_to_frontend()` function already serializes the full AST including sandbox/sandbox_policy — lumon/lumon_config should appear alongside them in the same structure.

## Acceptance Criteria
- [ ] `GET /api/flows` includes `lumon` field in flow metadata
- [ ] `GET /api/flows/{id}` includes `lumon` and `lumon_config` in flow-level AST JSON
- [ ] Node-level AST JSON includes `lumon` and `lumon_config` fields
- [ ] All existing tests still pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/routes.py` — update `_flow_to_frontend()` to include lumon fields

### Key Implementation Details

The `_flow_to_frontend()` function already serializes the AST to JSON via `ast_json`. Since the AST is serialized as a dict, the new `lumon` and `lumon_config` fields on Flow and Node dataclasses will be included automatically in the `ast_json` response once DSL-014 adds them.

Check if there are any explicit field extractions (like `harness` is extracted at line ~157) that need lumon added. If so, add:
```python
"lumon": flow_ast.lumon,
```
to the flow metadata dict.

### Edge Cases
- Flow with `lumon = false` (default) → field present with value `false`
- Flow with `lumon_config` but no `lumon` → would be caught by type checker, but API returns whatever the AST has

## Testing Strategy
- Unit test: verify API response includes lumon fields for a flow with lumon enabled
- Regression: run full server test suite

## E2E Verification Plan

### Verification Steps
1. Create a `.flow` file with `lumon = true` and `lumon_config = "security/strict.lumon.json"`
2. Start server: `uv run flowstate serve`
3. `curl http://localhost:9090/api/flows` — verify lumon field in response
4. `curl http://localhost:9090/api/flows/<id>` — verify lumon and lumon_config in AST JSON

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in: server restarted, exact commands, observed output, confirmation fix/feature works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
