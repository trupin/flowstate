# [UI-045] Add "Re-run" button for completed/failed tasks

## Domain
ui

## Status
done

## Priority
P1

## Dependencies
- Depends on: SERVER-016
- Blocks: —

## Summary
Add a "Re-run" button that appears on tasks with terminal status (completed, failed, cancelled). Clicking it calls `POST /api/tasks/{task_id}/rerun` to duplicate the task and queue it for immediate execution. Button appears in two places: the TaskDetail page header and the TaskQueuePanel's recent tasks list.

## Acceptance Criteria
- [ ] "Re-run" button visible on completed/failed/cancelled tasks in TaskDetail page
- [ ] "Re-run" button (↻ icon) visible on recent tasks in TaskQueuePanel
- [ ] Clicking creates a new queued task and refreshes the task list
- [ ] Button NOT shown on running/queued/scheduled tasks
- [ ] Error feedback if the API call fails

## Technical Design

### Files to Modify
- `ui/src/api/client.ts` — Add `rerun(taskId)` method to `api.tasks`
- `ui/src/pages/TaskDetail.tsx` — Add "Re-run" button in header
- `ui/src/components/TaskQueuePanel/TaskQueuePanel.tsx` — Add ↻ button on recent tasks

### API Client
```typescript
rerun: (taskId: string) => post<QueuedTask>(`/api/tasks/${taskId}/rerun`),
```

### TaskDetail Button
In the header section, after the status badge, for terminal statuses only:
```tsx
{['completed', 'failed', 'cancelled'].includes(task.status) && (
  <button className="task-rerun-btn" onClick={handleRerun}>Re-run</button>
)}
```

### TaskQueuePanel Button
In the recent tasks section, add a small ↻ button per task.

## Testing Strategy
- Visual verification with Playwright
- Verify button appears only for terminal statuses
- Verify clicking creates a new task in the queue

## Completion Checklist
- [ ] API client method added
- [ ] Button in TaskDetail
- [ ] Button in TaskQueuePanel
- [ ] `/lint` passes
