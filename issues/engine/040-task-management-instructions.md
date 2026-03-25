# [ENGINE-040] Inject task management instructions when `tasks=true`

## Domain
engine

## Status
done

## Priority
P1

## Dependencies
- Depends on: DSL-012
- Blocks: none (SERVER-015 can be developed in parallel against the same API contract)

## Spec References
- specs.md Section 14 — "Agent Subtask Management" (to be added)

## Summary
When a node has task management enabled (`tasks = true` at node level, or inherited from flow default), the engine injects a "Task Management" section into the agent's prompt. This section provides the Flowstate server's REST API base URL, the current task execution ID, and curl examples for creating, listing, and updating subtasks. In handoff mode, the predecessor's task execution ID is also included so agents can introspect previous agent's subtasks.

## Acceptance Criteria
- [ ] New `_use_tasks(flow, node) -> bool` helper mirrors `_use_judge` pattern
- [ ] `build_task_management_instructions(server_base_url, task_execution_id) -> str` function in `context.py`
- [ ] Task management instructions appended to prompt in `_create_task_execution()` when tasks enabled
- [ ] FlowExecutor accepts `server_base_url: str | None` constructor parameter
- [ ] Handoff prompts include predecessor task_execution_id for subtask introspection
- [ ] Instructions NOT appended for wait/fence nodes (they don't execute subprocesses)
- [ ] Existing tests pass (no regressions)

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/context.py` — Add `build_task_management_instructions()` function
- `src/flowstate/engine/executor.py` — Add `_use_tasks()` helper, `server_base_url` param, inject instructions in `_create_task_execution()`
- `src/flowstate/server/routes.py` — Pass `server_base_url` when constructing FlowExecutor
- `tests/engine/test_executor.py` — Add tests for task management prompt injection

### Key Implementation Details

**`_use_tasks(flow, node)` helper** (executor.py):
```python
def _use_tasks(flow: Flow, node: Node) -> bool:
    if node.tasks is not None:
        return node.tasks
    return flow.tasks
```
Same inheritance pattern as `_use_judge`.

**`build_task_management_instructions()`** (context.py):
```python
def build_task_management_instructions(
    server_base_url: str,
    run_id: str,
    task_execution_id: str,
    predecessor_task_execution_id: str | None = None,
) -> str:
```

The function returns a prompt section like:
```
## Task Management
You have a subtask management system. Use it to break your work into subtasks and track progress.

### Create a subtask
curl -s -X POST {base}/api/runs/{run_id}/tasks/{task_id}/subtasks \
  -H "Content-Type: application/json" \
  -d '{"title": "your subtask title"}'

### Update a subtask
curl -s -X PATCH {base}/api/runs/{run_id}/tasks/{task_id}/subtasks/{subtask_id} \
  -H "Content-Type: application/json" \
  -d '{"status": "in_progress"}'  # or "done"

### List your subtasks
curl -s {base}/api/runs/{run_id}/tasks/{task_id}/subtasks

### Query predecessor's subtasks (if available)
curl -s {base}/api/runs/{run_id}/tasks/{predecessor_id}/subtasks
```

The predecessor section is only included in handoff mode when `predecessor_task_execution_id` is provided.

**FlowExecutor changes**:
- Add `server_base_url: str | None = None` to `__init__()` (stored as `self._server_base_url`)
- In `_create_task_execution()`, after routing and cross-flow instructions, check `_use_tasks(flow, node)` and append task management instructions if enabled and `self._server_base_url` is set
- Pass `predecessor_task_id` through to the instruction builder in handoff mode

**Server wiring** (routes.py):
- When creating `FlowExecutor`, pass `server_base_url=f"http://{config.server_host}:{config.server_port}"` from the app config

### Edge Cases
- `server_base_url` is None (e.g., engine used without server) → skip task management instructions even if `tasks=true`
- Wait/fence nodes: `_create_task_execution` is not called for these, so they're already excluded
- Session mode: instructions should still be injected (agent needs them in the resumed session too)

## Testing Strategy
- Test `_use_tasks` inherits from flow when node.tasks is None
- Test `_use_tasks` uses node override when set
- Test `build_task_management_instructions` generates correct curl commands with URLs
- Test `build_task_management_instructions` includes predecessor section only when provided
- Test `_create_task_execution` appends instructions when tasks enabled
- Test `_create_task_execution` skips instructions when tasks disabled
- Mock-based: no real API calls needed

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
