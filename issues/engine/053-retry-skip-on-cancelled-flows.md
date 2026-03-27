# [ENGINE-053] Support retry/skip on cancelled flows (re-create executor)

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: SERVER-018

## Spec References
- specs.md Section 6.1 — "Flow Run Lifecycle"
- specs.md Section 6.2 — "Task Execution Lifecycle"

## Summary
When a flow is cancelled, the executor's main loop exits and the RunManager removes the executor instance. If the user then clicks "Retry Task" or "Skip Task", there is no executor to process the request. The engine needs a way to reconstruct an executor for a cancelled flow and restart execution from a specific task, so that retry/skip work on terminal flows.

## Acceptance Criteria
- [ ] `FlowExecutor` has a `restart_from_task()` class method or instance method that sets up executor state and starts the main loop from a retried/skipped task
- [ ] The method re-parses the flow AST from the flow file, sets up data_dir, expanded prompts, and budget guard
- [ ] The flow run status transitions from `cancelled` → `running`
- [ ] The retried task gets a new task execution with incremented generation
- [ ] The executor main loop picks up the new pending task and executes it
- [ ] After the retried task completes, the executor continues with normal edge traversal (subsequent nodes run)

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — add `restart_from_task()` method

### Key Implementation Details

Add a method that reconstructs enough executor state to resume from a specific task:

```python
async def restart_from_task(
    self,
    flow: Flow,
    flow_run_id: str,
    task_execution_id: str,
    action: str,  # "retry" or "skip"
    parameters: dict | None = None,
) -> str:
    """Restart a cancelled/failed flow from a specific task.

    Sets up executor state (normally done in execute()) and then
    calls retry_task() or skip_task() before entering the main loop.
    """
```

This method needs to replicate the setup from `execute()`:
1. Set `self._flow`, `self._flow_run_id`, `self._db` references
2. Load the flow run from DB to get `data_dir`, `workspace`
3. Expand prompts for all nodes (`_expand_prompt()`)
4. Set up budget guard if flow has a budget
5. Un-cancel the flow: `self._db.update_flow_run_status(flow_run_id, "running")`
6. Call `self.retry_task()` or `self.skip_task()` to create the new task execution
7. Enter the main loop (`_run_main_loop()` or equivalent) to pick up pending tasks

The `retry_task()` method already handles:
- Creating a new task execution with incremented generation
- Adding to `self._pending_tasks`
- Resuming if paused

It needs a small change: handle the cancelled state in addition to paused (set `self._cancelled = False` and update flow status).

### Edge Cases
- Flow file may have been modified since the original run — re-parsing should use the version from when the run started (if stored) or warn if the flow has changed
- Budget: if the original budget is exhausted, the retried flow should reset the budget timer or use remaining budget
- Worktree: a new worktree may need to be created if the original was cleaned up during cancel
- Multiple retries: ensure each retry gets a unique generation number

## Testing Strategy
- Unit test: create executor, cancel flow, call `restart_from_task()`, verify flow resumes and task executes
- Unit test: verify skip_task via `restart_from_task()` creates the correct next task
- Unit test: verify flow status transitions: cancelled → running → completed

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
