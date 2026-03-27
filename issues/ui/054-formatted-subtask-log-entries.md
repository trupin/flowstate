# [UI-054] Format subtask events as styled log entries

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
- specs.md Section 10.5 — "Log Viewer"

## Summary
When agents create or update subtasks (via `curl` tool calls to the subtask API), the log viewer shows raw tool call output. These should be rendered as formatted, styled log entries that clearly communicate subtask lifecycle events, e.g., "Subtask 'Read discussion' → in_progress".

## Acceptance Criteria
- [ ] Subtask creation events render as styled entries: "Created subtask: 'title'"
- [ ] Subtask status updates render as styled entries: "Subtask 'title' → done"
- [ ] The styled entries replace or augment the raw `curl` tool call output for subtask API calls
- [ ] Non-subtask tool calls are unaffected
- [ ] The entries are visually consistent with other log entry types (activity logs, tool calls)

## Technical Design

### Files to Create/Modify
- `ui/src/components/LogViewer/LogViewer.tsx` — detect subtask API responses in tool result logs and render formatted entries
- `ui/src/components/LogViewer/LogViewer.css` — subtask event entry styles

### Key Implementation Details

Subtask API calls appear in the log stream as `tool_result` entries with titles like:
```
curl -s -X POST http://127.0.0.1:9090/api/runs/{run_id}/tasks/{task_id}/subtasks ...
```

And the response body contains JSON like:
```json
{"id": "...", "title": "Read discussion", "status": "todo", ...}
```

For PATCH (update) calls:
```
curl -s -X PATCH http://127.0.0.1:9090/api/runs/{run_id}/tasks/{task_id}/subtasks/{id} ...
```

**Detection**: In the log rendering logic, check if a `tool_result` log entry's title matches the pattern `/subtasks` (subtask API URL). If it does:

1. Parse the response JSON from the tool result content
2. Determine if it's a create (POST) or update (PATCH) based on the URL method
3. Render a formatted entry instead of (or in addition to) the raw output

**Formatted rendering**:

```tsx
<div className="log-subtask-event">
  <span className="log-subtask-icon">{statusIcon}</span>
  <span className="log-subtask-text">
    {isCreate ? 'Created subtask: ' : 'Subtask '}
    <strong>{title}</strong>
    {!isCreate && ` → ${status}`}
  </span>
</div>
```

**CSS**:
```css
.log-subtask-event {
  padding: 2px 8px;
  color: var(--accent);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}
```

### Edge Cases
- Subtask API call that fails (non-JSON response) — fall back to raw tool call display
- Subtask list queries (`GET /subtasks`) — don't render as events, show as normal tool output
- Multiple subtask operations in quick succession — each gets its own formatted entry

## Testing Strategy
- Manual test: run a flow with subtasks, verify create/update events appear as formatted entries
- Manual test: verify non-subtask tool calls still render normally
- Manual test: verify failed subtask API calls fall back gracefully

## Completion Checklist
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (eslint)
- [ ] Acceptance criteria verified
