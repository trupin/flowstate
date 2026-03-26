# [SERVER-016] Add POST /api/tasks/{task_id}/rerun endpoint

## Domain
server

## Status
done

## Priority
P1

## Dependencies
- Depends on: —
- Blocks: UI-045

## Summary
Add an API endpoint that duplicates a completed/failed/cancelled task and queues the copy for immediate execution. The new task inherits the original's flow_name, title, description, and params but gets a fresh status (queued) and no timestamps.

## Acceptance Criteria
- [ ] `POST /api/tasks/{task_id}/rerun` creates a new task cloned from the original
- [ ] Returns 201 with the new task's full details
- [ ] Returns 400 if the original task is still running/queued (only terminal tasks can be rerun)
- [ ] Returns 404 if the task doesn't exist
- [ ] New task has status "queued", no scheduled_at, no cron
- [ ] Queue manager picks up the new task automatically

## Technical Design

### File to Modify
- `src/flowstate/server/routes.py` — Add new endpoint

### Endpoint
```
POST /api/tasks/{task_id}/rerun
Response: 201 { ...full task object... }
```

### Implementation
1. Fetch original task via `db.get_task(task_id)`
2. Validate terminal status (`completed`, `failed`, `cancelled`)
3. Call `db.create_task()` with:
   - `flow_name`: original's flow_name
   - `title`: `original.title` (or `"Re-run: {original.title}"`)
   - `description`: original's description
   - `params_json`: original's params_json
   - `priority`: 0
   - `status`: "queued"
4. Return new task

## Testing Strategy
- Unit test: POST rerun on completed task → 201, new task queued
- Unit test: POST rerun on running task → 400
- Unit test: POST rerun on nonexistent task → 404

## Completion Checklist
- [ ] Endpoint implemented
- [ ] Tests passing
- [ ] `/lint` passes
