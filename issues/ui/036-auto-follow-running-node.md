# [UI-036] Auto-follow running node in log viewer when no manual selection

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
When no node is manually selected in the graph, the log viewer should automatically follow the currently executing node — showing its logs in real time as they stream in. When multiple nodes execute in parallel (fork-join), auto-select the first one alphabetically. Manual node clicks override auto-follow; deselecting (clicking the selected node again or clicking empty graph space) resumes auto-follow mode. The auto-selected node should be visually highlighted in the graph.

## Acceptance Criteria
- [ ] When a flow run starts and no node is selected, the log viewer auto-selects the first node that begins executing
- [ ] As execution progresses (node completes → next node starts), the log viewer follows to the newly running node
- [ ] When multiple nodes run in parallel, the first one (alphabetically by node name) is auto-selected
- [ ] Clicking a node manually overrides auto-follow — logs stay on that node even as other nodes start
- [ ] Deselecting a node (clicking the selected node again) resumes auto-follow mode
- [ ] The log viewer no longer shows "Select a node to view logs" during active execution — it always has a node selected
- [ ] When all tasks complete, the log viewer stays on the last auto-selected task (does NOT revert to empty state)
- [ ] The auto-selected/manually selected node is visually highlighted in the graph (e.g., border glow or ring)
- [ ] LogViewer state (showAll, pinned, showDetails) only resets on manual selection changes, NOT on auto-follow transitions
- [ ] Auto-follow does not cause render storms during high-throughput parallel execution

## Technical Design

### Files to Modify

- `ui/src/hooks/useFlowRun.ts` — Add `isManualSelection` boolean state. Modify `selectTask` to set `isManualSelection = true` on user click, `false` on deselect. Expose both `selectedTask` and `autoSelectedTask` separately. Track running task names in a derived state that only updates when task statuses actually change (not on every log event).

- `ui/src/pages/RunDetail.tsx` — Compute `effectiveTask = selectedTask ?? autoSelectedTask`. Pass to LogViewer and GraphView. Pass an `isAutoSelected` flag to LogViewer to control state reset behavior.

- `ui/src/components/NodePill.tsx` — Add `isSelected` to `NodePillData` interface. Apply a `.node-pill-selected` CSS class when the node is the effective selection.

- `ui/src/components/NodePill.css` — Add `.node-pill-selected` style with a visible border/glow effect.

- `ui/src/components/LogViewer/LogViewer.tsx` — Only reset `showAll`, `pinned`, and `showDetails` state when the task change is from a manual selection (not auto-follow). Accept an `isAutoFollow` prop to control this behavior.

### Key Implementation Details

**Performance-safe auto-selection** — The `tasks` Map creates a new reference on every WebSocket event (log, status change, etc.). A naive `useMemo` depending on `tasks` recalculates on every event. Instead, track running task names in a separate derived state:

```typescript
// Only update when task STATUSES change, not on every log event
const [runningTaskNames, setRunningTaskNames] = useState<string[]>([]);

// In applyEvent, when task.started/completed/failed fires:
// Update runningTaskNames (not on task.log events)

const autoSelectedTask: string | null = useMemo(() => {
  if (isManualSelection) return null;
  return runningTaskNames[0] ?? lastAutoSelectedTask;
}, [isManualSelection, runningTaskNames, lastAutoSelectedTask]);
```

**Last auto-selected task** — When all tasks finish, `runningTaskNames` becomes empty. Keep the last non-null auto-selected value to avoid reverting to empty state:

```typescript
const [lastAutoSelectedTask, setLastAutoSelectedTask] = useState<string | null>(null);
useEffect(() => {
  if (autoSelectedTask) setLastAutoSelectedTask(autoSelectedTask);
}, [autoSelectedTask]);
```

**LogViewer conditional reset** — Pass `isAutoFollow` prop. Only reset scroll/filter state when NOT auto-following:

```typescript
useEffect(() => {
  if (!isAutoFollow) {
    setPinned(true);
    setShowAll(false);
    setShowDetails(false);
  }
}, [taskName, isAutoFollow]);
```

### Edge Cases
- Run not yet started (no tasks running) → no auto-selection, show empty state
- All tasks completed → stay on `lastAutoSelectedTask`
- User selects a node, it completes, new node starts → stay on user's selection (manual override)
- User deselects → resume auto-follow to currently running node
- Run is paused → keep showing the paused/failed task
- Rapid task transitions in fork-join → alphabetical sort is deterministic; no flicker
- User opens RunDetail page mid-execution → auto-selects first running task immediately
- Retry a task → task becomes running again, auto-follow picks it up if no manual selection

## Regression Risks
- `selectedNode` prop is currently passed to GraphView but not used for visual highlighting — adding `.node-pill-selected` is additive
- LogViewer state reset behavior changes — existing tests that rely on reset-on-task-change may need updating
- E2E tests that click nodes should still work since manual click overrides auto-follow

## Testing Strategy
- `cd ui && npm run lint && npm run build`
- E2E with Playwright (headless=False):
  - Start a linear flow, verify logs auto-select first running node without clicking
  - Click a node manually, verify auto-follow stops
  - Click the selected node again (deselect), verify auto-follow resumes
  - Run a fork-join flow, verify alphabetically first parallel node is auto-selected
  - Let a run complete, verify logs stay on last task (not empty state)
- Verify no render storms: run a flow with parallel tasks and high log throughput, check for UI lag

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
