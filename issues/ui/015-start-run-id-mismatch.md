# [UI-015] Fix StartRunModal navigating to /runs/undefined after starting a run

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md Section 10 — REST API

## Summary
After clicking "Start Run", the frontend navigates to `/runs/undefined` because the API client types expect `{ id: string }` but the server returns `{ flow_run_id: string }`. This causes a 404 and the user never sees the run detail page.

## Acceptance Criteria
- [ ] After starting a run, the browser navigates to `/runs/<actual-run-id>`
- [ ] The run detail page loads correctly
- [ ] No `GET /api/runs/undefined` requests in server logs

## Technical Design

### Root Cause
- Server endpoint `POST /api/flows/{flow_id}/runs` returns `StartRunResponse(flow_run_id=run_id)` → `{ "flow_run_id": "..." }`
- Frontend API client declares the return type as `post<{ id: string }>()` and accesses `result.id`
- `result.id` is `undefined` because the field is actually `flow_run_id`

### Fix Options (pick one)
**Option A** (frontend fix): Change the API client type to match the server response:
- `ui/src/api/client.ts` line 69: change `post<{ id: string }>` to `post<{ flow_run_id: string }>`
- `ui/src/components/StartRunModal.tsx` line 67: change `result.id` to `result.flow_run_id`

**Option B** (server fix): Change the server response model to use `id`:
- `src/flowstate/server/models.py` line 17: rename `flow_run_id` to `id` in `StartRunResponse`
- `src/flowstate/server/routes.py` line 225: update `StartRunResponse(id=run_id)`

Option A is safer (no server API change).

### Edge Cases
- None — straightforward field name mismatch

## Testing Strategy
- Start a run via the UI and verify navigation to the correct run detail page
- Check server logs for no `GET /api/runs/undefined` requests
- E2E test: `tests/e2e/test_start_run.py` (if exists) should cover this flow
