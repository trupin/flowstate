# [STATE-008] Add scheduling columns to tasks table

## Domain
state

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: ENGINE-030, SERVER-012

## Summary
Add `scheduled_at` and `cron_expression` columns to the tasks table. Add 'scheduled' to the task status enum. Add repository methods for scheduled task management.

## Acceptance Criteria
- [ ] Tasks table has scheduled_at TIMESTAMP and cron_expression TEXT columns
- [ ] Task status includes 'scheduled' (deferred, not yet in active queue)
- [ ] Repository: get_due_scheduled_tasks() returns tasks where scheduled_at <= now
- [ ] Repository: create_next_recurring_task() creates next occurrence from cron

## Technical Design
- `src/flowstate/state/schema.sql` — add columns, update CHECK constraint
- `src/flowstate/state/models.py` — add fields to TaskRow
- `src/flowstate/state/repository.py` — new methods

## Testing Strategy
- `uv run pytest tests/state/ -v`
