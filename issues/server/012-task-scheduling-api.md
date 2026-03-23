# [SERVER-012] Task scheduling API + recurring task management

## Domain
server

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: STATE-008
- Blocks: UI-034

## Summary
Extend task submission API with scheduled_at and cron fields. Add endpoints for managing scheduled/recurring tasks.

## Acceptance Criteria
- [ ] POST /api/flows/{name}/tasks accepts optional scheduled_at and cron
- [ ] Scheduled tasks appear in task list with 'scheduled' status
- [ ] API validates cron expression format
- [ ] GET /api/tasks includes scheduled tasks

## Technical Design
- `src/flowstate/server/routes.py` — extend submit_task
- `src/flowstate/server/models.py` — add fields to SubmitTaskRequest

## Testing Strategy
- `uv run pytest tests/server/ -v`
