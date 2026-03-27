# [UI-050] Fix Clear button in log viewer (onClear prop not wired)

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
The "Clear" button in the log viewer header does nothing when clicked. The `LogViewer` component accepts an `onClear` prop and the button calls it, but `RunDetail.tsx` never passes this prop. The button calls `undefined`, which is a no-op. The fix is to wire up an `onClear` handler that clears the displayed logs for the selected task.

## Acceptance Criteria
- [ ] Clicking "Clear" removes all log entries from the log viewer for the currently selected task
- [ ] The clear is client-side only — logs are not deleted from the server
- [ ] After clearing, new incoming logs (from WebSocket) still appear
- [ ] Switching tasks shows the full log history for the new task (clear only affects the previously selected task)
- [ ] If no task is selected, Clear does nothing (or is hidden)

## Technical Design

### Files to Create/Modify
- `ui/src/pages/RunDetail.tsx` — add `handleClear` callback and pass as `onClear` prop
- `ui/src/hooks/useFlowRun.ts` — add a `clearLogs(taskExecutionId)` function that removes entries from the `logs` Map

### Key Implementation Details

**In `useFlowRun.ts`**, expose a function to clear logs for a specific task:

```typescript
const clearLogs = useCallback((taskExecutionId: string) => {
  setLogs(prev => {
    const next = new Map(prev);
    next.set(taskExecutionId, []);
    return next;
  });
}, []);
```

Return `clearLogs` from the hook alongside `logs`, `tasks`, etc.

**In `RunDetail.tsx`**, wire it up:

```typescript
const { logs, clearLogs, ... } = useFlowRun(...);

function handleClear() {
  if (selectedTaskExecution?.id) {
    clearLogs(selectedTaskExecution.id);
  }
}

<LogViewer
  ...
  onClear={handleClear}
/>
```

Setting the logs array to `[]` (not deleting the key) ensures that new WebSocket `task.log` events still append correctly — the existing WebSocket handler pushes to the array for the task's key.

### Edge Cases
- Clearing logs for the orchestrator console — the OrchestratorConsole component has its own log management, so this only affects the LogViewer
- Race condition: clear + incoming WebSocket log at the same time — React state batching handles this safely

## Testing Strategy
- Manual test: open a completed run, click Clear, verify logs disappear
- Manual test: on a running task, click Clear, verify new logs still appear after clearing
- Manual test: clear one task's logs, switch to another task, verify the other task's logs are intact

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
