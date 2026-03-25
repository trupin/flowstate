# [ENGINE-048] QueueManager doesn't pass server_base_url to FlowExecutor — subtask instructions never injected

## Domain
engine

## Status
done

## Priority
P1

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section 4 — "Execution Engine"

## Summary
When tasks are submitted via the UI queue (the normal user flow), the `QueueManager._start_task()` method creates a `FlowExecutor` without passing `server_base_url`. This causes `_maybe_update_task_prompt()` to bail out at line 2480 (`if self._server_base_url is None: return`), so subtask management instructions (curl commands for creating/updating subtasks) are never appended to agent prompts.

The `routes.py` start_run endpoint (line 307) does pass `server_base_url`, but the QueueManager path — which is what the task queue uses — does not.

**Result**: Even with `subtasks = true` in the flow (e.g., `discuss_flowstate`), agents never receive instructions to create subtasks, so 0 subtasks are ever created, and the `SubtaskProgress` UI component (which works correctly) never renders.

## Acceptance Criteria
- [ ] `QueueManager._start_task()` passes `server_base_url` to `FlowExecutor`
- [ ] When a flow has `subtasks = true`, agents receive subtask management instructions in their prompts
- [ ] Agents create subtasks during execution (visible in DB and API)
- [ ] `SubtaskProgress` component renders in the LogViewer during/after task execution

## Technical Design

### Files to Modify
- `src/flowstate/engine/queue_manager.py` — Pass `server_base_url` to `FlowExecutor` constructor in `_start_task()` (line 146-153)

### Key Implementation Details
The `QueueManager.__init__` already receives `config` which has `server_host` and `server_port`. The fix is a one-liner:

```python
executor = FlowExecutor(
    db=self._db,
    event_callback=event_callback,
    harness=self._harness,
    max_concurrent=getattr(self._config, "max_concurrent_tasks", 4),
    worktree_cleanup=getattr(self._config, "worktree_cleanup", True),
    harness_mgr=self._harness_mgr,
    server_base_url=f"http://{self._config.server_host}:{self._config.server_port}",  # ADD THIS
)
```

### Edge Cases
- Config `server_host` might be `0.0.0.0` (listen on all interfaces) — agents need `127.0.0.1` to reach the server. Should use `127.0.0.1` as the agent-facing host when the listen host is `0.0.0.0`.

## Testing Strategy
- Unit test: verify `FlowExecutor` receives `server_base_url` when created by `QueueManager`
- E2E: run `discuss_flowstate` flow, verify subtasks appear in DB and UI

## Completion Checklist
- [ ] Fix applied
- [ ] Unit tests written and passing
- [ ] `/lint` passes (ruff, pyright)
- [ ] Acceptance criteria verified
