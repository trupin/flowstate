# [UI-029] Graph doesn't refit when nodes expand or log panel opens

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-025
- Blocks: —

## Summary
When clicking a node in the run detail graph, two things happen: (1) the node expands to show details (type, cwd, task_dir, directories), and (2) the log panel opens on the right, reducing the graph's available width. The ResizeObserver from UI-025 should trigger `fitView()` on container resize, but the graph doesn't visually recenter. The nodes remain at their original positions, and the expanded node can be partially clipped or the overall graph layout doesn't adjust to the reduced viewport.

**Root causes identified from screenshots:**
1. The ResizeObserver fires and calls `fitView()`, but `fitView` uses the **current node bounding boxes**. When a node expands (NodePill becomes larger), the bounding box changes but React Flow may not have re-measured the node dimensions before `fitView` runs.
2. The `requestAnimationFrame` debounce may fire before React Flow's internal layout engine has updated the node measurements after expansion.
3. On the flow library page, the graph is slightly off-center because `fitView` runs before the dagre layout has fully settled.

**Evidence:** Screenshots at `/tmp/flowstate-debug-4-node-clicked.png` and `/tmp/flowstate-debug-5-second-node.png` show the graph not recentering after node click + log panel open.

## Acceptance Criteria
- [ ] Clicking a node and opening the log panel causes the graph to refit within 500ms
- [ ] The expanded node is fully visible (not clipped by the log panel boundary)
- [ ] All other nodes remain visible after refit
- [ ] Switching between nodes (clicking different nodes) triggers refit each time
- [ ] Works on both RunDetail page and FlowLibrary page

## Technical Design

### Files to Modify
- `ui/src/components/GraphView/GraphView.tsx` — fix fitView timing after node expansion
- `ui/src/pages/RunDetail.tsx` — potentially trigger fitView when selectedTask changes

### Key Implementation Details

**Fix 1: Delay fitView after node expansion**

The ResizeObserver fires when the container resizes, but `fitView` needs to run AFTER React Flow has re-measured node dimensions. Add a longer delay:

```typescript
const observer = new ResizeObserver(() => {
  if (rafId !== undefined) cancelAnimationFrame(rafId);
  rafId = requestAnimationFrame(() => {
    // Wait for React Flow to re-measure nodes
    setTimeout(() => {
      fitView({ duration: 200, padding: 0.15 });
    }, 50);  // 50ms delay for node measurement
  });
});
```

**Fix 2: Trigger fitView when selectedTask changes**

Pass `selectedTask` or a `onNodeSelect` callback to GraphView so it knows when to refit:

```typescript
// In RunDetail.tsx, when selectedTask changes:
useEffect(() => {
  // The graph container size changes when log panel opens/closes
  // fitView is handled by ResizeObserver, but we need an additional
  // trigger for node expansion
}, [selectedTask]);
```

Or in GraphView, listen for `onNodeClick` and trigger fitView:
```typescript
const onNodeClick = useCallback(() => {
  setTimeout(() => fitView({ duration: 200, padding: 0.15 }), 100);
}, [fitView]);
```

**Fix 3: React Flow `nodesDraggable` and `fitView` on `onNodesChange`**

React Flow emits `onNodesChange` when node dimensions change. Listen for dimension changes:
```typescript
const onNodesChange = useCallback((changes) => {
  const hasDimensionChange = changes.some(c => c.type === 'dimensions');
  if (hasDimensionChange) {
    setTimeout(() => fitView({ duration: 200, padding: 0.15 }), 50);
  }
}, [fitView]);
```

### Edge Cases
- Rapid clicking between nodes — debounce fitView
- Node collapse (deselect) — should also trigger refit
- Initial page load with no selected node — don't animate

## Testing Strategy
- Click a node on run detail page, verify graph recenters with all nodes visible
- Click a different node, verify graph adjusts again
- Open flow library, verify graph is centered in the available space
- Screenshot comparison before/after click
