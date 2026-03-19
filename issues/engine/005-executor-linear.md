# [ENGINE-005] Executor — Linear Flows

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-001, ENGINE-002, ENGINE-003, STATE-003, STATE-004
- Blocks: ENGINE-006, ENGINE-007, ENGINE-008, ENGINE-009, ENGINE-010, ENGINE-011

## Spec References
- specs.md Section 6.1 — "Flow Run Lifecycle"
- specs.md Section 6.2 — "Task Execution Lifecycle"
- specs.md Section 6.3 — "Execution Algorithm"
- specs.md Section 6.7 — "Concurrency Controls"
- specs.md Section 9.5 — "Task Directory Setup"
- agents/03-engine.md — "Execution Algorithm", "Exported Interface"

## Summary
Implement the core `FlowExecutor` class with the main execution loop that handles linear (sequential) flows — the simplest flow topology. A linear flow has an entry node, zero or more task nodes connected by unconditional edges, and an exit node. The executor creates the flow run record, expands template variables, enqueues the entry task, processes tasks through the main loop (create task directory, assemble context, launch subprocess, wait for completion, evaluate outgoing edges), and detects exit node completion. This issue establishes the executor skeleton that all subsequent executor issues (fork-join, conditional, control) extend.

## Acceptance Criteria
- [ ] File `src/flowstate/engine/executor.py` exists and is importable
- [ ] `FlowExecutor` class is implemented with:
  - `__init__(self, db: FlowstateDB, event_callback: Callable[[FlowEvent], None], max_concurrent: int = 4)`
  - `async execute(self, flow: Flow, params: dict[str, str | float | bool], workspace: str) -> str` — returns flow_run_id
- [ ] `execute` creates a flow run record in the DB with status `created`, then transitions to `running`
- [ ] Template variables in all node prompts are expanded using the provided params
- [ ] Entry node task is enqueued as the first pending task with generation=1
- [ ] Main loop processes ready tasks up to the semaphore limit
- [ ] For each task: creates task directory, assembles prompt based on context mode, launches subprocess, streams events
- [ ] On task completion: updates DB (status, elapsed_seconds, exit_code), checks budget
- [ ] Budget warnings are emitted as events when thresholds are crossed
- [ ] On budget exceeded: pauses the flow after current task completes
- [ ] Unconditional outgoing edges enqueue the next task
- [ ] Exit node completion triggers flow completion (status -> `completed`)
- [ ] On task failure: applies the flow's `on_error` policy (pause only in this issue; abort/skip in ENGINE-008)
- [ ] Flow run elapsed_seconds is updated in DB after each task
- [ ] Concurrency is controlled by `asyncio.Semaphore(max_concurrent)`
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — FlowExecutor implementation
- `tests/engine/test_executor.py` — tests (linear flow subset; fork-join and conditional added by later issues)

### Key Implementation Details

#### FlowExecutor Class Structure

```python
import asyncio
import uuid
from collections.abc import Callable
from flowstate.dsl.ast import Flow, Node, Edge, EdgeType, ContextMode, ErrorPolicy, NodeType
from flowstate.engine.budget import BudgetGuard
from flowstate.engine.context import (
    create_task_dir, build_prompt_handoff, build_prompt_session,
    build_prompt_none, expand_templates, get_context_mode, resolve_cwd,
    read_summary,
)
from flowstate.engine.subprocess_mgr import SubprocessManager, StreamEventType
from flowstate.engine.events import FlowEvent, EventType
from flowstate.state.repository import FlowstateDB


class FlowExecutor:
    def __init__(
        self,
        db: FlowstateDB,
        event_callback: Callable[[FlowEvent], None],
        subprocess_mgr: SubprocessManager,
        max_concurrent: int = 4,
    ) -> None:
        self._db = db
        self._emit = event_callback
        self._subprocess_mgr = subprocess_mgr  # injected for testability (E2E uses MockSubprocessManager)
        self._running_tasks: dict[str, asyncio.Task] = {}  # task_execution_id -> asyncio.Task
        self._paused = False
        self._cancelled = False
```

#### Main Execution Loop

```python
async def execute(
    self, flow: Flow, params: dict[str, str | float | bool], workspace: str
) -> str:
    flow_run_id = str(uuid.uuid4())
    data_dir = os.path.expanduser(f"~/.flowstate/runs/{flow_run_id}")

    # 1. Create flow run record
    self._db.create_flow_run(
        id=flow_run_id,
        flow_definition_id=...,  # looked up from DB
        status="created",
        default_workspace=workspace,
        data_dir=data_dir,
        params_json=json.dumps(params),
        budget_seconds=flow.budget_seconds,
        on_error=flow.on_error.value,
    )
    self._db.update_flow_run_status(flow_run_id, "running")
    self._emit(FlowEvent(
        type=EventType.FLOW_STARTED,
        flow_run_id=flow_run_id,
        timestamp=_now_iso(),
        payload={"status": "running", "budget_seconds": flow.budget_seconds},
    ))

    # 2. Expand templates in all node prompts
    expanded_prompts: dict[str, str] = {}
    for node_name, node in flow.nodes.items():
        expanded_prompts[node_name] = expand_templates(node.prompt, params)

    # 3. Initialize budget guard
    budget = BudgetGuard(flow.budget_seconds)

    # 4. Enqueue entry node
    entry_node = _find_entry_node(flow)
    entry_task_id = self._create_task_execution(
        flow_run_id, entry_node, generation=1, flow=flow,
        expanded_prompt=expanded_prompts[entry_node.name],
        data_dir=data_dir, context_mode=ContextMode.NONE,  # entry has no predecessor
    )

    # 5. Main loop
    pending: set[str] = {entry_task_id}
    completed_queue: asyncio.Queue[str] = asyncio.Queue()

    while pending or self._running_tasks:
        if self._paused or self._cancelled:
            break

        # Launch ready tasks (up to semaphore limit)
        ready = list(pending)  # For linear flows, all pending are ready
        for task_id in ready:
            if self._paused or self._cancelled:
                break
            pending.discard(task_id)
            await self._semaphore.acquire()
            atask = asyncio.create_task(
                self._execute_single_task(
                    flow_run_id, task_id, flow, expanded_prompts,
                    data_dir, budget, completed_queue,
                )
            )
            self._running_tasks[task_id] = atask

        # Wait for at least one task to complete
        if self._running_tasks and not completed_queue.qsize():
            completed_id = await completed_queue.get()
        elif completed_queue.qsize():
            completed_id = completed_queue.get_nowait()
        else:
            break

        self._running_tasks.pop(completed_id, None)
        self._semaphore.release()

        # Get task execution from DB
        task_exec = self._db.get_task_execution(completed_id)

        if task_exec.status == "failed":
            self._handle_error(flow_run_id, task_exec, flow)
            continue

        # Check for exit node
        node = flow.nodes[task_exec.node_name]
        if node.node_type == NodeType.EXIT:
            self._complete_flow(flow_run_id, budget)
            return flow_run_id

        # Evaluate outgoing edges
        outgoing = _get_outgoing_edges(flow, task_exec.node_name)
        if len(outgoing) == 1 and outgoing[0].edge_type == EdgeType.UNCONDITIONAL:
            edge = outgoing[0]
            assert edge.target is not None
            ctx_mode = get_context_mode(edge, flow)
            next_task_id = self._create_task_execution(
                flow_run_id, flow.nodes[edge.target],
                generation=task_exec.generation + 1 if _is_cycle(edge, ...) else 1,
                flow=flow,
                expanded_prompt=expanded_prompts[edge.target],
                data_dir=data_dir,
                context_mode=ctx_mode,
                predecessor_task_id=completed_id,
            )
            pending.add(next_task_id)

        # Budget check
        if budget.exceeded:
            self._pause_flow(flow_run_id, "Budget exceeded")
            break

    return flow_run_id
```

#### Single Task Execution

```python
async def _execute_single_task(
    self, flow_run_id: str, task_execution_id: str,
    flow: Flow, expanded_prompts: dict[str, str],
    data_dir: str, budget: BudgetGuard,
    completed_queue: asyncio.Queue[str],
) -> None:
    """Execute a single task subprocess and handle its output."""
    task_exec = self._db.get_task_execution(task_execution_id)
    node = flow.nodes[task_exec.node_name]

    # Update status to running
    self._db.update_task_status(task_execution_id, "running")
    start_time = time.monotonic()
    self._emit(FlowEvent(
        type=EventType.TASK_STARTED,
        flow_run_id=flow_run_id,
        timestamp=_now_iso(),
        payload={
            "task_execution_id": task_execution_id,
            "node_name": node.name,
            "generation": task_exec.generation,
        },
    ))

    try:
        # Assemble prompt and launch
        session_id = str(uuid.uuid4())
        if task_exec.context_mode == ContextMode.SESSION:
            # Resume mode
            stream = self._subprocess_mgr.run_task_resume(
                expanded_prompts[node.name], task_exec.cwd, session_id
            )
        else:
            stream = self._subprocess_mgr.run_task(
                task_exec.prompt_text, task_exec.cwd, session_id
            )

        # Stream events
        exit_code = None
        async for event in stream:
            # Store log
            self._db.insert_task_log(task_execution_id, event.type.value, event.raw)
            # Emit to UI
            self._emit(FlowEvent(
                type=EventType.TASK_LOG,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "log_type": event.type.value,
                    "content": event.raw,
                },
            ))
            if event.type == StreamEventType.SYSTEM and event.content.get("event") == "process_exit":
                exit_code = event.content.get("exit_code", -1)

        elapsed = time.monotonic() - start_time

        if exit_code == 0:
            self._db.update_task_completed(
                task_execution_id, exit_code=exit_code, elapsed_seconds=elapsed,
                claude_session_id=session_id,
            )
            # Budget tracking
            warnings = budget.add_elapsed(elapsed)
            for w in warnings:
                self._emit(FlowEvent(
                    type=EventType.FLOW_BUDGET_WARNING,
                    flow_run_id=flow_run_id,
                    timestamp=_now_iso(),
                    payload={
                        "elapsed_seconds": budget.elapsed,
                        "budget_seconds": budget.budget_seconds,
                        "percent_used": w,
                    },
                ))
            self._emit(FlowEvent(
                type=EventType.TASK_COMPLETED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "node_name": node.name,
                    "exit_code": exit_code,
                    "elapsed_seconds": elapsed,
                },
            ))
        else:
            error_msg = f"Task exited with code {exit_code}"
            self._db.update_task_failed(task_execution_id, error_msg, elapsed_seconds=elapsed)
            self._emit(FlowEvent(
                type=EventType.TASK_FAILED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "node_name": node.name,
                    "error_message": error_msg,
                },
            ))
    except Exception as e:
        elapsed = time.monotonic() - start_time
        self._db.update_task_failed(task_execution_id, str(e), elapsed_seconds=elapsed)
        self._emit(FlowEvent(
            type=EventType.TASK_FAILED,
            flow_run_id=flow_run_id,
            timestamp=_now_iso(),
            payload={
                "task_execution_id": task_execution_id,
                "node_name": node.name,
                "error_message": str(e),
            },
        ))
    finally:
        await completed_queue.put(task_execution_id)
```

#### Helper Functions

```python
def _find_entry_node(flow: Flow) -> Node:
    """Find the single entry node in a flow."""
    for node in flow.nodes.values():
        if node.node_type == NodeType.ENTRY:
            return node
    raise ValueError(f"Flow '{flow.name}' has no entry node")


def _get_outgoing_edges(flow: Flow, node_name: str) -> list[Edge]:
    """Get all outgoing edges from a node."""
    return [
        e for e in flow.edges
        if e.source == node_name
        or (e.edge_type == EdgeType.FORK and e.source == node_name)
    ]


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
```

#### Task Execution Record Creation

```python
def _create_task_execution(
    self, flow_run_id: str, node: Node, generation: int,
    flow: Flow, expanded_prompt: str, data_dir: str,
    context_mode: ContextMode,
    predecessor_task_id: str | None = None,
) -> str:
    """Create a task execution record and its task directory."""
    task_id = str(uuid.uuid4())
    task_dir = create_task_dir(data_dir, node.name, generation)
    cwd = resolve_cwd(node, flow)

    # Build the full prompt
    if context_mode == ContextMode.HANDOFF and predecessor_task_id:
        pred = self._db.get_task_execution(predecessor_task_id)
        summary = read_summary(pred.task_dir)
        prompt = build_prompt_handoff(node, task_dir, cwd, summary)
    elif context_mode == ContextMode.SESSION:
        prompt = build_prompt_session(node, task_dir)
    else:
        prompt = build_prompt_none(node, task_dir, cwd)

    self._db.create_task_execution(
        id=task_id,
        flow_run_id=flow_run_id,
        node_name=node.name,
        node_type=node.node_type.value,
        status="pending",
        generation=generation,
        context_mode=context_mode.value,
        cwd=cwd,
        task_dir=task_dir,
        prompt_text=prompt,
    )
    return task_id
```

### Edge Cases
- **Flow with only entry + exit (2 nodes, 1 edge)**: The entry node completes, its outgoing unconditional edge enqueues the exit node, exit completes, flow is complete.
- **Entry node fails**: The on_error policy applies. With `pause` (default in this issue), the flow pauses.
- **No outgoing edges from a non-exit node**: This should not happen — the type checker (rule S3) ensures all non-exit nodes have outgoing edges. But defensively, the executor should pause the flow if this occurs.
- **Exit node with outgoing edges**: The type checker (rule S4) forbids this. The executor stops when an exit node completes regardless.
- **Budget exceeded exactly at exit**: The exit node completion takes priority — the flow completes successfully rather than pausing for budget.
- **Subprocess crash during stream**: The exception handler catches it, marks the task as failed, and puts the task_execution_id on the completed queue.
- **Empty params dict**: Template expansion with empty dict leaves `{{var}}` as-is. The type checker validates param references, so this is only an issue if execution bypasses validation.
- **workspace is None**: `resolve_cwd` raises `CwdResolutionError` which propagates as a task failure.
- **Concurrent task launches bounded by semaphore**: For linear flows, tasks run one at a time (each depends on the previous). But the semaphore is still acquired/released correctly for when fork-join is added later.

## Testing Strategy

Create `tests/engine/test_executor.py` (linear flow tests; fork-join and conditional tests added by ENGINE-006 and ENGINE-007):

1. **test_linear_3_node_flow** — Create a flow: entry -> task -> exit, all unconditional. Mock subprocess manager to return success for all tasks. Execute the flow. Verify:
   - Flow run status is `completed`
   - All 3 task executions exist in DB with status `completed`
   - Tasks were executed in order: entry, task, exit
   - `edge_transition` events were emitted

2. **test_linear_flow_returns_run_id** — Verify `execute()` returns a valid UUID string that matches the flow run in the DB.

3. **test_template_expansion** — Flow with a param `{{repo}}`. Provide `params={"repo": "my-repo"}`. Verify the expanded prompt in the task execution record contains "my-repo".

4. **test_task_directory_creation** — After execution, verify task directories exist at `~/.flowstate/runs/<run-id>/tasks/<name>-1/` for each node. Use tmp dir override for testing.

5. **test_budget_warning_events** — Flow with budget=100s. Mock tasks to report elapsed times that cross the 75% threshold. Verify a `flow.budget_warning` event is emitted.

6. **test_budget_exceeded_pauses** — Budget=100s, task reports 110s elapsed. Verify the flow is paused and a `flow.status_changed` event with reason="Budget exceeded" is emitted.

7. **test_task_failure_pauses_flow** — Mock subprocess to exit with code 1. Flow has `on_error=pause`. Verify the flow status is `paused` and a `task.failed` event is emitted.

8. **test_event_emission_order** — Verify events are emitted in the correct order: `flow.started`, `task.started`, `task.log` (multiple), `task.completed`, `edge.transition`, `task.started`, ..., `flow.completed`.

9. **test_context_mode_handoff** — Two-node flow with handoff context. Mock the first task to write a SUMMARY.md. Verify the second task's prompt includes the summary content.

10. **test_context_mode_none** — Two-node flow with `none` context. Verify the second task's prompt does NOT include predecessor context.

11. **test_concurrency_semaphore** — Verify the semaphore is created with the correct `max_concurrent` value.

**Mocking strategy**: Create a `MockSubprocessManager` that returns configurable `StreamEvent` sequences for each task. Use in-memory SQLite (`:memory:`) for the DB. Capture emitted events in a list via the `event_callback`.
