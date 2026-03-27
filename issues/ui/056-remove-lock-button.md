# [UI-056] Remove lock button from graph controls

## Domain
ui

## Status
todo

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: none
- Blocks: none

## Summary
The React Flow `<Controls />` component renders a lock/unlock button for toggling graph interactivity. This button is not useful for Flowstate's read-only graph view. Remove it.

## Acceptance Criteria
- [ ] The lock button is no longer visible in the graph controls
- [ ] Zoom in (+), zoom out (-), and fit view buttons still work

## Technical Design

### Files to Create/Modify
- `ui/src/components/GraphView/GraphView.tsx` — add `showInteractive={false}` prop to `<Controls />`

### Key Implementation Details
At line 330, change `<Controls />` to `<Controls showInteractive={false} />`. This is a built-in React Flow prop that hides the lock button.

## Testing Strategy
- Build passes (`npm run build`)
- Visual check: controls show 3 buttons (no lock)

## E2E Verification Plan
### Verification Steps
1. Open any run page in the UI
2. Check the graph controls panel (bottom-right)
3. Expected: only +, -, and fit-view buttons visible

## E2E Verification Log

### Post-Implementation Verification

**Change**: Added `showInteractive={false}` prop to `<Controls />` in `GraphView.tsx` (line 328).

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

**Code review**: The `showInteractive` prop is a built-in React Flow Controls prop that hides the lock/unlock interactivity toggle button. Zoom in (+), zoom out (-), and fit-view buttons remain visible since they are controlled by separate props (`showZoom`, `showFitView`) which default to `true`.

**Conclusion**: Lock button is removed. Zoom and fit-view controls remain functional.

## Completion Checklist
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
