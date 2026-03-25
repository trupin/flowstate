# [UI-040] Subtask progress display in node details

## Domain
ui

## Status
done

## Priority
P1

## Dependencies
- Depends on: SERVER-015
- Blocks: none

## Spec References
- specs.md Section 14 — "Agent Subtask Management" (to be added)

## Summary
Display agent subtask progress in the log viewer header (node details area) when viewing a task that has subtasks. Shows a compact progress indicator (e.g., "3/5 subtasks done") with an expandable list showing each subtask's title and status. Updates in real time via the `SUBTASK_UPDATED` WebSocket event.

## Acceptance Criteria
- [ ] Subtask progress indicator visible in log viewer header when a task has subtasks
- [ ] Progress shows count format: "N/M subtasks" with a mini progress bar
- [ ] Expandable subtask list shows each subtask's title and status icon (todo: circle, in_progress: spinner/dot, done: checkmark)
- [ ] Real-time updates when `SUBTASK_UPDATED` WebSocket event arrives
- [ ] No subtask section shown when a task has zero subtasks
- [ ] Subtasks fetched on initial load via GET API call
- [ ] UI build succeeds with no TypeScript errors

## Technical Design

### Files to Create/Modify
- `ui/src/components/LogViewer/SubtaskProgress.tsx` — New component for subtask display
- `ui/src/components/LogViewer/SubtaskProgress.module.css` — Styles
- `ui/src/components/LogViewer/LogViewerHeader.tsx` — Integrate SubtaskProgress component
- `ui/src/hooks/useSubtasks.ts` — Hook for fetching and subscribing to subtask updates
- `ui/src/api.ts` — Add `fetchSubtasks` API function
- `ui/src/types.ts` — Add `Subtask` type

### Key Implementation Details

**Type** (`types.ts`):
```typescript
interface Subtask {
  id: string;
  task_execution_id: string;
  title: string;
  status: "todo" | "in_progress" | "done";
  created_at: string;
  updated_at: string;
}
```

**API** (`api.ts`):
```typescript
export async function fetchSubtasks(runId: string, taskExecutionId: string): Promise<Subtask[]>
```

**Hook** (`useSubtasks.ts`):
- Fetches subtasks on mount via `fetchSubtasks(runId, taskExecutionId)`
- Subscribes to WebSocket `SUBTASK_UPDATED` events
- Filters events by `task_execution_id` match
- Returns `{ subtasks, loading }` — re-fetches full list on any subtask event for simplicity

**Component** (`SubtaskProgress.tsx`):
- Renders nothing if subtasks is empty
- Shows compact progress: `"3/5 subtasks"` with inline progress bar
- Expandable (click to toggle) list of subtasks with status icons
- Status icons: `○` todo, `◉` in_progress (with CSS animation), `✓` done
- Colocated with LogViewer per project conventions

**Integration** (`LogViewerHeader.tsx`):
- Render `<SubtaskProgress>` below the existing node details when `taskExecutionId` is available
- Pass `runId` and `taskExecutionId` as props

### Edge Cases
- Task has no subtasks → component renders nothing (no empty state message)
- WebSocket disconnects → subtasks still visible from initial fetch; reconnection auto-resubscribes
- Task not yet started → no task_execution_id yet, skip rendering
- Many subtasks (>20) → scrollable list with max-height

## Testing Strategy
- Verify SubtaskProgress renders nothing when subtasks array is empty
- Verify SubtaskProgress renders correct count for mixed-status subtasks
- Verify progress bar width matches done/total ratio
- Verify `npm run build` succeeds with no TypeScript errors
- Manual verification: start a flow with `tasks = true`, observe subtask creation in UI

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
