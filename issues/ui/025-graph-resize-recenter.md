# [UI-025] Graph canvas does not resize and recenter when node detail panel opens

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: —

## Summary
When clicking on a node in the graph, the detail panel (log viewer) opens on the right side, reducing the available space for the graph canvas. However, the graph does not resize or recenter to fit within the reduced canvas bounds. Nodes may become partially hidden or off-screen, forcing the user to manually pan/zoom to see the full graph. The graph should automatically recenter and fit within the visible canvas area whenever the detail panel opens or closes.

## Acceptance Criteria
- [ ] Clicking a node opens the detail panel and the graph recenters/resizes to fit the remaining space
- [ ] Closing the detail panel causes the graph to recenter/resize to fill the full width
- [ ] All nodes remain visible after the panel opens (no clipping or off-screen nodes)
- [ ] The recenter transition is smooth (not jarring)
- [ ] Works for graphs of varying sizes (2 nodes to 10+ nodes)

## Technical Design

### Files to Modify
- `ui/src/components/GraphView/GraphView.tsx` — trigger `fitView()` when container resizes
- `ui/src/pages/RunDetail.tsx` — ensure the graph container element resizes when the panel opens/closes

### Key Implementation Details

React Flow provides a `fitView()` method via `useReactFlow()` that recenters and fits all nodes within the viewport. The fix:

1. **Detect container resize**: When the detail panel opens/closes, the graph container's width changes. Use a `ResizeObserver` or React Flow's `onResize` callback to detect this.

2. **Call `fitView()` after resize**: After the container width changes, call `fitView({ duration: 200 })` with a short animation duration for a smooth transition.

```typescript
import { useReactFlow } from '@xyflow/react';

function GraphView({ ... }) {
  const { fitView } = useReactFlow();
  const containerRef = useRef<HTMLDivElement>(null);

  // Re-fit when container size changes
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver(() => {
      // Small delay to let the layout settle
      setTimeout(() => fitView({ duration: 200, padding: 0.1 }), 50);
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [fitView]);
}
```

3. **Alternative**: Pass `selectedTask` as a prop to GraphView and trigger `fitView()` when it changes:
```typescript
useEffect(() => {
  fitView({ duration: 200, padding: 0.1 });
}, [selectedTask, fitView]);
```

### Edge Cases
- Opening/closing the panel rapidly (debounce the fitView call)
- Very small viewports where the graph can't fit even at minimum zoom
- Panel opening during an active layout animation

## Testing Strategy
- Click a node, verify graph recenters within the visible area
- Close the panel (click elsewhere or press Escape), verify graph expands to fill
- Test with a 2-node flow and a 5+ node flow
- Visual verification via Playwright screenshot comparison
