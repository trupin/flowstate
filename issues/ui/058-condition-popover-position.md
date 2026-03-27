# [UI-058] Condition popover overlaps target node — open upward instead

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- None

## Summary
The ConditionalEdge popover always opens downward (`top: 28px`), which causes it to overlap the target node when the edge label is positioned close to it. Since edges flow top-to-bottom and the label sits between source and target, the popover should open upward (above the icon) to avoid covering nodes below.

## Acceptance Criteria
- [ ] Condition popover opens above the "if" icon, not below it
- [ ] Popover does not overlap the target node
- [ ] Popover is still readable and properly styled

## Technical Design

### Files to Create/Modify
- `ui/src/components/GraphView/ConditionalEdge.css` — change popover position from below to above

### Key Implementation Details
Change the `.conditional-edge-popover` positioning from `top: 28px` to `bottom: 28px`. This makes the popover expand upward from the icon instead of downward.

```css
.conditional-edge-popover {
  position: absolute;
  bottom: 28px;  /* was: top: 28px */
  left: 50%;
  transform: translateX(-50%);
  /* rest unchanged */
}
```

### Edge Cases
- Very long condition text: popover grows upward, which is fine since there's typically more space above (source node is further away than target)

## Testing Strategy
- Build passes (`npm run build`)
- Visual check: popover opens above the icon, doesn't overlap nodes

## E2E Verification Plan
### Verification Steps
1. Open a run with conditional edges (e.g., `discuss_flowstate`)
2. Click the "if" icon on a conditional edge
3. Expected: popover appears above the icon, does not overlap the target node

## E2E Verification Log
_Filled in by the implementing agent as proof-of-work._

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
