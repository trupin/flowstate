# [SERVER-025] Expose lumon/sandbox settings in flow API responses

## Domain
server

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: UI-067

## Spec References
- specs.md Section 9.9 — "Lumon Sandboxing"
- specs.md Section 10.2 — "REST API"

## Summary
Expose `lumon`, `lumon_config`, `sandbox`, and `sandbox_policy` settings in the flow detail API response so the UI can display Lumon security indicators. The AST already contains these fields — they just need to be surfaced in the API response.

## Acceptance Criteria
- [ ] `GET /api/flows` response includes `lumon` and `sandbox` boolean for each flow
- [ ] `GET /api/flows/:id` response includes `lumon`, `lumon_config`, `sandbox`, `sandbox_policy`
- [ ] Per-node settings included in the flow's node data
- [ ] Tests verify the API response shape

## Technical Design

### Files to Modify

**`src/flowstate/server/routes.py`:**
The flow detail endpoint already serializes the AST. Check if `lumon`/`sandbox` fields are included in the response. If not, add them to the flow metadata extraction (around `_flow_to_frontend()`).

The AST fields are already on `Flow` and `Node` dataclasses (DSL-014), so they may already be serialized via `ast_json`. Verify and add explicit extraction if needed.

## Testing Strategy
- API test: create a flow with `lumon = true`, verify it appears in GET response

## E2E Verification Plan

### Verification Steps
1. Write a .flow file with `sandbox = true`
2. Start server, GET /api/flows
3. Verify response includes `lumon: true` or `sandbox: true`

## E2E Verification Log
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
