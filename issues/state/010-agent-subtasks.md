# [STATE-010] Agent subtask table, model, and repository CRUD

## Domain
state

## Status
done

## Priority
P1

## Dependencies
- Depends on: none
- Blocks: SERVER-015

## Spec References
- specs.md Section 14 — "Agent Subtask Management" (to be added)

## Summary
Add a new `agent_subtasks` table to the SQLite schema for tracking subtasks created by agents during node execution. Agents with task management enabled can create, update, and list subtasks via the REST API. Subtask data persists after node completion for auditing and introspection by downstream agents.

## Acceptance Criteria
- [ ] New `agent_subtasks` table created in schema migration
- [ ] `AgentSubtaskRow` Pydantic model with fields: id, task_execution_id, title, status, created_at, updated_at
- [ ] Repository method: `create_agent_subtask(task_execution_id, title) -> AgentSubtaskRow`
- [ ] Repository method: `list_agent_subtasks(task_execution_id) -> list[AgentSubtaskRow]`
- [ ] Repository method: `update_agent_subtask(subtask_id, status) -> AgentSubtaskRow | None`
- [ ] Repository method: `get_agent_subtask(subtask_id) -> AgentSubtaskRow | None`
- [ ] Subtask statuses: `todo`, `in_progress`, `done`
- [ ] All existing tests pass (no regressions)

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/database.py` — Add `agent_subtasks` table to schema
- `src/flowstate/state/models.py` — Add `AgentSubtaskRow` Pydantic model
- `src/flowstate/state/repository.py` — Add CRUD methods
- `tests/state/test_repository.py` — Add tests for subtask operations

### Key Implementation Details

**Schema** (`database.py`):
```sql
CREATE TABLE IF NOT EXISTS agent_subtasks (
    id TEXT PRIMARY KEY,
    task_execution_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'todo',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_execution_id) REFERENCES task_executions(id)
);
CREATE INDEX IF NOT EXISTS idx_agent_subtasks_task ON agent_subtasks(task_execution_id);
```

**Model** (`models.py`):
```python
class AgentSubtaskRow(BaseModel):
    id: str
    task_execution_id: str
    title: str
    status: str  # "todo", "in_progress", "done"
    created_at: str
    updated_at: str
```

**Repository** (`repository.py`):
- `create_agent_subtask(task_execution_id: str, title: str) -> AgentSubtaskRow` — generates UUID, sets timestamps, inserts row
- `list_agent_subtasks(task_execution_id: str) -> list[AgentSubtaskRow]` — returns all subtasks for a task execution, ordered by created_at
- `update_agent_subtask(subtask_id: str, status: str) -> AgentSubtaskRow | None` — updates status and updated_at, returns updated row or None if not found
- `get_agent_subtask(subtask_id: str) -> AgentSubtaskRow | None` — returns single subtask or None

Follow existing patterns: use `uuid.uuid4()` for IDs, `datetime.now(UTC).isoformat()` for timestamps, `_commit()` after writes.

### Edge Cases
- Creating a subtask with a non-existent `task_execution_id` — the FK constraint will raise; let it propagate as an error.
- Updating a non-existent subtask — return `None`.
- Listing subtasks for a task with none — return empty list.

## Testing Strategy
- Test `create_agent_subtask` creates a row with correct fields and defaults
- Test `list_agent_subtasks` returns subtasks in creation order
- Test `list_agent_subtasks` returns empty list for unknown task_execution_id
- Test `update_agent_subtask` changes status and updates `updated_at`
- Test `update_agent_subtask` returns None for non-existent subtask
- Test `get_agent_subtask` returns the correct row
- Test `get_agent_subtask` returns None for non-existent ID
- Use in-memory SQLite (`:memory:`) per testing conventions

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
