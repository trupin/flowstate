# [ENGINE-008] Executor — Pause/Resume/Cancel/Retry/Skip

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-005
- Blocks: SERVER-009

## Spec References
- specs.md Section 6.1 — "Flow Run Lifecycle" (status transitions)
- specs.md Section 6.2 — "Task Execution Lifecycle" (retry/skip/abort)
- specs.md Section 3.2 — "Flow Declaration" (on_error policies)
- specs.md Section 6.7 — "Concurrency Controls" (paused flows release semaphore slots)
- agents/03-engine.md — "Exported Interface" (pause/resume/cancel/retry_task/skip_task)

## Summary
Implement the flow control methods on `FlowExecutor`: pause, resume, cancel, retry_task, and skip_task. Also integrate the `on_error` policy (pause, abort, skip) that triggers automatically when a task fails. Pause lets current tasks finish but blocks new ones from starting. Resume picks up pending tasks and continues execution. Cancel kills running subprocesses and marks everything as cancelled. Retry re-executes a failed task with a new generation. Skip marks a failed task as skipped and continues via its first outgoing edge. These controls are exposed through the REST API and WebSocket, giving users interactive control over flow execution.

## Acceptance Criteria
- [ ] `async pause(flow_run_id: str) -> None` — sets internal pause flag, lets running tasks finish, updates flow status to `paused`
- [ ] `async resume(flow_run_id: str) -> None` — clears pause flag, picks up pending tasks, updates flow status to `running`
- [ ] `async cancel(flow_run_id: str) -> None` — kills all running subprocesses, marks running tasks as `failed`, marks flow as `cancelled`
- [ ] `async retry_task(flow_run_id: str, task_execution_id: str) -> None` — creates new task execution for the same node with incremented generation, marks it pending
- [ ] `async skip_task(flow_run_id: str, task_execution_id: str) -> None` — marks task as `skipped`, continues via first outgoing edge
- [ ] on_error=pause: flow pauses when a task fails, emitting `flow.status_changed` event
- [ ] on_error=abort: flow cancels when a task fails (kills running subprocesses, marks cancelled)
- [ ] on_error=skip: failed task is marked `skipped`, execution continues via first outgoing edge
- [ ] Pause during fork: running fork members finish, but join does not trigger until resume
- [ ] Resume after pause: pending tasks (including newly created from retry/skip) are picked up
- [ ] Cancel during fork: all running fork members are killed
- [ ] Retry creates a new task directory with the incremented generation
- [ ] Skip on a task in a fork group: treated as "completed" for join completion check
- [ ] `flow.status_changed` events are emitted for every status transition with old/new status and reason
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — add control methods and on_error integration
- `tests/engine/test_executor.py` — add control and error policy tests

### Key Implementation Details

#### Pause

```python
async def pause(self, flow_run_id: str) -> None:
    """Pause the flow. Let running tasks finish, don't start new ones."""
    self._paused = True
    # The main loop checks self._paused before launching new tasks.
    # Running tasks continue to completion.
    # After all running tasks finish, update the flow status.

    # Wait for currently running tasks to finish
    if self._running_tasks:
        await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
    self._running_tasks.clear()

    old_status = self._db.get_flow_run(flow_run_id).status
    self._db.update_flow_run_status(flow_run_id, "paused")
    self._emit(FlowEvent(
        type=EventType.FLOW_STATUS_CHANGED,
        flow_run_id=flow_run_id,
        timestamp=_now_iso(),
        payload={
            "old_status": old_status,
            "new_status": "paused",
            "reason": "User paused",
        },
    ))
```

#### Resume

```python
async def resume(self, flow_run_id: str) -> None:
    """Resume a paused flow. Pick up from where we left off."""
    self._paused = False
    self._db.update_flow_run_status(flow_run_id, "running")
    self._emit(FlowEvent(
        type=EventType.FLOW_STATUS_CHANGED,
        flow_run_id=flow_run_id,
        timestamp=_now_iso(),
        payload={
            "old_status": "paused",
            "new_status": "running",
            "reason": "User resumed",
        },
    ))

    # Re-enter the main loop: find all pending tasks and continue processing
    # Implementation depends on executor architecture. Options:
    # 1. The main loop is still alive (awaiting), and clearing _paused unblocks it
    # 2. Resume re-enters the main loop explicitly
    # Preferred: option 1 — use an asyncio.Event that the main loop waits on when paused
```

The main loop should be modified to wait on an `asyncio.Event` when paused:

```python
# In the main loop:
while pending or self._running_tasks:
    if self._paused:
        # Wait until resumed
        await self._resume_event.wait()
        self._resume_event.clear()
        continue

    if self._cancelled:
        break
    # ... rest of loop
```

And resume sets the event:

```python
async def resume(self, flow_run_id: str) -> None:
    self._paused = False
    self._resume_event.set()
    # ... status update and event emission
```

#### Cancel

```python
async def cancel(self, flow_run_id: str) -> None:
    """Cancel the flow. Kill all running subprocesses."""
    self._cancelled = True
    self._paused = False  # unblock if paused

    # Kill all running subprocesses
    for task_id, atask in list(self._running_tasks.items()):
        task_exec = self._db.get_task_execution(task_id)
        if task_exec.claude_session_id:
            await self._subprocess_mgr.kill(task_exec.claude_session_id)
        atask.cancel()

    # Wait for all tasks to finish cancellation
    if self._running_tasks:
        await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
    self._running_tasks.clear()

    # Mark all running/pending tasks as failed
    tasks = self._db.get_task_executions(flow_run_id)
    for task in tasks:
        if task.status in ("running", "pending", "waiting"):
            self._db.update_task_failed(task.id, "Flow cancelled")

    # Update fork groups
    groups = self._db.get_fork_groups(flow_run_id)
    for group in groups:
        if group.status == "active":
            self._db.update_fork_group_status(group.id, "cancelled")

    self._db.update_flow_run_status(flow_run_id, "cancelled")
    self._emit(FlowEvent(
        type=EventType.FLOW_STATUS_CHANGED,
        flow_run_id=flow_run_id,
        timestamp=_now_iso(),
        payload={
            "old_status": "running",
            "new_status": "cancelled",
            "reason": "User cancelled",
        },
    ))

    # Unblock the main loop if it's waiting
    self._resume_event.set()
```

#### Retry Task

```python
async def retry_task(self, flow_run_id: str, task_execution_id: str) -> None:
    """Retry a failed task. Creates new task execution with incremented generation."""
    old_task = self._db.get_task_execution(task_execution_id)
    if old_task.status != "failed":
        raise ValueError(f"Can only retry failed tasks, got status: {old_task.status}")

    new_gen = _get_next_generation(flow_run_id, old_task.node_name, self._db)
    flow_run = self._db.get_flow_run(flow_run_id)

    # Re-create task execution with new generation
    # Re-assemble the prompt (same as original but with new task_dir)
    new_task_dir = create_task_dir(flow_run.data_dir, old_task.node_name, new_gen)

    # Use the same prompt as the original task but with updated task_dir
    # (The prompt includes the task_dir path, so it needs updating)
    new_prompt = old_task.prompt_text.replace(old_task.task_dir, new_task_dir)

    new_task_id = str(uuid.uuid4())
    self._db.create_task_execution(
        id=new_task_id,
        flow_run_id=flow_run_id,
        node_name=old_task.node_name,
        node_type=old_task.node_type,
        status="pending",
        generation=new_gen,
        context_mode=old_task.context_mode,
        cwd=old_task.cwd,
        task_dir=new_task_dir,
        prompt_text=new_prompt,
    )

    # If the flow is paused (common after failure with on_error=pause),
    # the new pending task will be picked up on resume.
    # Add to pending set if the main loop is still running.
    self._pending_tasks.add(new_task_id)
```

#### Skip Task

```python
async def skip_task(self, flow_run_id: str, task_execution_id: str) -> None:
    """Skip a failed task and continue via first outgoing edge."""
    task = self._db.get_task_execution(task_execution_id)
    if task.status != "failed":
        raise ValueError(f"Can only skip failed tasks, got status: {task.status}")

    self._db.update_task_status(task_execution_id, "skipped")

    # Continue via first outgoing edge
    # Need access to the flow AST — stored in the executor's state
    outgoing = _get_outgoing_edges(self._flow, task.node_name)
    if outgoing:
        edge = outgoing[0]
        if edge.edge_type == EdgeType.UNCONDITIONAL and edge.target:
            next_task_id = self._create_task_execution(...)
            self._pending_tasks.add(next_task_id)

    # Check fork group completion (skipped counts as "done" for join purposes)
    fork_group = self._db.get_fork_group_for_member(task_execution_id)
    if fork_group:
        self._check_fork_join_completion(fork_group)
```

#### on_error Policy Integration

```python
def _handle_error(self, flow_run_id: str, task_exec, flow: Flow) -> None:
    """Apply the on_error policy to a failed task."""
    policy = flow.on_error

    if policy == ErrorPolicy.PAUSE:
        self._pause_flow(
            flow_run_id,
            f"Task '{task_exec.node_name}' failed: {task_exec.error_message}",
        )

    elif policy == ErrorPolicy.ABORT:
        # Cancel the flow
        asyncio.create_task(self.cancel(flow_run_id))

    elif policy == ErrorPolicy.SKIP:
        # Mark as skipped and continue
        self._db.update_task_status(task_exec.id, "skipped")
        outgoing = _get_outgoing_edges(flow, task_exec.node_name)
        if outgoing:
            edge = outgoing[0]
            if edge.target:
                next_task_id = self._create_task_execution(...)
                self._pending_tasks.add(next_task_id)
```

### Edge Cases
- **Pause with no running tasks**: Flow status immediately transitions to `paused`. No tasks to wait for.
- **Resume with no pending tasks**: Flow status transitions to `running`, but the main loop exits immediately (no work to do). Flow status should then be checked — it may be completed or stuck.
- **Cancel a paused flow**: Works correctly — the cancel method clears the paused flag and sets cancelled.
- **Retry a non-failed task**: Raises `ValueError`. Only failed tasks can be retried.
- **Skip a non-failed task**: Raises `ValueError`. Only failed tasks can be skipped.
- **Retry after abort**: The flow is cancelled, so the new pending task won't be picked up. The user must be aware that retry after cancel is a no-op (or the system should prevent it).
- **Skip a task in a fork group**: The skipped task counts as "done" for fork-join completion. If all other members are also completed/skipped, the join triggers.
- **Multiple concurrent pauses**: Idempotent — second pause is a no-op if already paused.
- **Cancel during judge invocation**: The judge subprocess is also a Claude Code process. It should be killed alongside task processes. The judge invocation should check the cancelled flag.
- **on_error=abort with multiple running tasks**: All running subprocesses are killed. All pending tasks are cancelled.
- **on_error=skip on exit node failure**: The exit node has no outgoing edges. Skipping it means the flow has no exit — it should complete (since the exit node was "processed").

## Testing Strategy

Add to `tests/engine/test_executor.py`:

1. **test_pause_during_execution** — Start a flow, pause while a task is running. Verify: running task completes, no new tasks start, flow status is `paused`.

2. **test_resume_after_pause** — Pause, then resume. Verify: pending tasks are picked up, flow continues to completion.

3. **test_cancel_flow** — Start a flow, cancel while running. Verify: subprocess kill is called, flow status is `cancelled`, task statuses are `failed`.

4. **test_cancel_paused_flow** — Pause, then cancel. Verify: flow status transitions paused -> cancelled.

5. **test_retry_failed_task** — Task fails. Call retry_task. Verify: new task execution with incremented generation, status `pending`, new task directory created.

6. **test_retry_non_failed_raises** — Retry a completed task. Verify: `ValueError` raised.

7. **test_skip_failed_task** — Task fails. Call skip_task. Verify: task status is `skipped`, next task is enqueued via first outgoing edge.

8. **test_skip_non_failed_raises** — Skip a completed task. Verify: `ValueError` raised.

9. **test_on_error_pause** — Flow with `on_error=pause`. Task fails. Verify: flow pauses, `flow.status_changed` event emitted.

10. **test_on_error_abort** — Flow with `on_error=abort`. Task fails. Verify: flow cancels, running subprocesses killed.

11. **test_on_error_skip** — Flow with `on_error=skip`. Task fails. Verify: task marked `skipped`, next task enqueued and executes.

12. **test_pause_during_fork** — Fork into 2 tasks. Pause while both are running. Verify: both finish, no join target created.

13. **test_resume_after_fork_pause** — After test_pause_during_fork scenario, resume. Verify: if both members completed, join triggers.

14. **test_skip_fork_member** — Fork member fails. Skip it. Verify: fork group join check treats skipped as done. If all members are completed/skipped, join triggers.

15. **test_cancel_kills_subprocesses** — During fork execution, cancel. Verify: `subprocess_mgr.kill()` is called for each running task's session_id.

16. **test_flow_status_change_events** — Verify `flow.status_changed` events are emitted for: created->running, running->paused, paused->running, running->cancelled, running->completed.

Use mock subprocess manager with configurable delays to control timing. Use `asyncio.Event` or `asyncio.sleep` in mock subprocesses to simulate long-running tasks.
