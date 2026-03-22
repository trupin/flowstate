# [UI-033] Re-run dagre layout when node dimensions change

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-029
- Blocks: —

## Summary
When a node is clicked and expands (showing type, elapsed, cwd, task_dir), the node becomes much larger but the dagre graph layout is NOT recalculated. The layout was computed once with the initial compact pill sizes, so expanded nodes overlap edges, create awkward spacing, and look broken. The graph should re-run the dagre layout algorithm whenever node dimensions change, then call `fitView` to recenter.

**Evidence**: Screenshot shows "implement" node expanded to ~3x the size of compact nodes, with edges crossing through it and uneven spacing to "verify" and "done" below.

## Acceptance Criteria
- [ ] When a node expands (click to show details), the entire graph re-layouts with correct spacing
- [ ] When a node collapses (deselect), the graph re-layouts back to compact spacing
- [ ] Edges route correctly around expanded nodes (no crossing through node body)
- [ ] The transition is smooth (animated fitView after layout)
- [ ] Layout recalculation is debounced (no jitter from rapid clicks)

## Technical Design

### Files to Modify
- `ui/src/components/GraphView/GraphView.tsx` — re-run dagre layout on dimension changes

### Root Cause
The dagre layout in `useLayoutedElements()` (or equivalent) runs once when nodes/edges change, but uses hardcoded node dimensions (e.g., width=150, height=40 for all nodes). When a node expands (NodePill shows details), React Flow updates the node's measured dimensions but dagre doesn't re-run — the positions stay fixed.

### Fix Approach

**Option A: Use React Flow's measured dimensions for dagre**

After React Flow measures nodes (via `onNodesChange` with `type === 'dimensions'`), re-run dagre with the actual measured dimensions:

```typescript
const onNodesChange = useCallback((changes) => {
  const hasDimensionChange = changes.some(c => c.type === 'dimensions');
  if (hasDimensionChange) {
    // Get current node dimensions from React Flow
    const rfInstance = reactFlowInstance;
    const currentNodes = rfInstance.getNodes();

    // Re-run dagre with actual measured dimensions
    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: 'TB', nodesep: 50, ranksep: 80 });

    for (const node of currentNodes) {
      g.setNode(node.id, {
        width: node.measured?.width ?? 150,
        height: node.measured?.height ?? 40,
      });
    }
    // ... add edges, run dagre.layout(g), update positions

    // Animate to new positions
    fitView({ duration: 300, padding: 0.15 });
  }
}, []);
```

**Option B: Track selected node and re-layout on selection change**

Since expansion is tied to selection, re-run layout when the selected node changes:

```typescript
useEffect(() => {
  // When selectedNode changes, nodes resize → re-layout after a delay
  const timer = setTimeout(() => {
    relayout();
    fitView({ duration: 300, padding: 0.15 });
  }, 100); // Wait for DOM measurement
  return () => clearTimeout(timer);
}, [selectedNode]);
```

**Recommended: Option A** — it's more general and handles any dimension change (not just selection).

### Key Detail: dagre needs real dimensions

The current dagre setup likely uses fixed dimensions:
```typescript
g.setNode(node.id, { width: 150, height: 40 });
```

After a node expands, its actual measured size might be 300x200. Dagre needs these real dimensions to compute correct positions.

### Edge Cases
- Multiple expanded nodes (if supported) — all should be accounted for
- Rapid expand/collapse — debounce the re-layout
- Layout animation during execution (nodes changing status) — don't re-layout on status changes, only on dimension changes

## Testing Strategy
- Click a node → verify graph re-layouts with proper spacing around expanded node
- Click another node → verify layout adjusts
- Click away (deselect) → verify layout returns to compact
- Visual: no edges crossing through node bodies
