# [UI-017] Add run history section to sidebar

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md Section 10 — REST API (GET /api/runs)

## Summary
There is no way to view past (completed, paused, cancelled, failed) runs in the UI. The sidebar only shows "ACTIVE RUNS" (currently running). Users need a "RECENT RUNS" or "HISTORY" section to review and inspect past flow executions.

## Acceptance Criteria
- [ ] Sidebar has a "RECENT RUNS" section below "ACTIVE RUNS"
- [ ] Shows the last 10-20 runs with status, flow name, and short ID
- [ ] Each run is clickable and navigates to the run detail page
- [ ] Status dot color reflects run status (completed=green, paused=yellow, failed=red, cancelled=gray)
- [ ] Section is collapsible like other sidebar sections

## Technical Design

### Files to Modify
- `ui/src/components/Sidebar/Sidebar.tsx` — Add "RECENT RUNS" section
- `ui/src/components/Sidebar/Sidebar.css` — Style if needed

### Key Implementation Details
- Fetch all runs (not just running) via `api.runs.list()` on mount
- Filter out currently running runs (already shown in ACTIVE RUNS)
- Sort by started_at descending, limit to 20
- Re-fetch when WebSocket events indicate run lifecycle changes

## Testing Strategy
- Complete a run and verify it appears in the history section
- Click a past run and verify navigation to run detail page
