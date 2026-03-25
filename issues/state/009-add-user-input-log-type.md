# [STATE-009] Task message queue + interrupted status + user_input log type

## Domain
state

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: ENGINE-036, SERVER-014

## Spec References
- specs.md Section 8.1 — "SQLite Schema"

## Summary
Schema changes to support interactive agent messaging: (1) a `task_messages` table for queuing user messages per task execution, (2) `interrupted` as a valid task execution status, and (3) `user_input` as a valid log type. Since we don't need backward compatibility, DROP and recreate affected tables.

## Acceptance Criteria
- [ ] New `task_messages` table with columns: id, task_execution_id (FK), message (TEXT), created_at, processed (BOOLEAN default FALSE)
- [ ] `task_executions.status` CHECK constraint includes `'interrupted'`
- [ ] `task_logs.log_type` CHECK constraint includes `'user_input'`
- [ ] Repository methods: `insert_task_message()`, `get_unprocessed_messages()`, `mark_messages_processed()`
- [ ] UI TypeScript types updated: `TaskStatus` includes `'interrupted'`, `LogEntry.log_type` includes `'user_input'`

## Technical Design

### Files to Modify

- `src/flowstate/state/schema.sql` — DROP and recreate `task_executions` (add `'interrupted'` to status CHECK), `task_logs` (add `'user_input'` to log_type CHECK). Add new `task_messages` table.

- `src/flowstate/state/repository.py` — Add methods:
  - `insert_task_message(task_execution_id: str, message: str) -> str` — returns message ID
  - `get_unprocessed_messages(task_execution_id: str) -> list[TaskMessage]` — ordered by created_at
  - `mark_messages_processed(task_execution_id: str) -> int` — marks all as processed, returns count

- `src/flowstate/state/models.py` — Add `TaskMessage` Pydantic model.

- `ui/src/api/types.ts` — Add `'interrupted'` to `TaskStatus` union. Add `'user_input'` to log_type union.

### Key Implementation Details

```sql
CREATE TABLE IF NOT EXISTS task_messages (
    id TEXT PRIMARY KEY,
    task_execution_id TEXT NOT NULL REFERENCES task_executions(id),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    processed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_messages_task ON task_messages(task_execution_id, processed);
```

## Testing Strategy
- Unit test: insert message, retrieve unprocessed, mark processed
- Unit test: insert task log with `log_type='user_input'` succeeds
- Unit test: update task status to `'interrupted'` succeeds
- `uv run pytest tests/state/ && cd ui && npm run build`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
