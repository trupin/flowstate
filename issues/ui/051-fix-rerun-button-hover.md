# [UI-051] Fix rerun button staying visible after hover in task queue

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
- specs.md Section 10.4 — UI interactions

## Summary
In the TaskQueuePanel's "RECENT" section, the rerun button (blue circular arrow) on task items stays permanently visible instead of only appearing on hover. The CSS has the correct `opacity: 0` base and `opacity: 1` on `.task-item:hover`, but the button remains visible on all items at all times. There is likely a CSS specificity conflict or an additional style overriding the opacity.

## Acceptance Criteria
- [ ] Rerun button is hidden by default (opacity 0) on task items in the RECENT section
- [ ] Rerun button appears on hover of the task item row
- [ ] Rerun button disappears when the mouse leaves the task item row
- [ ] The button remains clickable when visible on hover
- [ ] Other action buttons (cancel, remove, edit) also follow the same hover-to-show pattern

## Technical Design

### Files to Create/Modify
- `ui/src/components/TaskQueuePanel/TaskQueuePanel.css` — fix specificity conflict on `.task-action-btn` opacity

### Key Implementation Details

The current CSS (lines 117-131):
```css
.task-action-btn {
  opacity: 0;
  transition: opacity 0.1s;
}

.task-item:hover .task-action-btn {
  opacity: 1;
}
```

This should work. Investigate and fix the override — possible causes:

1. **The `.task-rerun-btn` has a `background` color** that makes it visible even at `opacity: 0` — check if the blue background is set via a more specific selector or inline style
2. **A parent element's style** is forcing visibility (e.g., `opacity: 1 !important` somewhere)
3. **The button has an explicit `opacity` set** in a more specific rule (e.g., `.task-rerun-btn { opacity: 1; }`)
4. **The `background: var(--accent)` on the button** may be set unconditionally — it should only apply on hover or be hidden via opacity

Check the full CSS cascade for `.task-rerun-btn` and `.task-action-btn`. If the blue background is always applied, either:
- Move the background color to the hover state: `.task-item:hover .task-rerun-btn { background: var(--accent); }`
- Or ensure `opacity: 0` on the base class has sufficient specificity

### Edge Cases
- Touch devices: buttons should remain visible after tap (no hover on mobile)
- Keyboard navigation: focused button should be visible
- Currently active/editing task items may need buttons always visible

## Testing Strategy
- Manual test: hover over task items in RECENT section, verify button appears only on hover
- Manual test: move mouse away, verify button disappears
- Manual test: click the button while visible, verify rerun triggers

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
