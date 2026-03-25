# [SERVER-015] Subtask REST API endpoints + WebSocket events

## Domain
server

## Status
done

## Priority
P1

## Dependencies
- Depends on: STATE-010
- Blocks: UI-040

## Spec References
- specs.md Section 14 — "Agent Subtask Management" (to be added)
- specs.md Section 10 — "REST API" (new endpoints)

## Summary
Add REST API endpoints for agents to create, list, and update subtasks during node execution. Also emit WebSocket events when subtasks change so the UI can display real-time progress. These endpoints are called by agents via curl commands injected into their prompts by the engine.

## Acceptance Criteria
- [ ] `POST /api/runs/{run_id}/tasks/{task_execution_id}/subtasks` creates a subtask (201 response)
- [ ] `GET /api/runs/{run_id}/tasks/{task_execution_id}/subtasks` lists subtasks for a task execution
- [ ] `PATCH /api/runs/{run_id}/tasks/{task_execution_id}/subtasks/{subtask_id}` updates subtask status
- [ ] Request/response Pydantic models for subtask operations
- [ ] WebSocket `SUBTASK_UPDATED` event emitted on create and update
- [ ] Error handling: 404 for non-existent task execution or subtask, 400 for invalid status
- [ ] All existing tests pass (no regressions)

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/routes.py` — Add 3 new route handlers
- `src/flowstate/server/models.py` — Add request/response Pydantic models
- `src/flowstate/engine/events.py` — Add `SUBTASK_UPDATED` event type
- `tests/server/test_subtask_routes.py` — New test file for subtask endpoints

### Key Implementation Details

**Request/Response models** (`models.py`):
```python
class CreateSubtaskRequest(BaseModel):
    title: str

class UpdateSubtaskRequest(BaseModel):
    status: str  # "todo", "in_progress", "done"

class SubtaskResponse(BaseModel):
    id: str
    task_execution_id: str
    title: str
    status: str
    created_at: str
    updated_at: str
```

**Routes** (`routes.py`):

1. `POST /api/runs/{run_id}/tasks/{task_execution_id}/subtasks`:
   - Validate task_execution_id exists and belongs to run_id
   - Call `db.create_agent_subtask(task_execution_id, body.title)`
   - Emit `SUBTASK_UPDATED` WebSocket event
   - Return 201 with `SubtaskResponse`

2. `GET /api/runs/{run_id}/tasks/{task_execution_id}/subtasks`:
   - Call `db.list_agent_subtasks(task_execution_id)`
   - Return list of `SubtaskResponse`

3. `PATCH /api/runs/{run_id}/tasks/{task_execution_id}/subtasks/{subtask_id}`:
   - Validate status is one of `todo`, `in_progress`, `done`
   - Call `db.update_agent_subtask(subtask_id, body.status)`
   - Return 404 if subtask not found
   - Emit `SUBTASK_UPDATED` WebSocket event
   - Return 200 with `SubtaskResponse`

**WebSocket event** (`events.py`):
Add `SUBTASK_UPDATED = "subtask_updated"` to `EventType` enum. The event payload includes the full subtask data plus `flow_run_id` for routing.

Follow the existing pattern from `SERVER-014` (message endpoint) for route structure and WebSocket event emission.

### Edge Cases
- Agent creates subtask for a task_execution_id that doesn't exist → 404
- Agent sends invalid status value → 400 with descriptive error
- Agent creates many subtasks rapidly → no rate limiting for now (follow-up issue)
- Subtask API called after task completes → still works (subtasks persist, agents may use this for cleanup)

## Testing Strategy
- Test POST creates a subtask and returns 201 with correct fields
- Test GET returns empty list for task with no subtasks
- Test GET returns subtasks in creation order
- Test PATCH updates status and returns updated subtask
- Test PATCH returns 404 for non-existent subtask
- Test PATCH returns 400 for invalid status
- Test POST returns 404 for non-existent task_execution_id
- Use FastAPI TestClient with mocked FlowExecutor per server test conventions

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
