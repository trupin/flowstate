# [UI-026] Flow library: detail panel as scrollable sidebar alongside a wide graph canvas

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: UI-023, UI-025
- Blocks: —

## Summary
The flow library page currently stacks the detail panel (settings, nodes, edges, params, recent runs, DSL source) on top of the graph preview, pushing the graph to the bottom where it has limited height and can be hard to see. Instead, the layout should place the detail panel as a side panel on the right and give the graph the full remaining width on the left. The graph should always be centered and fit within its canvas boundaries. This makes the graph the primary visual element (as it should be for understanding flow structure) with details accessible alongside.

## Acceptance Criteria
- [ ] FlowLibrary uses a horizontal split layout: graph (left, wide) + detail panel (right, scrollable sidebar)
- [ ] The graph canvas takes the majority of the width (~65-70%)
- [ ] The detail panel is scrollable independently (overflow-y: auto) with a fixed width (~30-35%)
- [ ] The graph is always centered and fits within the canvas (uses `fitView`)
- [ ] The graph auto-recenters when the window resizes
- [ ] On narrow viewports (< 900px), fall back to vertical stacking (responsive)
- [ ] The "Start Run" button remains easily accessible in the header

## Technical Design

### Files to Modify
- `ui/src/pages/FlowLibrary.tsx` — restructure JSX to horizontal split
- `ui/src/pages/FlowLibrary.css` — change layout from vertical to horizontal
- `ui/src/components/GraphView/GraphView.tsx` — ensure `fitView` is called on mount and container resize

### Key Implementation Details

**Layout change** — Replace the current vertical stack:
```
[Header]
[DetailPanel]  ← full width, pushes graph down
[Graph]        ← squeezed at bottom
```

With a horizontal split:
```
[Header — full width]
[Graph (flex: 1)  |  DetailPanel (width: 350px, scrollable)]
```

**CSS changes** in `FlowLibrary.css`:
```css
.flow-library-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.flow-library-graph {
  flex: 1;
  min-width: 0;
  position: relative;
}

.flow-library-detail-sidebar {
  width: 350px;
  flex-shrink: 0;
  overflow-y: auto;
  border-left: 1px solid var(--border);
}

/* Responsive: stack vertically on narrow screens */
@media (max-width: 900px) {
  .flow-library-body {
    flex-direction: column;
  }
  .flow-library-detail-sidebar {
    width: 100%;
    max-height: 40vh;
    border-left: none;
    border-top: 1px solid var(--border);
  }
}
```

**JSX restructure** in `FlowLibrary.tsx`:
```tsx
<div className="flow-library">
  <div className="flow-library-header">
    <h2>{selectedFlow.name}</h2>
    <button>Start Run</button>
  </div>
  <div className="flow-library-body">
    <div className="flow-library-graph">
      <GraphView nodes={...} edges={...} readOnly />
    </div>
    <div className="flow-library-detail-sidebar">
      <FlowDetailPanel flow={selectedFlow} />
    </div>
  </div>
</div>
```

**Graph centering** — ensure GraphView calls `fitView()` on mount and on container resize. This ties into UI-025 (ResizeObserver + fitView). Add `fitView` to the GraphView component:
```typescript
const { fitView } = useReactFlow();
useEffect(() => {
  // Fit on mount and when nodes change
  setTimeout(() => fitView({ padding: 0.15 }), 100);
}, [nodes, fitView]);
```

### Edge Cases
- Flow with many nodes (10+): graph should zoom out to fit, detail panel stays fixed width
- Flow with only 2 nodes: graph should center them, not stretch to fill
- Very long detail panel content (many params, long DSL): sidebar scrolls independently
- Window resize: graph recenters via ResizeObserver

## Testing Strategy
- Visual: select a flow, verify graph is on the left (wide) and details on the right (sidebar)
- Resize the browser window, verify responsive fallback at < 900px
- Select flows with different node counts, verify graph always fits and centers
- Verify detail panel scrolls independently from the graph
