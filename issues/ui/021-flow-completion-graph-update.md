# [UI-021] Graph UI does not update when flow completes — requires manual re-select

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-011, UI-008
- Blocks: none

## Summary
When a flow run completes, the graph visualization and run detail view do not automatically update to reflect the final state. The user has to manually click the run again in the sidebar "Recent Runs" panel to see the completed graph. The WebSocket should be delivering a `flow.completed` event that triggers a re-fetch of the run detail, updating the graph nodes to their final statuses.

## Acceptance Criteria
- [ ] When a flow completes, the graph nodes automatically update to show completed/failed status
- [ ] The run status in the sidebar updates from "running" to "completed" without manual interaction
- [ ] The control panel reflects the completed state (no more pause/cancel buttons)
- [ ] No need to re-select the run from the sidebar to see the final state

## Technical Design

### Files to Modify
- `ui/src/hooks/useFlowRunState.ts` (or equivalent) — ensure `flow.completed` and `flow.status_changed` WebSocket events trigger a run detail re-fetch
- `ui/src/components/RunDetail/` (or equivalent) — ensure the component re-renders when run state changes
- `ui/src/components/Sidebar/` — ensure the run list refreshes on completion events

### Key Implementation Details
- Check that the WebSocket hook processes `flow.completed` events
- Verify that the run detail hook/state re-fetches run data when status changes
- The graph nodes derive their status from `tasks[].status` in the run detail — if the re-fetch happens, the graph should update automatically
- Possible root cause: the WebSocket event might update a status field but not trigger a full run detail re-fetch, leaving task statuses stale

### Edge Cases
- Flow completes while user is on a different page — sidebar should still update
- Flow fails/pauses — same behavior expected (auto-update)
- Multiple concurrent runs — only the affected run should re-fetch

## Testing Strategy
- Start a flow run, observe the graph, wait for completion, verify graph updates automatically
- E2E test: start run, wait for completion event, assert final node statuses without re-navigation
