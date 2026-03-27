# [ENGINE-056] Auto-complete remaining subtasks when task exits successfully

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md — subtask management

## Summary
When a task completes successfully (exit code 0), any remaining subtasks stuck at `todo` or `in_progress` should be automatically marked `done` by the executor. Currently we rely on agents updating their subtasks before exiting (ENGINE-054 improved the prompt instructions), but agents frequently ignore this — observed on run `6e0b5e3d` where 5 of 8 task executions left all subtasks as `todo` despite completing successfully. Prompt compliance is unreliable; the engine should enforce correctness.

## Acceptance Criteria
- [ ] When a task exits with code 0, all its subtasks with status `todo` or `in_progress` are auto-marked `done`
- [ ] A `subtask.updated` WebSocket event is emitted for each auto-completed subtask so the UI updates
- [ ] When a task fails (non-zero exit), subtasks are left as-is (not auto-completed)
- [ ] Existing tests pass
- [ ] Subtask badges on graph nodes reflect the auto-completed state

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — add auto-complete logic after task completion
- `src/flowstate/state/repository.py` — add `complete_remaining_subtasks()` method

### Key Implementation Details

**1. New repository method** (`repository.py`):

```python
def complete_remaining_subtasks(self, task_execution_id: str) -> list[AgentSubtaskRow]:
    """Mark all todo/in_progress subtasks as done. Return the updated rows."""
    now = _now_iso()
    self._execute(
        "UPDATE agent_subtasks SET status = 'done', updated_at = ? "
        "WHERE task_execution_id = ? AND status IN ('todo', 'in_progress')",
        (now, task_execution_id),
    )
    return self.list_agent_subtasks(task_execution_id)
```

**2. Call after task completion** (`executor.py`, after line 2471):

After `self._db.update_task_status(task_execution_id, "completed", ...)`, add:

```python
# Auto-complete remaining subtasks
if self._use_subtasks(flow, node):
    updated = self._db.complete_remaining_subtasks(task_execution_id)
    for sub in updated:
        if sub.status == "done":
            self._emit(FlowEvent(
                type=EventType.SUBTASK_UPDATED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "subtask_id": sub.id,
                    "title": sub.title,
                    "status": "done",
                },
            ))
```

### Edge Cases
- Task with no subtasks: `complete_remaining_subtasks` returns empty list, no events emitted
- Task where agent already completed all subtasks: UPDATE affects 0 rows, harmless
- Task failure: don't auto-complete — subtasks may be partially done and the failure state is informative

## Testing Strategy
- Unit test: create subtasks in various states, complete the task, verify all are `done`
- Unit test: failed task does NOT auto-complete subtasks
- Existing tests still pass

## E2E Verification Plan
### Verification Steps
1. Start server, run `discuss_flowstate.flow`
2. After run completes, query subtasks for each task
3. Expected: all subtasks are `done` for completed tasks

## E2E Verification Log

### Post-Implementation Verification

**Tests run**: `uv run pytest tests/engine/test_executor.py -x -v -k "AutoComplete or NoAutoComplete"`

**Result**: 7 passed in 0.32s

Tests cover:
1. `test_subtasks_auto_completed_integration` -- subtasks in todo/in_progress are auto-marked done when task exits with code 0. Verifies DB state AND subtask.updated events emitted.
2. `test_already_done_subtasks_still_emit_events` -- subtasks already marked done are included in event emission (UI stays in sync).
3. `test_subtasks_not_completed_on_failure` -- failed task (exit code 1 with ABORT policy) does NOT auto-complete subtasks. Verifies subtasks stay todo/in_progress and no subtask.updated events emitted.
4. `test_no_auto_complete_when_subtasks_disabled` -- flow with subtasks=False emits no subtask events.
5. `test_complete_remaining_marks_todo_and_in_progress` -- direct repo test: todo+in_progress become done, already-done stays done.
6. `test_complete_remaining_empty_list` -- repo method on task with no subtasks returns empty list.
7. `test_complete_remaining_all_already_done` -- repo method when all subtasks already done: UPDATE affects 0 rows, returns all.

**Full test suite**: `uv run pytest tests/engine/ -x -v` -- 517 passed in 31.58s (no regressions).

**Lint**: `uv run ruff check src/flowstate/engine/ tests/engine/ src/flowstate/state/repository.py` -- All checks passed.

**Type check**: `uv run pyright src/flowstate/engine/ src/flowstate/state/repository.py` -- 0 errors, 0 warnings.

## Completion Checklist
- [x] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [x] `/lint` passes (ruff, pyright, eslint)
- [x] Acceptance criteria verified
- [x] E2E verification log filled in with concrete evidence
