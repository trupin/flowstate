# [UI-016] Sidebar active runs don't update when a new run starts

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
- specs.md Section 10 — WebSocket events

## Summary
The sidebar's "ACTIVE RUNS" section only fetches running runs once on mount via `useEffect([], [])`. When a user starts a new run (via the Start Run modal), the sidebar never re-fetches, so the new run doesn't appear. The user has no way to navigate to the running flow.

## Acceptance Criteria
- [ ] Starting a run immediately shows it in the sidebar under "ACTIVE RUNS"
- [ ] When a run completes or pauses, it's removed from the active runs list
- [ ] Sidebar re-fetches active runs when WebSocket events indicate run lifecycle changes

## Technical Design

### Files to Modify
- `ui/src/components/Sidebar/Sidebar.tsx` — Add periodic re-fetch or WebSocket-driven refresh of active runs

### Key Implementation Details
Option A: Poll active runs every 5 seconds while connected.
Option B: Use a global WebSocket listener for `flow.started`, `flow.completed`, `flow.paused` events to trigger re-fetch.
Option C: After `StartRunModal` navigates, trigger a re-fetch via a shared state/callback.

Option B is cleanest — the WebSocket hub already broadcasts global events. The Sidebar should subscribe and re-fetch when run lifecycle events arrive.

## Testing Strategy
- Start a run via the UI and verify it appears in the sidebar immediately
- Wait for run to complete and verify it disappears from active runs
