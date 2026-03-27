# [UI-059] Condition popover renders behind graph nodes

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
The ConditionalEdge popover renders behind graph nodes because React Flow draws edges in a layer below nodes. CSS `z-index` on the popover cannot escape its parent stacking context (the edge label renderer layer). The popover must be portaled out of React Flow's DOM tree to render above everything.

## Acceptance Criteria
- [ ] Condition popover renders above all graph nodes when expanded
- [ ] Popover position is visually next to the "if" icon (right side preferred)
- [ ] Outside-click dismiss still works
- [ ] Popover stays positioned correctly when the graph is panned or zoomed

## Technical Design

### Files to Create/Modify
- `ui/src/components/GraphView/ConditionalEdge.tsx` — portal the popover to document.body
- `ui/src/components/GraphView/ConditionalEdge.css` — update popover to use fixed/absolute positioning relative to viewport

### Key Implementation Details

Use `createPortal` from `react-dom` to render the popover into `document.body`, escaping React Flow's stacking context entirely.

1. Get the icon's bounding rect via a ref when expanded
2. Portal the popover to `document.body` with fixed positioning based on the icon's viewport coordinates
3. Position the popover to the right of the icon (or left if near the right edge)
4. The outside-click handler already uses `document.addEventListener` so it will still work with the portal

```tsx
import { createPortal } from 'react-dom';

// In the component:
const iconRef = useRef<HTMLDivElement>(null);
const [popoverPos, setPopoverPos] = useState<{ top: number; left: number } | null>(null);

useEffect(() => {
  if (expanded && iconRef.current) {
    const rect = iconRef.current.getBoundingClientRect();
    setPopoverPos({ top: rect.top + rect.height / 2, left: rect.right + 8 });
  }
}, [expanded]);

// In the JSX — replace inline popover with portal:
{expanded && popoverPos && createPortal(
  <div
    className="conditional-edge-popover"
    style={{ position: 'fixed', top: popoverPos.top, left: popoverPos.left, transform: 'translateY(-50%)' }}
  >
    {condition}
  </div>,
  document.body
)}
```

Update the outside-click handler to also check clicks on the portaled popover element (add a ref to the popover div and check both refs in the handler).

### Edge Cases
- Graph pan/zoom while popover is open: close the popover on any React Flow viewport change (listen to `onMove` or close on scroll)
- Icon near right viewport edge: could position popover to the left instead, but acceptable to always go right for now

## Testing Strategy
- Build passes (`npm run build`)
- Visual check: popover appears in front of all nodes
- Click outside dismisses it

## E2E Verification Plan
### Verification Steps
1. Open a run with conditional edges
2. Click the "if" icon
3. Expected: popover appears to the right of the icon, fully visible above all nodes
4. Pan the graph — popover should dismiss

## E2E Verification Log

### Post-Implementation Verification
- **Build**: `cd ui && npm run build` passes cleanly (tsc + vite, 827 modules, built in 1.24s)
- **Lint**: `cd ui && npm run lint` passes with zero warnings/errors
- **Prettier**: `cd ui && npx prettier --check "src/**/*.{ts,tsx}"` reports all files formatted correctly
- **Changes made**:
  - `ConditionalEdge.tsx`: Added `createPortal` from `react-dom`, `useOnViewportChange` from `@xyflow/react`. Added `iconRef` on the "if" icon element and `popoverRef` on the portaled popover. Popover is now rendered via `createPortal(..., document.body)` with `position: fixed` computed from the icon's `getBoundingClientRect()`. Outside-click handler checks both `containerRef` (icon area) and `popoverRef` (portaled popover). `useOnViewportChange({ onStart })` closes the popover on any pan/zoom.
  - `ConditionalEdge.css`: Removed `position: absolute`, `top`, `left`, `transform` from `.conditional-edge-popover` (now handled via inline styles on the portaled element). Bumped `z-index` from 10 to 10000 to ensure visibility above all other UI elements. Kept all visual styles (background, border, padding, font, shadow, etc.).

## Completion Checklist
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
