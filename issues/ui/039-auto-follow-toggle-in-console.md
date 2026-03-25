# [UI-039] Add auto-follow toggle button in log viewer header

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section 10.5 — "Log Viewer"

## Summary
Once a user clicks a node in the graph, manual selection overrides auto-follow and there's no way to re-enable auto-follow from the log viewer. The user must click the selected node again in the graph to deselect it. Add a "Follow" toggle button in the log viewer header (next to "Details", "Show all", etc.) that re-enables auto-follow mode, clearing the manual selection.

## Acceptance Criteria
- [ ] A "Follow" button appears in the log viewer header when `isManualSelection` is true and at least one task is running
- [ ] Clicking "Follow" clears manual selection and resumes auto-follow (log viewer switches to the currently running task)
- [ ] The button is hidden when already in auto-follow mode (no manual selection)
- [ ] The button is hidden when no tasks are running (nothing to follow)
- [ ] When auto-follow is active (no manual selection), the existing auto-follow indicator/behavior works as before

## Technical Design

### Files to Modify

- `ui/src/hooks/useFlowRun.ts` — Expose a `clearManualSelection` callback that sets `isManualSelection = false` and `selectedTask = null`. Add it to the return object.

- `ui/src/components/LogViewer/LogViewer.tsx` — Add a new prop `onFollowClick?: () => void` and `showFollowButton?: boolean`. Render a "Follow" button in `.log-viewer-controls` when `showFollowButton` is true. Style it similar to the "Details" button.

- `ui/src/pages/RunDetail.tsx` — Compute `showFollowButton = isManualSelection && runningTaskNames.length > 0`. Pass `showFollowButton` and `onFollowClick={clearManualSelection}` to LogViewer.

- `ui/src/components/LogViewer/LogViewer.css` — Style for the Follow button (reuse `.log-viewer-details-btn` pattern or similar).

### Edge Cases
- User clicks Follow while a task is running → auto-follow kicks in immediately, shows the running task
- User clicks Follow but run just completed → button should already be hidden (no running tasks)
- User clicks a node after clicking Follow → manual selection overrides again (existing behavior)

## Testing Strategy
- `cd ui && npm run lint && npm run build`
- Manual: click a node, verify Follow button appears in log header, click it, verify auto-follow resumes
- E2E: start flow, click node, verify Follow button, click it, verify logs switch to running node

## Completion Checklist
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (eslint, prettier, tsc)
- [ ] Acceptance criteria verified
