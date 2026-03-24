# [UI-035] Move node details from expanded node pill to log viewer header

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
- specs.md Section 10.4 — "Graph Visualization"
- specs.md Section 10.5 — "Log Viewer"

## Summary
Remove the node expansion feature from the graph (clicking a node currently toggles an inline detail panel showing type badge, elapsed time, cwd, task_dir, worktree). Instead, show these details via a "Details" button in the log viewer header (next to "Show all", "Pinned", "Clear"). When clicked, the details appear above or inline within the log panel. This keeps the graph clean and compact while still providing node metadata when needed.

## Acceptance Criteria
- [ ] Clicking a node in the graph no longer expands it — nodes stay as compact pills at all times
- [ ] The log viewer header gains a "Details" button (next to the existing "Show all" / "Pinned" / "Clear")
- [ ] Clicking "Details" toggles a collapsible panel above the log entries showing: node type badge, elapsed time, cwd (clickable), task_dir (clickable), worktree_dir (clickable), "Not yet executed" if applicable
- [ ] The details panel uses the same `ClickablePath` component currently in NodePill
- [ ] The "Details" button only appears when a node is selected (i.e., `taskName` is set)
- [ ] Countdown timer for waiting nodes still works in the new location
- [ ] Node click still selects the node (updates logs) — just no expansion
- [ ] Graph layout is not disrupted by the change (dagre relayout was triggered by node dimension changes — this should simplify things)

## Technical Design

### Files to Modify

- `ui/src/components/NodePill.tsx` — Remove `expanded` state, remove `onClick` toggle, remove the entire `{expanded && (...)}` detail section. Keep `data-testid` and `data-status` attributes. The node click handler should only call the parent's `selectTask` callback (already wired through React Flow's `onNodeClick`).

- `ui/src/components/NodePill.css` — Remove `.node-pill.expanded`, `.node-pill-details`, `.node-pill-dirs`, `.node-pill-dir`, `.node-pill-dir-label`, `.node-pill-not-executed`, `.node-pill-countdown`, `.node-pill-type-badge`, `.node-pill-elapsed` styles.

- `ui/src/components/LogViewer/LogViewer.tsx` — Add a "Details" toggle button in `.log-viewer-controls`. When active, render a `<NodeDetails>` panel between the header and the log entries. The panel receives the selected task execution data (type, elapsed, cwd, taskDir, worktreeDir, waitUntil, status).

- `ui/src/components/LogViewer/LogViewer.css` — Add styles for the node details panel (`.log-viewer-details` with background, padding, border-bottom).

- `ui/src/pages/RunDetail.tsx` — Pass the selected task execution metadata to LogViewer as a new prop (e.g., `taskExecution`). This data is already available from `tasks.get(selectedTask)`.

### Key Implementation Details

The LogViewer currently receives `logs` and `taskName`. Add a new prop:
```tsx
interface LogViewerProps {
  logs: LogEntry[];
  taskName: string | null;
  taskExecution?: {  // NEW
    nodeType: string;
    elapsedSeconds: number | null;
    cwd: string | null;
    taskDir: string | null;
    worktreeDir: string | null;
    status: string;
    waitUntil: string | null;
  } | null;
  onClear?: () => void;
}
```

The "Details" button toggles a local `showDetails` state. The details panel reuses `ClickablePath` (move it from NodePill to a shared location or import directly).

### Edge Cases
- No node selected → "Details" button hidden, no details panel
- Node selected but not yet executed → show "Not yet executed" in details panel
- Waiting node → show countdown timer in details panel
- Switching between nodes → details panel updates immediately (no stale data)

## Testing Strategy
- Existing E2E tests that click nodes and check logs should still pass (node click still selects)
- Verify no expanded node state in graph after clicking
- Verify "Details" button appears in log header when node is selected
- `cd ui && npm run lint && npm run build`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
