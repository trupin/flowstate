# [UI-031] Show DSL source in a popup modal instead of inline collapsible

## Domain
ui

## Status
done

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: UI-023
- Blocks: —

## Summary
The flow detail panel currently shows the DSL source in an inline collapsible `<pre>` block within the sidebar. This takes up vertical space and pushes other content down. Instead, clicking "Source DSL" should open a popup/modal window showing the full source code in a larger, more readable format — similar to how a code editor preview would look.

## Acceptance Criteria
- [ ] Clicking "Source DSL" opens a modal overlay with the full flow source
- [ ] The modal is large enough to read comfortably (e.g., 80% viewport width, 70% height)
- [ ] Source is displayed in monospace font with syntax highlighting (or at minimum, monospace `<pre>`)
- [ ] Modal has a close button (X) and closes on Escape or clicking outside
- [ ] The inline collapsible section is removed from FlowDetailPanel
- [ ] A "View Source" button replaces the collapsible toggle

## Technical Design

### Files to Modify
- `ui/src/components/FlowDetailPanel/FlowDetailPanel.tsx` — replace collapsible source section with a "View Source" button that opens the modal
- `ui/src/components/FlowDetailPanel/FlowDetailPanel.css` — add modal styles

### Key Implementation Details

Replace the current collapsible `<pre>` block with a button:
```tsx
<button className="view-source-btn" onClick={() => setShowSource(true)}>
  View Source
</button>

{showSource && (
  <div className="source-modal-overlay" onClick={() => setShowSource(false)}>
    <div className="source-modal" onClick={e => e.stopPropagation()}>
      <div className="source-modal-header">
        <h3>{flow.name}.flow</h3>
        <button onClick={() => setShowSource(false)}>×</button>
      </div>
      <pre><code>{flow.source_dsl}</code></pre>
    </div>
  </div>
)}
```

Modal CSS:
```css
.source-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.source-modal {
  width: 80vw;
  max-height: 70vh;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.source-modal pre {
  flex: 1;
  overflow: auto;
  padding: 16px;
  margin: 0;
  font-size: 13px;
}
```

## Testing Strategy
- Click "View Source" → modal opens with flow DSL
- Press Escape → modal closes
- Click outside → modal closes
- Verify source content matches the .flow file
