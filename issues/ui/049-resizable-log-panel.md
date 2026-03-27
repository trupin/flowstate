# [UI-049] Add draggable resize handle between graph and log panel

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
- specs.md Section 10.4 — Graph visualization layout

## Summary
The RunDetail page has a fixed flex ratio (3:2) between the graph canvas and the log viewer panel. Users should be able to drag the border between them to resize the panels. The graph (React Flow) should adapt dynamically to the new container size. The user's preferred split ratio should persist in localStorage.

## Acceptance Criteria
- [ ] A vertical drag handle appears on the border between the graph and log panel
- [ ] Dragging the handle resizes both panels proportionally
- [ ] The React Flow graph re-fits to its new container size during and after drag
- [ ] The resize handle shows a visual affordance on hover (cursor change, highlight)
- [ ] The user's preferred panel ratio persists across page loads (localStorage)
- [ ] Minimum width constraints prevent either panel from collapsing to zero
- [ ] The resize works correctly when the sidebar is open or closed

## Technical Design

### Files to Create/Modify
- `ui/src/pages/RunDetail.tsx` — add resize state, drag handlers, and resize handle element
- `ui/src/pages/RunDetail.css` — add `.resize-handle` styles, replace flex ratios with explicit widths during drag

### Key Implementation Details

**State management** in RunDetail.tsx:

```typescript
const [logPanelWidth, setLogPanelWidth] = useState<number | null>(() => {
  const saved = localStorage.getItem('flowstate-log-panel-width');
  return saved ? parseInt(saved, 10) : null;
});
const [isDragging, setIsDragging] = useState(false);
```

When `logPanelWidth` is null, use the default flex ratio (3:2). When set, apply explicit width.

**Drag handler**:

```typescript
const handleMouseDown = (e: React.MouseEvent) => {
  e.preventDefault();
  setIsDragging(true);
};

useEffect(() => {
  if (!isDragging) return;

  const handleMouseMove = (e: MouseEvent) => {
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const newLogWidth = rect.right - e.clientX;
    const clamped = Math.max(280, Math.min(newLogWidth, rect.width - 200));
    setLogPanelWidth(clamped);
  };

  const handleMouseUp = () => {
    setIsDragging(false);
    if (logPanelWidth) {
      localStorage.setItem('flowstate-log-panel-width', String(logPanelWidth));
    }
  };

  document.addEventListener('mousemove', handleMouseMove);
  document.addEventListener('mouseup', handleMouseUp);
  return () => {
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', handleMouseUp);
  };
}, [isDragging, logPanelWidth]);
```

**Layout**: Apply inline styles when dragging or when a saved width exists:

```tsx
<div className="run-detail-graph" style={logPanelWidth ? { flex: 'none', width: `calc(100% - ${logPanelWidth}px)` } : undefined}>
  ...
</div>
<div className="resize-handle" onMouseDown={handleMouseDown} />
<div className="run-detail-logs" style={logPanelWidth ? { flex: 'none', width: logPanelWidth } : undefined}>
  ...
</div>
```

**CSS** for resize handle:

```css
.resize-handle {
  width: 4px;
  cursor: col-resize;
  background: transparent;
  transition: background 0.15s;
  flex-shrink: 0;
}
.resize-handle:hover,
.resize-handle:active {
  background: var(--accent);
}
```

The React Flow canvas uses a `ResizeObserver` internally, so it will automatically re-fit when its container width changes.

### Edge Cases
- Window resize while the log panel has a fixed width — the graph panel should absorb the change
- Very narrow viewports — ensure minimum widths prevent layout breakage (min 200px graph, min 280px log)
- Double-click on handle could reset to default ratio (optional enhancement)

## Testing Strategy
- Manual test: drag the handle, verify both panels resize smoothly
- Manual test: refresh the page, verify the panel width is restored from localStorage
- Manual test: verify React Flow graph re-fits during drag
- Verify no layout breakage at various viewport sizes

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
