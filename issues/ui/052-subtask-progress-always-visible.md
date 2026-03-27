# [UI-052] Show subtask progress bar in log viewer without Details click

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section 10.5 — "Log Viewer"

## Summary
The subtask progress bar (`SubtaskProgress` component) is only visible when the user clicks the "Details" button in the log viewer header. Subtasks should be visible by default whenever a task has subtasks, without requiring the user to expand the details panel.

## Acceptance Criteria
- [ ] Subtask progress bar appears automatically when a task has subtasks (no Details click needed)
- [ ] The progress bar still renders nothing when there are no subtasks (no empty UI)
- [ ] The Details panel (node metadata) remains behind the Details button — only the subtask bar moves out
- [ ] Live updates still work (WebSocket `subtask.updated` events trigger re-fetch)

## Technical Design

### Files to Create/Modify
- `ui/src/components/LogViewer/LogViewer.tsx` — move `SubtaskProgress` outside `showDetails` guard

### Key Implementation Details

In `LogViewer.tsx`, change line ~1021 from:
```tsx
{showDetails && taskExecutionId && (
    <SubtaskProgress subtasks={subtasks} loading={subtasksLoading} />
)}
```

To:
```tsx
{taskExecutionId && (
    <SubtaskProgress subtasks={subtasks} loading={subtasksLoading} />
)}
```

The `SubtaskProgress` component already returns `null` when `subtasks.length === 0 && !loading`, so it won't show an empty bar.

### Edge Cases
- Task with no subtasks: component renders nothing (existing behavior)
- Task still loading subtasks: component renders nothing until data arrives (existing behavior)

## Testing Strategy
- Manual test: run a flow with `subtasks=true`, verify progress bar appears without clicking Details
- Manual test: verify progress bar updates in real-time as subtasks are created/completed

## Completion Checklist
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (eslint)
- [ ] Acceptance criteria verified
