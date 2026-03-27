# [UI-053] Show subtask count badge on graph node pills

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
- specs.md Section 10.4 — Graph visualization, node design

## Summary
Graph node pills should display a subtask count badge (e.g., "2/3 subtasks") when a task has subtasks. This gives users at-a-glance progress visibility directly on the graph, similar to the existing generation badge for cycle flows.

## Acceptance Criteria
- [ ] Nodes with subtasks show a badge displaying "done/total" count (e.g., "2/3")
- [ ] The badge updates live as subtasks are created and completed (WebSocket events)
- [ ] Nodes with no subtasks show no badge
- [ ] The badge is visually distinct from the generation badge (different color or icon)
- [ ] Badge appears on both running and completed task nodes

## Technical Design

### Files to Create/Modify
- `ui/src/components/GraphView/FlowNode.tsx` — add subtask badge rendering
- `ui/src/components/GraphView/FlowNode.css` — badge styles
- `ui/src/components/GraphView/GraphView.tsx` — pass subtask data to nodes
- `ui/src/pages/RunDetail.tsx` — pass subtask counts to GraphView

### Key Implementation Details

**Data flow**: The subtask counts need to flow from the `useSubtasks` hook (or a new lightweight hook) down to individual graph nodes.

Option A (simplest): Fetch subtasks per task when the node renders. Each `FlowNode` calls `useSubtasks(runId, taskExecutionId, subtaskVersion)`. This reuses existing infrastructure but makes N API calls (one per node).

Option B (efficient): Add a new API endpoint `GET /api/runs/{run_id}/subtask-summary` that returns `{task_execution_id: {done: N, total: M}}` for all tasks in one call. This is more efficient but requires a server change.

**Recommend Option A** for now — the number of nodes is small (typically <10), and the hook already caches results. Can optimize later if needed.

**Badge rendering** in `FlowNode.tsx`:

```tsx
{subtaskTotal > 0 && (
  <div className="node-subtask-badge">
    {subtaskDone}/{subtaskTotal}
  </div>
)}
```

**Badge CSS**:
```css
.node-subtask-badge {
  font-size: 10px;
  color: var(--accent);
  margin-top: 2px;
}
```

### Edge Cases
- Node re-entered via cycle (multiple generations) — show subtasks for the latest generation only (matching the task shown in the log viewer)
- Fork-join nodes — each fork member may have its own subtasks
- Nodes without subtask support — no badge (subtasks array is empty)

## Testing Strategy
- Manual test: run a flow with `subtasks=true`, verify badges appear on nodes as agents create subtasks
- Manual test: verify badges update in real-time
- Manual test: verify no badge on nodes without subtasks

## Completion Checklist
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (eslint)
- [ ] Acceptance criteria verified
