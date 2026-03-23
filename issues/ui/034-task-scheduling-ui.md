# [UI-034] Task scheduling UI — datetime picker + cron input

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-012
- Blocks: —

## Summary
Add scheduling options to the TaskModal: immediate (default), one-time (datetime picker), recurring (cron input).

## Acceptance Criteria
- [ ] TaskModal has "When" section: Immediate / Schedule for / Recurring
- [ ] "Schedule for" shows a datetime picker
- [ ] "Recurring" shows a cron expression input with examples
- [ ] Scheduled tasks show their scheduled_at in the task queue panel
- [ ] Recurring tasks show their cron expression

## Technical Design
- `ui/src/components/TaskModal/TaskModal.tsx` — add scheduling fields
- `ui/src/components/TaskQueuePanel/TaskQueuePanel.tsx` — show scheduled_at

## Testing Strategy
- `cd ui && npm run lint && npm run build`
