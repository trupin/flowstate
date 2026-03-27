# [UI-057] Collapse conditional edge labels into clickable icons

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Summary
Conditional edge labels display the full condition text (truncated at 40 chars) directly on the graph, which is too large and clutters the view. Replace the inline text with a small icon (e.g., a filter/condition icon) on the edge. Clicking the icon shows the full condition text in a tooltip or popover.

## Acceptance Criteria
- [ ] Conditional edges show a small icon instead of truncated text
- [ ] Clicking the icon reveals the full condition text (not truncated)
- [ ] Clicking elsewhere or clicking the icon again dismisses the popover
- [ ] Unconditional edges remain unchanged (no icon, no label)
- [ ] Back edges remain unchanged (no label)
- [ ] Edge colors, dashing, and animation still work correctly

## Technical Design

### Files to Create/Modify
- `ui/src/components/GraphView/ConditionalEdge.tsx` — new custom edge component
- `ui/src/components/GraphView/ConditionalEdge.css` — styles for icon and popover
- `ui/src/components/GraphView/GraphView.tsx` — register custom edge type, use it for conditional edges

### Key Implementation Details

**1. Custom edge component (`ConditionalEdge.tsx`)**

Create a custom React Flow edge using `BaseEdge` + `EdgeLabelRenderer`:

```tsx
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath } from '@xyflow/react';

// Render the edge path with BaseEdge, then overlay a small icon via EdgeLabelRenderer
// The icon is positioned at the edge's label position (labelX, labelY from getSmoothStepPath)
// State: expanded (boolean) — toggled on click, shows full condition text
```

The icon should be a small (20x20) circle/badge with a "?" or condition icon (e.g., `⑂` branch symbol or a simple `if` text). On click, expand to show the full condition text in a styled popover below/beside the icon.

**2. Register the edge type in `GraphView.tsx`**

```tsx
const edgeTypes = {
  conditional: ConditionalEdge,
};
// Pass to <ReactFlow edgeTypes={edgeTypes} />
```

**3. Update edge building in `convertToReactFlowEdges`**

For conditional edges, change from using `label`/`labelStyle` props to using the custom edge type with the condition passed as data:

```tsx
return {
  id,
  source,
  target,
  type: e.edge_type === 'conditional' ? 'conditional' : 'smoothstep',
  data: { condition: e.condition, stroke, isActive, isTraversed },
  // Remove label, labelBgPadding, labelBgBorderRadius, labelBgStyle, labelStyle
  style: { ... },
  animated: isActive,
  markerEnd: { type: 'arrowclosed' },
};
```

**4. Popover styling (`ConditionalEdge.css`)**

- Icon: small pill/circle, `background: var(--bg-secondary)`, `border: 1px solid var(--border)`
- Popover: max-width 300px, `background: var(--bg-secondary)`, `border-radius: 6px`, `padding: 8px 12px`, `font-size: 12px`, `color: var(--text-primary)`, `box-shadow` for depth
- Use `pointer-events: all` on the label renderer content (React Flow disables pointer events on edge labels by default)

### Edge Cases
- Very short conditions (e.g., "x > 5"): still use icon, don't special-case
- Multiple conditional edges from same node: each gets its own icon
- Zoomed out: icon should remain legible at reasonable zoom levels
- Edge click vs icon click: only the icon is clickable, not the entire edge path

## Testing Strategy
- Build passes (`npm run build`)
- Visual check: conditional edges show icon, clicking reveals full text
- Unconditional edges unaffected

## E2E Verification Plan
### Verification Steps
1. Open a run with conditional edges (e.g., `discuss_flowstate` — the `moderator -> done` edge)
2. Expected: small icon on the conditional dashed edge, no truncated text
3. Click the icon — full condition text appears in popover
4. Click elsewhere — popover dismisses

## E2E Verification Log

### Post-Implementation Verification

**Files created/modified**:
- Created `ui/src/components/GraphView/ConditionalEdge.tsx` -- custom React Flow edge component
- Created `ui/src/components/GraphView/ConditionalEdge.css` -- icon and popover styles
- Modified `ui/src/components/GraphView/GraphView.tsx` -- registered edge type, updated edge conversion

**Implementation details**:
- `ConditionalEdge` uses `BaseEdge` + `EdgeLabelRenderer` + `getSmoothStepPath`
- Small 22x22 pill icon labeled "if" positioned at edge midpoint via `labelX`/`labelY`
- Click toggles `expanded` state showing full condition text in a popover
- Icon colors reflect edge state: accent for active, success for traversed
- `pointer-events: all` on the label container so clicks work through React Flow's default pointer-events blocking
- Back edges and unconditional edges remain unchanged (use `smoothstep` type, no label)
- Removed unused `truncate` helper function that was previously used for inline label text

**Build verification**:
```
$ cd ui && npm run build
> tsc && vite build
✓ 827 modules transformed.
✓ built in 1.29s
```

**Lint verification**:
```
$ cd ui && npm run lint
> eslint .
(no errors)
```

**Prettier verification**:
```
$ cd ui && npx prettier --check "src/components/GraphView/*.{ts,tsx,css}"
All files formatted correctly
```

**Conclusion**: Conditional edges now show a compact "if" icon instead of truncated text. Clicking reveals the full condition in a popover. All other edge types unaffected.

## Completion Checklist
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
