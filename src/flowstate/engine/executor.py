"""Flow executor -- the main orchestration loop for executing flows.

Drives flow execution by coordinating subprocess execution, context assembly,
budget tracking, and state persistence. Handles the full task lifecycle: create
task directory, assemble prompt, launch subprocess, stream events, evaluate
outgoing edges, and detect flow completion.

Supports:
- Linear (sequential) flows (ENGINE-005)
- Fork-join parallel execution (ENGINE-006)
- Conditional branching with judge protocol (ENGINE-007)
- Default edge: 1 unconditional + N conditional; falls back to default on no match
- Cycle re-entry with generation tracking (ENGINE-007)
- Pause/resume/cancel/retry/skip control operations (ENGINE-008)
- Full event emission at every state change (ENGINE-009)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from flowstate.dsl.ast import ContextMode, EdgeType, ErrorPolicy, NodeType
from flowstate.engine.budget import BudgetGuard
from flowstate.engine.context import (
    build_prompt_handoff,
    build_prompt_join,
    build_prompt_none,
    build_prompt_session,
    build_routing_instructions,
    create_task_dir,
    expand_templates,
    get_context_mode,
    read_summary,
    resolve_cwd,
)
from flowstate.engine.events import EventType, FlowEvent
from flowstate.engine.judge import JudgeContext, JudgeDecision, JudgePauseError, JudgeProtocol
from flowstate.engine.subprocess_mgr import StreamEventType, SubprocessManager
from flowstate.engine.worktree import (
    WorktreeInfo,
    cleanup_worktree,
    map_cwd_to_worktree,
    setup_worktree_if_needed,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from flowstate.dsl.ast import Edge, Flow, Node
    from flowstate.state.models import TaskExecutionRow
    from flowstate.state.repository import FlowstateDB

logger = logging.getLogger(__name__)

# Map StreamEventType to the allowed log_type values in the task_logs schema:
# 'stdout', 'stderr', 'tool_use', 'assistant_message', 'system'
_LOG_TYPE_MAP: dict[StreamEventType, str] = {
    StreamEventType.ASSISTANT: "assistant_message",
    StreamEventType.TOOL_USE: "tool_use",
    StreamEventType.TOOL_RESULT: "stdout",
    StreamEventType.RESULT: "stdout",
    StreamEventType.ERROR: "stderr",
    StreamEventType.SYSTEM: "system",
}


def _to_log_type(event_type: StreamEventType) -> str:
    """Convert a StreamEventType to a valid task_logs log_type."""
    return _LOG_TYPE_MAP.get(event_type, "system")


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def _find_entry_node(flow: Flow) -> Node:
    """Find the single entry node in a flow."""
    for node in flow.nodes.values():
        if node.node_type == NodeType.ENTRY:
            return node
    raise ValueError(f"Flow '{flow.name}' has no entry node")


def _get_outgoing_edges(flow: Flow, node_name: str) -> list[Edge]:
    """Get all outgoing edges from a node."""
    return [e for e in flow.edges if e.source == node_name]


def _is_fork(edges: list[Edge]) -> bool:
    """Check if any outgoing edge is a fork."""
    return any(e.edge_type == EdgeType.FORK for e in edges)


def _is_conditional(edges: list[Edge]) -> bool:
    """Check if any outgoing edge is conditional."""
    return any(e.edge_type == EdgeType.CONDITIONAL for e in edges)


def _maybe_append_routing(prompt: str, flow: Flow, node: Node, task_dir: str) -> str:
    """Append self-report routing instructions if judge is disabled and node has conditionals."""
    if _use_judge(flow, node):
        return prompt
    cond_edges = _conditional_edge_pairs(_get_outgoing_edges(flow, node.name))
    if cond_edges:
        return prompt + build_routing_instructions(task_dir, cond_edges)
    return prompt


def _conditional_edge_pairs(edges: list[Edge]) -> list[tuple[str, str]]:
    """Extract (condition, target) pairs from conditional edges."""
    return [
        (e.condition, e.target)
        for e in edges
        if e.edge_type == EdgeType.CONDITIONAL and e.condition and e.target
    ]


def _use_judge(flow: Flow, node: Node) -> bool:
    """Determine if a separate judge subprocess should evaluate routing.

    Node-level ``judge`` overrides flow-level. ``None`` at node level means
    inherit from flow. Default is ``False`` (task self-reports).
    """
    if node.judge is not None:
        return node.judge
    return flow.judge


def _has_default_edge(edges: list[Edge]) -> bool:
    """Check if edges form a default-edge pattern: exactly 1 unconditional + 1+ conditional."""
    unconditional = sum(1 for e in edges if e.edge_type == EdgeType.UNCONDITIONAL)
    conditional = sum(1 for e in edges if e.edge_type == EdgeType.CONDITIONAL)
    return unconditional == 1 and conditional >= 1


def _find_join_node(flow: Flow, fork_targets: tuple[str, ...]) -> str:
    """Find the join node for a set of fork targets.

    The join edge has join_sources matching the fork targets.
    """
    for edge in flow.edges:
        if (
            edge.edge_type == EdgeType.JOIN
            and edge.join_sources is not None
            and set(edge.join_sources) == set(fork_targets)
        ):
            assert edge.target is not None
            return edge.target
    raise ValueError(f"No join edge found for fork targets {fork_targets}")


def _has_been_executed(flow_run_id: str, node_name: str, db: FlowstateDB) -> bool:
    """Check if a node has any completed/failed/skipped executions in this run."""
    executions = db.list_task_executions(flow_run_id)
    return any(
        e.node_name == node_name and e.status in ("completed", "failed", "skipped")
        for e in executions
    )


def _get_next_generation(flow_run_id: str, node_name: str, db: FlowstateDB) -> int:
    """Get the next generation number for a node in a run."""
    executions = db.list_task_executions(flow_run_id)
    node_execs = [e for e in executions if e.node_name == node_name]
    if not node_execs:
        return 1
    return max(e.generation for e in node_execs) + 1


def _get_fork_group_for_member(
    task_id: str, flow_run_id: str, db: FlowstateDB
) -> tuple[str, str, str] | None:
    """Find the fork group that a task belongs to.

    Returns (fork_group_id, join_node_name, group_status) or None if the task
    is not in any fork group. Checks ALL fork groups, not just active ones,
    because a concurrent task completion may have already joined the group.
    """
    # Query all fork groups for this flow run (not just active)
    all_rows = db._fetchall(  # type: ignore[attr-defined]
        "SELECT * FROM fork_groups WHERE flow_run_id = ?", (flow_run_id,)
    )
    for row in all_rows:
        group_id = row["id"]
        members = db.get_fork_group_members(group_id)
        for member in members:
            if member.id == task_id:
                return (group_id, row["join_node_name"], row["status"])
    return None


class FlowExecutor:
    """Executes a flow by orchestrating subprocess launches, budget tracking, and state.

    Supports linear, fork-join, conditional, and cyclic flow topologies.
    Concurrency is bounded by an asyncio.Semaphore. Provides control operations
    (pause, resume, cancel, retry, skip) for interactive flow management.
    """

    def __init__(
        self,
        db: FlowstateDB,
        event_callback: Callable[[FlowEvent], None],
        subprocess_mgr: SubprocessManager,
        judge: JudgeProtocol | None = None,
        max_concurrent: int = 4,
        worktree_cleanup: bool = True,
    ) -> None:
        self._db = db
        self._raw_callback = event_callback
        self._subprocess_mgr = subprocess_mgr
        self._judge = judge or JudgeProtocol(subprocess_mgr)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._paused = False
        self._cancelled = False
        # asyncio.Event for pause/resume coordination
        self._resume_event = asyncio.Event()
        # Shared state for control operations
        self._pending_tasks: set[str] = set()
        self._flow: Flow | None = None
        self._flow_run_id: str | None = None
        self._data_dir: str | None = None
        self._expanded_prompts: dict[str, str] = {}
        self._budget: BudgetGuard | None = None
        self._completed_queue: asyncio.Queue[str] | None = None
        # Worktree isolation
        self._worktree_cleanup = worktree_cleanup
        self._worktree_info: WorktreeInfo | None = None
        # Queue task tracking (set by execute() when processing a queue task)
        self._task_id: str | None = None

    def _emit(self, event: FlowEvent) -> None:
        """Emit an event via the callback, catching any callback exceptions."""
        try:
            self._raw_callback(event)
        except Exception:
            logger.exception("Event callback raised an exception for event %s", event.type)

    def _emit_activity(self, flow_run_id: str, task_id: str, message: str) -> None:
        """Emit a human-readable executor activity log entry.

        Stores a system log in the DB and emits a TASK_LOG FlowEvent so the
        UI console can display executor orchestration decisions alongside
        normal task output.
        """
        content = json.dumps({"subtype": "activity", "message": message})
        self._db.insert_task_log(task_id, "system", content)
        self._emit(
            FlowEvent(
                type=EventType.TASK_LOG,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_id,
                    "log_type": "system",
                    "content": content,
                },
            )
        )

    async def execute(
        self,
        flow: Flow,
        params: dict[str, str | float | bool],
        workspace: str,
        flow_run_id: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Execute a flow and return the flow_run_id.

        Creates the flow run record, expands template variables, enqueues the
        entry node, and processes tasks through the main loop until the exit
        node completes, the flow is paused, or it is cancelled.

        If *flow_run_id* is provided it is used as the database primary key
        for the run; otherwise a new UUID is generated.  Passing the ID from
        the route handler ensures the RunManager key matches the DB key.
        """
        desired_id = flow_run_id or str(uuid.uuid4())
        data_dir = os.path.expanduser(f"~/.flowstate/runs/{desired_id}")

        # 1. Look up or create flow definition
        flow_def = self._db.get_flow_definition_by_name(flow.name)
        if flow_def is not None:
            flow_definition_id = flow_def.id
        else:
            flow_definition_id = self._db.create_flow_definition(
                name=flow.name, source_dsl="", ast_json=json.dumps({"name": flow.name})
            )

        # 2. Create flow run record
        flow_run_id = self._db.create_flow_run(
            flow_definition_id=flow_definition_id,
            data_dir=data_dir,
            budget_seconds=flow.budget_seconds,
            on_error=flow.on_error.value,
            default_workspace=workspace,
            params_json=json.dumps(params) if params else None,
            run_id=desired_id,
        )
        data_dir = os.path.expanduser(f"~/.flowstate/runs/{flow_run_id}")

        # Store task_id for task-aware execution
        self._task_id = task_id
        if task_id:
            # Link flow_run -> task
            self._db._execute(  # type: ignore[attr-defined]
                "UPDATE flow_runs SET task_id = ? WHERE id = ?",
                (task_id, flow_run_id),
            )
            self._db._commit()  # type: ignore[attr-defined]
            # Link task -> flow_run (now that flow_runs row exists)
            self._db.update_task_queue_status(task_id, "running", flow_run_id=flow_run_id)

        # Resolve workspace to absolute path
        workspace = str(Path(workspace).resolve())

        # Git worktree isolation
        self._worktree_info = await setup_worktree_if_needed(workspace, flow_run_id, flow.worktree)
        if self._worktree_info is not None:
            workspace = self._worktree_info.worktree_path
            self._db.update_flow_run_worktree(flow_run_id, workspace)
            logger.info("Created git worktree at %s for run %s", workspace, flow_run_id)

        # Ensure workspace directory exists (ignore errors for test paths)
        with contextlib.suppress(OSError):
            os.makedirs(workspace, exist_ok=True)

        # Transition to running
        self._db.update_flow_run_status(flow_run_id, "running")
        self._emit(
            FlowEvent(
                type=EventType.FLOW_STARTED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={"status": "running", "budget_seconds": flow.budget_seconds},
            )
        )

        # 3. Expand templates in all node prompts
        expanded_prompts: dict[str, str] = {}
        for node_name, node in flow.nodes.items():
            expanded_prompts[node_name] = expand_templates(node.prompt, params)

        # 4. Initialize budget guard
        budget = BudgetGuard(flow.budget_seconds)

        # 5. Store shared state for control operations
        self._flow = flow
        self._flow_run_id = flow_run_id
        self._data_dir = data_dir
        self._expanded_prompts = expanded_prompts
        self._budget = budget
        self._paused = False
        self._cancelled = False
        self._resume_event.clear()

        # 6. Enqueue entry node
        entry_node = _find_entry_node(flow)
        entry_task_id = self._create_task_execution(
            flow_run_id=flow_run_id,
            node=entry_node,
            generation=1,
            flow=flow,
            expanded_prompt=expanded_prompts[entry_node.name],
            data_dir=data_dir,
            context_mode=ContextMode.NONE,
        )

        # 7. Main loop
        pending: set[str] = {entry_task_id}
        self._pending_tasks = pending
        completed_queue: asyncio.Queue[str] = asyncio.Queue()
        self._completed_queue = completed_queue

        while pending or self._running_tasks or self._paused:
            # If cancelled, break out of the loop immediately.
            if self._cancelled:
                break

            # If paused, wait for resume (or cancel) before continuing.
            if self._paused:
                await self._resume_event.wait()
                self._resume_event.clear()
                # After waking, re-check cancel (cancel signals the event too).
                if self._cancelled:
                    break
                continue

            # Launch ready tasks (up to semaphore limit)
            ready = list(pending)
            for task_id in ready:
                if self._paused or self._cancelled:
                    break
                pending.discard(task_id)
                await self._semaphore.acquire()
                atask = asyncio.create_task(
                    self._execute_single_task(
                        flow_run_id=flow_run_id,
                        task_execution_id=task_id,
                        flow=flow,
                        expanded_prompts=expanded_prompts,
                        data_dir=data_dir,
                        budget=budget,
                        completed_queue=completed_queue,
                    )
                )
                self._running_tasks[task_id] = atask

            if self._cancelled:
                break

            # If paused after the for loop (with no running tasks), go back
            # to the top of the while loop where the pause-wait logic handles it.
            if self._paused and not self._running_tasks:
                continue

            # Wait for at least one task to complete
            if self._running_tasks and not completed_queue.qsize():
                completed_id = await completed_queue.get()
            elif completed_queue.qsize():
                completed_id = completed_queue.get_nowait()
            else:
                break

            self._running_tasks.pop(completed_id, None)
            self._semaphore.release()

            should_stop = await self._process_completed_task(
                completed_id, flow_run_id, flow, expanded_prompts, data_dir, budget, pending
            )
            if should_stop:
                await self._cleanup_worktree()
                return flow_run_id

        await self._cleanup_worktree()
        return flow_run_id

    async def _process_completed_task(
        self,
        completed_id: str,
        flow_run_id: str,
        flow: Flow,
        expanded_prompts: dict[str, str],
        data_dir: str,
        budget: BudgetGuard,
        pending: set[str],
    ) -> bool:
        """Process a completed task: evaluate edges, handle errors, check completion.

        Returns True if the flow should stop (completed or error handled).
        """
        # ENGINE-017: If the flow is being cancelled, skip all processing.
        # This guards against the race where a task's CancelledError handler
        # (or the subprocess exit with code 143) puts the task into the
        # completed_queue and the main loop processes it before cancel()
        # finishes its own cleanup.  Without this check, a task whose DB
        # status is still "running" (because CancelledError fired before the
        # status update) could be treated as a successful completion and
        # trigger successor task creation or on_error=pause.
        if self._cancelled:
            return False

        # Get task execution from DB
        task_exec = self._db.get_task_execution(completed_id)
        if task_exec is None:
            return False

        if task_exec.status == "failed":
            # If the flow is being cancelled, don't apply the on_error policy.
            # The cancel() method handles its own cleanup.
            if self._cancelled:
                return False
            await self._handle_error(
                flow_run_id, flow, budget, completed_id, pending, expanded_prompts, data_dir
            )
            return False

        # Check for exit node
        node = flow.nodes[task_exec.node_name]
        if node.node_type == NodeType.EXIT:
            self._complete_flow(flow_run_id, budget)
            return True

        # Evaluate outgoing edges
        outgoing = _get_outgoing_edges(flow, task_exec.node_name)

        if not outgoing:
            # Check if this is a fork group member -- fork members have no outgoing edges
            # because the join check handles the continuation
            fork_info = _get_fork_group_for_member(completed_id, flow_run_id, self._db)
            if fork_info is not None:
                _fg_id, _join_name, fg_status = fork_info
                if fg_status == "active":
                    # Only attempt join if the group hasn't already been joined
                    # (a concurrent task completion may have already triggered the join)
                    await self._check_fork_join_completion(
                        _fg_id, flow_run_id, flow, expanded_prompts, data_dir, budget, pending
                    )
                # If already joined or cancelled, this is a no-op
            else:
                # Defensive: no outgoing edges from a non-exit, non-fork-member node
                self._pause_flow(flow_run_id, "No outgoing edges from non-exit node")
            # Update elapsed and check budget
            self._db.update_flow_run_elapsed(flow_run_id, budget.elapsed)
            if budget.exceeded:
                self._pause_flow(flow_run_id, "Budget exceeded")
            return False

        # Handle fork edges (ENGINE-006)
        if _is_fork(outgoing):
            fork_edge = next(e for e in outgoing if e.edge_type == EdgeType.FORK)
            assert fork_edge.fork_targets is not None
            await self._handle_fork(
                fork_edge,
                completed_id,
                task_exec.generation,
                flow_run_id,
                flow,
                expanded_prompts,
                data_dir,
                pending,
            )

        # Handle default edge pattern: 1 unconditional + N conditional
        # Must come BEFORE _is_conditional because that returns True for any conditional edge
        elif _has_default_edge(outgoing):
            await self._handle_default_edge(
                outgoing,
                completed_id,
                task_exec,
                flow_run_id,
                flow,
                expanded_prompts,
                data_dir,
                pending,
            )

        # Handle conditional edges (ENGINE-007)
        elif _is_conditional(outgoing):
            await self._handle_conditional(
                outgoing,
                completed_id,
                task_exec,
                flow_run_id,
                flow,
                expanded_prompts,
                data_dir,
                pending,
            )

        # Handle unconditional edges
        elif len(outgoing) >= 1 and outgoing[0].edge_type == EdgeType.UNCONDITIONAL:
            edge = outgoing[0]
            assert edge.target is not None
            ctx_mode = get_context_mode(edge, flow)
            next_task_id = self._create_task_execution(
                flow_run_id=flow_run_id,
                node=flow.nodes[edge.target],
                generation=1,
                flow=flow,
                expanded_prompt=expanded_prompts[edge.target],
                data_dir=data_dir,
                context_mode=ctx_mode,
                predecessor_task_id=completed_id,
            )
            pending.add(next_task_id)

            # Record edge transition
            self._db.create_edge_transition(
                flow_run_id=flow_run_id,
                from_task_id=completed_id,
                to_task_id=next_task_id,
                edge_type=edge.edge_type.value,
            )
            self._emit(
                FlowEvent(
                    type=EventType.EDGE_TRANSITION,
                    flow_run_id=flow_run_id,
                    timestamp=_now_iso(),
                    payload={
                        "from_node": task_exec.node_name,
                        "to_node": edge.target,
                        "edge_type": edge.edge_type.value,
                        "condition": None,
                        "judge_reasoning": None,
                    },
                )
            )
            self._emit_activity(
                flow_run_id,
                completed_id,
                f"\u2192 Edge transition: {task_exec.node_name} \u2192 {edge.target}",
            )

        # Check fork group completion for fork members (only if still active)
        fork_info = _get_fork_group_for_member(completed_id, flow_run_id, self._db)
        if fork_info is not None and fork_info[2] == "active":
            await self._check_fork_join_completion(
                fork_info[0], flow_run_id, flow, expanded_prompts, data_dir, budget, pending
            )

        # Update flow run elapsed
        self._db.update_flow_run_elapsed(flow_run_id, budget.elapsed)

        # Budget check (exit node completion takes priority over budget)
        if budget.exceeded:
            self._pause_flow(flow_run_id, "Budget exceeded")

        return False

    # ------------------------------------------------------------------ #
    # Fork-join handling (ENGINE-006)
    # ------------------------------------------------------------------ #

    async def _handle_fork(
        self,
        fork_edge: Edge,
        source_task_id: str,
        generation: int,
        flow_run_id: str,
        flow: Flow,
        expanded_prompts: dict[str, str],
        data_dir: str,
        pending: set[str],
    ) -> None:
        """Create fork group and enqueue all fork target tasks."""
        assert fork_edge.fork_targets is not None
        join_node_name = _find_join_node(flow, fork_edge.fork_targets)

        # Create task executions for all fork targets
        member_task_ids: list[str] = []
        for target_name in fork_edge.fork_targets:
            target_node = flow.nodes[target_name]
            ctx_mode = get_context_mode(fork_edge, flow)
            task_id = self._create_task_execution(
                flow_run_id=flow_run_id,
                node=target_node,
                generation=generation,
                flow=flow,
                expanded_prompt=expanded_prompts[target_name],
                data_dir=data_dir,
                context_mode=ctx_mode,
                predecessor_task_id=source_task_id,
            )
            member_task_ids.append(task_id)
            pending.add(task_id)

        # Create fork group in DB with all members atomically
        fork_group_id = self._db.create_fork_group(
            flow_run_id=flow_run_id,
            source_task_id=source_task_id,
            join_node_name=join_node_name,
            generation=generation,
            member_task_ids=member_task_ids,
        )

        # Record edge transitions for each fork target
        for i, _target_name in enumerate(fork_edge.fork_targets):
            self._db.create_edge_transition(
                flow_run_id=flow_run_id,
                from_task_id=source_task_id,
                to_task_id=member_task_ids[i],
                edge_type="fork",
            )

        task_exec = self._db.get_task_execution(source_task_id)
        source_node_name = task_exec.node_name if task_exec else "unknown"

        self._emit(
            FlowEvent(
                type=EventType.FORK_STARTED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "fork_group_id": fork_group_id,
                    "source_node": source_node_name,
                    "targets": list(fork_edge.fork_targets),
                },
            )
        )

        self._emit(
            FlowEvent(
                type=EventType.EDGE_TRANSITION,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "from_node": source_node_name,
                    "to_node": ", ".join(fork_edge.fork_targets),
                    "edge_type": "fork",
                    "condition": None,
                    "judge_reasoning": None,
                },
            )
        )
        targets_str = ", ".join(fork_edge.fork_targets)
        self._emit_activity(
            flow_run_id,
            source_task_id,
            f"\u2442 Fork: {source_node_name} \u2192 [{targets_str}]",
        )

    async def _check_fork_join_completion(
        self,
        fork_group_id: str,
        flow_run_id: str,
        flow: Flow,
        expanded_prompts: dict[str, str],
        data_dir: str,
        budget: BudgetGuard,
        pending: set[str],
    ) -> None:
        """Check if all fork members are done and trigger join if so."""
        fork_group = self._db.get_fork_group(fork_group_id)
        if fork_group is None or fork_group.status != "active":
            return

        members = self._db.get_fork_group_members(fork_group_id)
        all_done = all(m.status in ("completed", "skipped") for m in members)

        if not all_done:
            return

        if self._paused:
            # Don't trigger join while paused; it will be checked on resume
            return

        # Mark fork group as joined
        self._db.update_fork_group_status(fork_group_id, "joined")

        # Collect summaries from all members
        member_summaries: dict[str, str | None] = {}
        for m in members:
            summary = read_summary(m.task_dir)
            member_summaries[m.node_name] = summary

        # Enqueue join target
        join_node = flow.nodes[fork_group.join_node_name]
        join_gen = fork_group.generation + 1
        task_dir = create_task_dir(data_dir, join_node.name, join_gen)
        cwd = resolve_cwd(join_node, flow)
        cwd = self._apply_worktree_mapping(cwd)
        prompt = build_prompt_join(join_node, task_dir, cwd, member_summaries)

        # Expand template if needed
        expanded = expanded_prompts.get(join_node.name, join_node.prompt)
        if expanded != join_node.prompt:
            prompt = prompt.replace(join_node.prompt, expanded)

        join_task_id = self._db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name=join_node.name,
            node_type=join_node.node_type.value,
            generation=join_gen,
            context_mode=ContextMode.HANDOFF.value,
            cwd=cwd,
            task_dir=task_dir,
            prompt_text=prompt,
        )
        pending.add(join_task_id)

        # Record join edge transition
        self._db.create_edge_transition(
            flow_run_id=flow_run_id,
            from_task_id=members[0].id,  # use first member as representative
            to_task_id=join_task_id,
            edge_type="join",
        )

        self._emit(
            FlowEvent(
                type=EventType.FORK_JOINED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "fork_group_id": fork_group_id,
                    "join_node": fork_group.join_node_name,
                },
            )
        )

        member_names = ", ".join(m.node_name for m in members)
        self._emit(
            FlowEvent(
                type=EventType.EDGE_TRANSITION,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "from_node": member_names,
                    "to_node": fork_group.join_node_name,
                    "edge_type": "join",
                    "condition": None,
                    "judge_reasoning": None,
                },
            )
        )
        join_node = flow.nodes[fork_group.join_node_name]
        self._emit_activity(
            flow_run_id,
            join_task_id,
            f"\u2295 Join: [{member_names}] \u2192 {join_node.name}",
        )

    # ------------------------------------------------------------------ #
    # Conditional + cycle handling (ENGINE-007)
    # ------------------------------------------------------------------ #

    async def _acquire_routing_decision(
        self,
        flow: Flow,
        node: Node,
        task_exec: object,
        cond_edges: list[tuple[str, str]],
        flow_run_id: str,
    ) -> JudgeDecision | None:
        """Acquire a routing decision via judge subprocess or self-report.

        Returns the decision, or None if the flow was paused due to failure.
        """
        from flowstate.engine.judge import read_judge_decision
        from flowstate.state.models import TaskExecutionRow

        assert isinstance(task_exec, TaskExecutionRow)

        if _use_judge(flow, node):
            summary = read_summary(task_exec.task_dir)
            judge_context = JudgeContext(
                node_name=task_exec.node_name,
                task_prompt=task_exec.prompt_text,
                exit_code=task_exec.exit_code or 0,
                summary=summary,
                task_cwd=task_exec.cwd,
                run_id=flow_run_id,
                outgoing_edges=cond_edges,
                skip_permissions=flow.skip_permissions,
            )

            self._emit(
                FlowEvent(
                    type=EventType.JUDGE_STARTED,
                    flow_run_id=flow_run_id,
                    timestamp=_now_iso(),
                    payload={
                        "from_node": task_exec.node_name,
                        "conditions": [c for c, _ in cond_edges],
                    },
                )
            )

            try:
                return await self._judge.evaluate(judge_context)
            except JudgePauseError as e:
                self._pause_flow(flow_run_id, f"Judge failed: {e.reason}")
                return None
        else:
            try:
                return read_judge_decision(task_exec.task_dir)
            except (FileNotFoundError, ValueError) as e:
                self._pause_flow(flow_run_id, f"Task self-report failed: {e}")
                return None

    async def _handle_conditional(
        self,
        outgoing: list[Edge],
        completed_id: str,
        task_exec: object,
        flow_run_id: str,
        flow: Flow,
        expanded_prompts: dict[str, str],
        data_dir: str,
        pending: set[str],
    ) -> None:
        """Evaluate conditional edges and route accordingly."""
        from flowstate.state.models import TaskExecutionRow

        assert isinstance(task_exec, TaskExecutionRow)
        node = flow.nodes[task_exec.node_name]
        cond_edges = _conditional_edge_pairs(outgoing)

        decision = await self._acquire_routing_decision(
            flow, node, task_exec, cond_edges, flow_run_id
        )
        if decision is None:
            return

        self._emit(
            FlowEvent(
                type=EventType.JUDGE_DECIDED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "from_node": task_exec.node_name,
                    "to_node": decision.target,
                    "reasoning": decision.reasoning,
                    "confidence": decision.confidence,
                },
            )
        )
        self._emit_judge_activity(flow_run_id, completed_id, task_exec, decision)

        # Handle special cases
        if decision.is_none:
            self._pause_flow(flow_run_id, "Judge could not match any condition")
            return

        if decision.is_low_confidence:
            self._pause_flow(
                flow_run_id,
                f"Judge has low confidence ({decision.confidence:.2f}) "
                f"for transition to '{decision.target}': {decision.reasoning}",
            )
            return

        # Find the matching edge
        chosen_edge = next(
            e
            for e in outgoing
            if e.edge_type == EdgeType.CONDITIONAL and e.target == decision.target
        )

        # Determine if this is a cycle re-entry
        is_cycle = _has_been_executed(flow_run_id, decision.target, self._db)
        target_gen = _get_next_generation(flow_run_id, decision.target, self._db) if is_cycle else 1

        ctx_mode = get_context_mode(chosen_edge, flow)
        target_node = flow.nodes[decision.target]

        # Create task execution for the target
        next_task_id = self._create_task_execution_conditional(
            flow_run_id=flow_run_id,
            target_node=target_node,
            generation=target_gen,
            flow=flow,
            expanded_prompt=expanded_prompts[decision.target],
            data_dir=data_dir,
            context_mode=ctx_mode,
            source_task=task_exec,
            judge_decision=decision,
            is_cycle=is_cycle,
        )
        pending.add(next_task_id)

        # Record edge transition
        self._db.create_edge_transition(
            flow_run_id=flow_run_id,
            from_task_id=completed_id,
            to_task_id=next_task_id,
            edge_type="conditional",
            condition_text=chosen_edge.condition,
            judge_decision=decision.target,
            judge_reasoning=decision.reasoning,
            judge_confidence=decision.confidence,
        )

        self._emit(
            FlowEvent(
                type=EventType.EDGE_TRANSITION,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "from_node": task_exec.node_name,
                    "to_node": decision.target,
                    "edge_type": "conditional",
                    "condition": chosen_edge.condition,
                    "judge_reasoning": decision.reasoning,
                },
            )
        )

    async def _handle_default_edge(
        self,
        outgoing: list[Edge],
        completed_id: str,
        task_exec: object,
        flow_run_id: str,
        flow: Flow,
        expanded_prompts: dict[str, str],
        data_dir: str,
        pending: set[str],
    ) -> None:
        """Evaluate conditional edges; fall back to default edge if no match."""
        from flowstate.state.models import TaskExecutionRow

        assert isinstance(task_exec, TaskExecutionRow)
        node = flow.nodes[task_exec.node_name]

        # Separate default and conditional edges
        default_edge = next(e for e in outgoing if e.edge_type == EdgeType.UNCONDITIONAL)
        cond_edges = _conditional_edge_pairs(outgoing)

        decision = await self._acquire_routing_decision(
            flow, node, task_exec, cond_edges, flow_run_id
        )
        if decision is None:
            return

        self._emit(
            FlowEvent(
                type=EventType.JUDGE_DECIDED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "from_node": task_exec.node_name,
                    "to_node": decision.target,
                    "reasoning": decision.reasoning,
                    "confidence": decision.confidence,
                },
            )
        )
        self._emit_judge_activity(flow_run_id, completed_id, task_exec, decision)

        # On __none__ or low confidence, follow the DEFAULT edge instead of pausing
        if decision.is_none or decision.is_low_confidence:
            assert default_edge.target is not None
            is_cycle = _has_been_executed(flow_run_id, default_edge.target, self._db)
            target_gen = (
                _get_next_generation(flow_run_id, default_edge.target, self._db) if is_cycle else 1
            )
            ctx_mode = get_context_mode(default_edge, flow)
            target_node = flow.nodes[default_edge.target]

            next_task_id = self._create_task_execution_conditional(
                flow_run_id=flow_run_id,
                target_node=target_node,
                generation=target_gen,
                flow=flow,
                expanded_prompt=expanded_prompts[default_edge.target],
                data_dir=data_dir,
                context_mode=ctx_mode,
                source_task=task_exec,
                judge_decision=decision,
                is_cycle=is_cycle,
            )
            pending.add(next_task_id)

            self._db.create_edge_transition(
                flow_run_id=flow_run_id,
                from_task_id=completed_id,
                to_task_id=next_task_id,
                edge_type="unconditional",
                condition_text=None,
                judge_decision=default_edge.target,
                judge_reasoning=(
                    decision.reasoning
                    if not decision.is_none
                    else "No condition matched, following default edge"
                ),
                judge_confidence=decision.confidence if not decision.is_none else 0.0,
            )

            self._emit(
                FlowEvent(
                    type=EventType.EDGE_TRANSITION,
                    flow_run_id=flow_run_id,
                    timestamp=_now_iso(),
                    payload={
                        "from_node": task_exec.node_name,
                        "to_node": default_edge.target,
                        "edge_type": "unconditional",
                        "condition": None,
                        "judge_reasoning": (
                            decision.reasoning
                            if not decision.is_none
                            else "No condition matched, following default edge"
                        ),
                    },
                )
            )
            return

        # Judge matched a condition -> follow that conditional edge
        chosen_edge = next(
            e
            for e in outgoing
            if e.edge_type == EdgeType.CONDITIONAL and e.target == decision.target
        )

        is_cycle = _has_been_executed(flow_run_id, decision.target, self._db)
        target_gen = _get_next_generation(flow_run_id, decision.target, self._db) if is_cycle else 1
        ctx_mode = get_context_mode(chosen_edge, flow)
        target_node = flow.nodes[decision.target]

        next_task_id = self._create_task_execution_conditional(
            flow_run_id=flow_run_id,
            target_node=target_node,
            generation=target_gen,
            flow=flow,
            expanded_prompt=expanded_prompts[decision.target],
            data_dir=data_dir,
            context_mode=ctx_mode,
            source_task=task_exec,
            judge_decision=decision,
            is_cycle=is_cycle,
        )
        pending.add(next_task_id)

        self._db.create_edge_transition(
            flow_run_id=flow_run_id,
            from_task_id=completed_id,
            to_task_id=next_task_id,
            edge_type="conditional",
            condition_text=chosen_edge.condition,
            judge_decision=decision.target,
            judge_reasoning=decision.reasoning,
            judge_confidence=decision.confidence,
        )

        self._emit(
            FlowEvent(
                type=EventType.EDGE_TRANSITION,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "from_node": task_exec.node_name,
                    "to_node": decision.target,
                    "edge_type": "conditional",
                    "condition": chosen_edge.condition,
                    "judge_reasoning": decision.reasoning,
                },
            )
        )

    def _create_task_execution_conditional(
        self,
        flow_run_id: str,
        target_node: Node,
        generation: int,
        flow: Flow,
        expanded_prompt: str,
        data_dir: str,
        context_mode: ContextMode,
        source_task: object,
        judge_decision: JudgeDecision,
        is_cycle: bool,
    ) -> str:
        """Create task execution for a conditional transition, handling cycles."""
        from flowstate.state.models import TaskExecutionRow

        assert isinstance(source_task, TaskExecutionRow)

        task_dir = create_task_dir(data_dir, target_node.name, generation)
        cwd = resolve_cwd(target_node, flow)
        cwd = self._apply_worktree_mapping(cwd)
        claude_session_id: str | None = None

        if is_cycle and context_mode == ContextMode.HANDOFF:
            # For cycle re-entry with handoff: include source task's summary
            # AND the judge's reasoning as feedback
            source_summary = read_summary(source_task.task_dir)
            cycle_context = (
                f"{source_summary or '(No summary available)'}\n\n"
                f"## Judge Feedback\n"
                f"The reviewing judge decided: {judge_decision.reasoning}\n"
                f"You are re-entering this task (generation {generation}) "
                f"to address the feedback."
            )
            prompt = build_prompt_handoff(target_node, task_dir, cwd, cycle_context)

        elif is_cycle and context_mode == ContextMode.SESSION:
            # Resume the SOURCE task's session (the reviewer), not the
            # target's previous session
            prompt = build_prompt_session(target_node, task_dir)
            claude_session_id = source_task.claude_session_id

        elif context_mode == ContextMode.HANDOFF:
            # Normal (non-cycle) conditional transition
            source_summary = read_summary(source_task.task_dir)
            prompt = build_prompt_handoff(target_node, task_dir, cwd, source_summary)

        elif context_mode == ContextMode.SESSION:
            prompt = build_prompt_session(target_node, task_dir)
            claude_session_id = source_task.claude_session_id

        else:  # none
            prompt = build_prompt_none(target_node, task_dir, cwd)

        # Expand template if needed
        if expanded_prompt != target_node.prompt:
            prompt = prompt.replace(target_node.prompt, expanded_prompt)

        prompt = _maybe_append_routing(prompt, flow, target_node, task_dir)

        # Inject task queue context if executing on behalf of a task
        prompt = self._inject_task_context(prompt)

        task_id = self._db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name=target_node.name,
            node_type=target_node.node_type.value,
            generation=generation,
            context_mode=context_mode.value,
            cwd=cwd,
            task_dir=task_dir,
            prompt_text=prompt,
        )

        # Store session ID if resuming
        if claude_session_id:
            self._db.update_task_status(task_id, "pending", claude_session_id=claude_session_id)

        return task_id

    # ------------------------------------------------------------------ #
    # Control operations (ENGINE-008)
    # ------------------------------------------------------------------ #

    async def pause(self, flow_run_id: str) -> None:
        """Pause the flow. Let running tasks finish, don't start new ones."""
        if self._paused:
            return  # idempotent

        self._paused = True

        # Wait for currently running tasks to finish
        if self._running_tasks:
            await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
        self._running_tasks.clear()

        run = self._db.get_flow_run(flow_run_id)
        old_status = run.status if run else "unknown"
        self._db.update_flow_run_status(flow_run_id, "paused")
        self._emit(
            FlowEvent(
                type=EventType.FLOW_STATUS_CHANGED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "old_status": old_status,
                    "new_status": "paused",
                    "reason": "User paused",
                },
            )
        )

    async def resume(self, flow_run_id: str) -> None:
        """Resume a paused flow. Pick up from where we left off."""
        self._paused = False
        self._db.update_flow_run_status(flow_run_id, "running")
        self._emit(
            FlowEvent(
                type=EventType.FLOW_STATUS_CHANGED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "old_status": "paused",
                    "new_status": "running",
                    "reason": "User resumed",
                },
            )
        )

        # Re-populate pending tasks: find tasks whose predecessors are all
        # complete but that haven't been started yet (status = "pending").
        if self._flow is not None:
            tasks = self._db.list_task_executions(flow_run_id)
            for task in tasks:
                if task.status == "pending" and task.id not in self._running_tasks:
                    self._pending_tasks.add(task.id)

        # Signal the main loop to wake up and continue.
        self._resume_event.set()

    async def cancel(self, flow_run_id: str) -> None:
        """Cancel the flow. Kill all running subprocesses."""
        self._cancelled = True
        self._paused = False  # unblock if paused
        # Wake up the main loop if it's waiting on _resume_event (paused state).
        self._resume_event.set()

        # Kill all running subprocesses
        for task_id in list(self._running_tasks):
            task_exec = self._db.get_task_execution(task_id)
            if task_exec and task_exec.claude_session_id:
                await self._subprocess_mgr.kill(task_exec.claude_session_id)
            atask = self._running_tasks.get(task_id)
            if atask:
                atask.cancel()

        # Wait for all tasks to finish cancellation
        if self._running_tasks:
            await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
        self._running_tasks.clear()

        # Mark all still-active tasks as failed (due to cancellation).
        # The DB schema only allows 'failed'/'skipped' for terminal error states.
        tasks = self._db.list_task_executions(flow_run_id)
        for task in tasks:
            if task.status in ("running", "pending", "waiting"):
                self._db.update_task_status(task.id, "failed", error_message="Flow cancelled")

        # Update fork groups
        groups = self._db.get_active_fork_groups(flow_run_id)
        for group in groups:
            self._db.update_fork_group_status(group.id, "cancelled")

        # Cleanup worktree
        await self._cleanup_worktree()

        # Mark the queue task as cancelled
        queue_task_id = self._task_id
        if queue_task_id:
            self._db.update_task_queue_status(queue_task_id, "cancelled")

        self._db.update_flow_run_status(flow_run_id, "cancelled")
        self._emit(
            FlowEvent(
                type=EventType.FLOW_STATUS_CHANGED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "old_status": "running",
                    "new_status": "cancelled",
                    "reason": "User cancelled",
                },
            )
        )

    async def retry_task(self, flow_run_id: str, task_execution_id: str) -> None:
        """Retry a failed task. Creates new task execution with incremented generation."""
        old_task = self._db.get_task_execution(task_execution_id)
        if old_task is None:
            raise ValueError(f"Task execution not found: {task_execution_id}")
        if old_task.status != "failed":
            raise ValueError(f"Can only retry failed tasks, got status: {old_task.status}")

        new_gen = _get_next_generation(flow_run_id, old_task.node_name, self._db)
        flow_run = self._db.get_flow_run(flow_run_id)
        if flow_run is None:
            raise ValueError(f"Flow run not found: {flow_run_id}")

        # Re-create task execution with new generation
        new_task_dir = create_task_dir(flow_run.data_dir, old_task.node_name, new_gen)

        # Use the same prompt as the original but with updated task_dir
        new_prompt = old_task.prompt_text.replace(old_task.task_dir, new_task_dir)

        new_task_id = self._db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name=old_task.node_name,
            node_type=old_task.node_type,
            generation=new_gen,
            context_mode=old_task.context_mode,
            cwd=old_task.cwd,
            task_dir=new_task_dir,
            prompt_text=new_prompt,
        )

        # Add to pending set so it gets picked up
        self._pending_tasks.add(new_task_id)

    async def skip_task(self, flow_run_id: str, task_execution_id: str) -> None:
        """Skip a failed task and continue via first outgoing edge."""
        task = self._db.get_task_execution(task_execution_id)
        if task is None:
            raise ValueError(f"Task execution not found: {task_execution_id}")
        if task.status != "failed":
            raise ValueError(f"Can only skip failed tasks, got status: {task.status}")

        self._db.update_task_status(task_execution_id, "skipped")

        # Continue via first outgoing edge (if flow context is available)
        if self._flow is not None:
            outgoing = _get_outgoing_edges(self._flow, task.node_name)
            if outgoing:
                edge = outgoing[0]
                if edge.edge_type == EdgeType.UNCONDITIONAL and edge.target:
                    ctx_mode = get_context_mode(edge, self._flow)
                    data_dir = self._data_dir or ""
                    expanded = self._expanded_prompts.get(edge.target, "")
                    next_task_id = self._create_task_execution(
                        flow_run_id=flow_run_id,
                        node=self._flow.nodes[edge.target],
                        generation=1,
                        flow=self._flow,
                        expanded_prompt=expanded,
                        data_dir=data_dir,
                        context_mode=ctx_mode,
                        predecessor_task_id=task_execution_id,
                    )
                    self._pending_tasks.add(next_task_id)

        # Check fork group completion (skipped counts as "done" for join purposes)
        fork_info = _get_fork_group_for_member(task_execution_id, flow_run_id, self._db)
        if fork_info is not None and fork_info[2] == "active" and self._flow is not None:
            await self._check_fork_join_completion(
                fork_info[0],
                flow_run_id,
                self._flow,
                self._expanded_prompts,
                self._data_dir or "",
                self._budget or BudgetGuard(3600),
                self._pending_tasks,
            )

    # ------------------------------------------------------------------ #
    # Task execution
    # ------------------------------------------------------------------ #

    async def _execute_single_task(
        self,
        flow_run_id: str,
        task_execution_id: str,
        flow: Flow,
        expanded_prompts: dict[str, str],
        data_dir: str,
        budget: BudgetGuard,
        completed_queue: asyncio.Queue[str],
    ) -> None:
        """Execute a single task subprocess and handle its output."""
        task_exec = self._db.get_task_execution(task_execution_id)
        if task_exec is None:
            await completed_queue.put(task_execution_id)
            return

        node = flow.nodes[task_exec.node_name]

        # Update status to running
        self._db.update_task_status(task_execution_id, "running", started_at=_now_iso())
        start_time = time.monotonic()

        # Track task node history when executing on behalf of a queue task
        queue_task_id = self._task_id
        if queue_task_id:
            self._db.update_task_queue_status(queue_task_id, "running", current_node=node.name)
            self._db.add_task_node_history(queue_task_id, node.name, flow_run_id)

        self._emit(
            FlowEvent(
                type=EventType.TASK_STARTED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "node_name": node.name,
                    "generation": task_exec.generation,
                    "cwd": task_exec.cwd,
                    "task_dir": task_exec.task_dir,
                },
            )
        )
        self._emit_activity(
            flow_run_id,
            task_execution_id,
            f"\u25b6 Dispatching node '{node.name}' (generation {task_exec.generation})",
        )

        try:
            skip_perms = flow.skip_permissions
            session_id = task_exec.claude_session_id or str(uuid.uuid4())

            # Determine if this is a session resume or fresh task
            if task_exec.context_mode == ContextMode.SESSION.value and task_exec.claude_session_id:
                stream = self._subprocess_mgr.run_task_resume(
                    task_exec.prompt_text,
                    task_exec.cwd,
                    task_exec.claude_session_id,
                    skip_permissions=skip_perms,
                )
            else:
                stream = self._subprocess_mgr.run_task(
                    task_exec.prompt_text,
                    task_exec.cwd,
                    session_id,
                    skip_permissions=skip_perms,
                )

            # Stream events
            exit_code: int | None = None
            async for event in stream:
                # Store log (map event type to allowed DB log_type)
                log_type = _to_log_type(event.type)
                self._db.insert_task_log(task_execution_id, log_type, event.raw)
                # Emit to UI
                self._emit(
                    FlowEvent(
                        type=EventType.TASK_LOG,
                        flow_run_id=flow_run_id,
                        timestamp=_now_iso(),
                        payload={
                            "task_execution_id": task_execution_id,
                            "log_type": event.type.value,
                            "content": event.raw,
                        },
                    )
                )
                if (
                    event.type == StreamEventType.SYSTEM
                    and event.content.get("event") == "process_exit"
                ):
                    exit_code = event.content.get("exit_code", -1)
                # Capture real Claude Code session ID from system/init event.
                if (
                    event.type == StreamEventType.SYSTEM
                    and event.content.get("subtype") == "init"
                    and isinstance(event.content.get("session_id"), str)
                ):
                    session_id = event.content["session_id"]

            elapsed = time.monotonic() - start_time

            if exit_code == 0:
                self._db.update_task_status(
                    task_execution_id,
                    "completed",
                    exit_code=exit_code,
                    elapsed_seconds=elapsed,
                    claude_session_id=session_id,
                    completed_at=_now_iso(),
                )
                # Complete task node history when executing on behalf of a queue task
                if queue_task_id:
                    self._db.complete_task_node_history(queue_task_id, node.name)
                # Budget tracking
                warnings = budget.add_elapsed(elapsed)
                for w in warnings:
                    self._emit(
                        FlowEvent(
                            type=EventType.FLOW_BUDGET_WARNING,
                            flow_run_id=flow_run_id,
                            timestamp=_now_iso(),
                            payload={
                                "elapsed_seconds": budget.elapsed,
                                "budget_seconds": budget.budget_seconds,
                                "percent_used": w,
                            },
                        )
                    )
                    self._emit_activity(
                        flow_run_id,
                        task_execution_id,
                        f"\u26a0 Budget warning: {w}% used"
                        f" ({budget.elapsed:.0f}s / {budget.budget_seconds}s)",
                    )
                self._emit(
                    FlowEvent(
                        type=EventType.TASK_COMPLETED,
                        flow_run_id=flow_run_id,
                        timestamp=_now_iso(),
                        payload={
                            "task_execution_id": task_execution_id,
                            "node_name": node.name,
                            "exit_code": exit_code,
                            "elapsed_seconds": elapsed,
                        },
                    )
                )
            else:
                # When the flow is being cancelled, still mark as "failed" (DB
                # schema constraint) but skip the TASK_FAILED event -- the
                # _handle_error guard on self._cancelled prevents the on_error
                # policy from triggering.
                error_msg = (
                    "Flow cancelled" if self._cancelled else f"Task exited with code {exit_code}"
                )
                self._db.update_task_status(
                    task_execution_id,
                    "failed",
                    error_message=error_msg,
                    elapsed_seconds=elapsed,
                    completed_at=_now_iso(),
                )
                if not self._cancelled:
                    self._emit(
                        FlowEvent(
                            type=EventType.TASK_FAILED,
                            flow_run_id=flow_run_id,
                            timestamp=_now_iso(),
                            payload={
                                "task_execution_id": task_execution_id,
                                "node_name": node.name,
                                "error_message": error_msg,
                            },
                        )
                    )
        except asyncio.CancelledError:
            # ENGINE-017: asyncio.CancelledError is a BaseException, not caught by
            # 'except Exception'.  When cancel() calls atask.cancel() on our task,
            # we must handle it explicitly to mark the task as failed in the DB.
            # Without this handler the task status stays "running" and the cancel()
            # cleanup loop has to fix it -- but between then and the main loop
            # picking the task off completed_queue there is a window where
            # _process_completed_task could see a non-"failed" status and
            # incorrectly evaluate outgoing edges or trigger on_error=pause.
            elapsed = time.monotonic() - start_time
            self._db.update_task_status(
                task_execution_id,
                "failed",
                error_message="Flow cancelled",
                elapsed_seconds=elapsed,
                completed_at=_now_iso(),
            )
        except Exception as e:
            elapsed = time.monotonic() - start_time
            error_msg_exc = "Flow cancelled" if self._cancelled else str(e)
            self._db.update_task_status(
                task_execution_id,
                "failed",
                error_message=error_msg_exc,
                elapsed_seconds=elapsed,
                completed_at=_now_iso(),
            )
            if not self._cancelled:
                self._emit(
                    FlowEvent(
                        type=EventType.TASK_FAILED,
                        flow_run_id=flow_run_id,
                        timestamp=_now_iso(),
                        payload={
                            "task_execution_id": task_execution_id,
                            "node_name": node.name,
                            "error_message": str(e),
                        },
                    )
                )
        finally:
            await completed_queue.put(task_execution_id)

    # ------------------------------------------------------------------ #
    # Task creation helpers
    # ------------------------------------------------------------------ #

    def _create_task_execution(
        self,
        flow_run_id: str,
        node: Node,
        generation: int,
        flow: Flow,
        expanded_prompt: str,
        data_dir: str,
        context_mode: ContextMode,
        predecessor_task_id: str | None = None,
    ) -> str:
        """Create a task execution record and its task directory."""
        task_dir = create_task_dir(data_dir, node.name, generation)
        cwd = resolve_cwd(node, flow)
        cwd = self._apply_worktree_mapping(cwd)

        # Build the full prompt based on context mode
        if context_mode == ContextMode.HANDOFF and predecessor_task_id:
            pred = self._db.get_task_execution(predecessor_task_id)
            summary = read_summary(pred.task_dir) if pred else None
            prompt = build_prompt_handoff(node, task_dir, cwd, summary)
        elif context_mode == ContextMode.SESSION:
            prompt = build_prompt_session(node, task_dir)
        else:
            prompt = build_prompt_none(node, task_dir, cwd)

        # Use expanded prompt in the prompt text (replace the node.prompt with expanded version)
        # The prompt builders use node.prompt, so we need to substitute
        if expanded_prompt != node.prompt:
            prompt = prompt.replace(node.prompt, expanded_prompt)

        prompt = _maybe_append_routing(prompt, flow, node, task_dir)

        # Inject task queue context if executing on behalf of a task
        prompt = self._inject_task_context(prompt)

        task_id = self._db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name=node.name,
            node_type=node.node_type.value,
            generation=generation,
            context_mode=context_mode.value,
            cwd=cwd,
            task_dir=task_dir,
            prompt_text=prompt,
        )
        return task_id

    # ------------------------------------------------------------------ #
    # Error handling
    # ------------------------------------------------------------------ #

    async def _handle_error(
        self,
        flow_run_id: str,
        flow: Flow,
        budget: BudgetGuard,
        failed_task_id: str,
        pending: set[str],
        expanded_prompts: dict[str, str],
        data_dir: str,
    ) -> None:
        """Apply the flow's on_error policy after a task failure."""
        # Don't apply on_error policy when the flow is being cancelled.
        if self._cancelled:
            return

        policy = flow.on_error
        failed_task = self._db.get_task_execution(failed_task_id)

        if policy == ErrorPolicy.PAUSE:
            error_msg = failed_task.error_message if failed_task else "Unknown error"
            self._pause_flow(flow_run_id, f"Task failed (on_error=pause): {error_msg}")

        elif policy == ErrorPolicy.ABORT:
            await self.cancel(flow_run_id)

        elif policy == ErrorPolicy.SKIP:
            if failed_task is not None:
                self._db.update_task_status(failed_task_id, "skipped")
                # Continue via first outgoing edge
                outgoing = _get_outgoing_edges(flow, failed_task.node_name)
                if outgoing:
                    edge = outgoing[0]
                    if edge.target:
                        ctx_mode = get_context_mode(edge, flow)
                        next_task_id = self._create_task_execution(
                            flow_run_id=flow_run_id,
                            node=flow.nodes[edge.target],
                            generation=1,
                            flow=flow,
                            expanded_prompt=expanded_prompts.get(
                                edge.target, flow.nodes[edge.target].prompt
                            ),
                            data_dir=data_dir,
                            context_mode=ctx_mode,
                            predecessor_task_id=failed_task_id,
                        )
                        pending.add(next_task_id)

                # Check fork group completion
                fork_info = _get_fork_group_for_member(failed_task_id, flow_run_id, self._db)
                if fork_info is not None and fork_info[2] == "active":
                    await self._check_fork_join_completion(
                        fork_info[0],
                        flow_run_id,
                        flow,
                        expanded_prompts,
                        data_dir,
                        budget,
                        pending,
                    )

    def _pause_flow(self, flow_run_id: str, reason: str) -> None:
        """Pause the flow: update DB status and emit event."""
        self._paused = True
        run = self._db.get_flow_run(flow_run_id)
        old_status = run.status if run else "unknown"
        self._db.update_flow_run_status(flow_run_id, "paused", error_message=reason)

        # Mark the queue task as paused
        queue_task_id = self._task_id
        if queue_task_id:
            self._db.update_task_queue_status(queue_task_id, "paused", error_message=reason)

        # Emit activity log on the most recent task execution for this run
        latest_task = self._db.get_latest_task_execution(flow_run_id)
        if latest_task:
            self._emit_activity(
                flow_run_id,
                latest_task.id,
                f"\u23f8 Flow paused: {reason}",
            )

        self._emit(
            FlowEvent(
                type=EventType.FLOW_STATUS_CHANGED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "old_status": old_status,
                    "new_status": "paused",
                    "reason": reason,
                },
            )
        )

    def _complete_flow(self, flow_run_id: str, budget: BudgetGuard) -> None:
        """Mark the flow as completed: update DB and emit event."""
        self._db.update_flow_run_elapsed(flow_run_id, budget.elapsed)
        self._db.update_flow_run_status(flow_run_id, "completed")

        # Mark the queue task as completed
        task_id = self._task_id
        if task_id:
            self._db.update_task_queue_status(task_id, "completed")

        self._emit(
            FlowEvent(
                type=EventType.FLOW_COMPLETED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "final_status": "completed",
                    "elapsed_seconds": budget.elapsed,
                },
            )
        )

    def _emit_judge_activity(
        self,
        flow_run_id: str,
        task_id: str,
        task_exec: TaskExecutionRow,
        decision: JudgeDecision,
    ) -> None:
        """Emit activity log for a judge routing decision."""
        self._emit_activity(
            flow_run_id,
            task_id,
            f"\u2696 Judge decided: {task_exec.node_name} \u2192 {decision.target}"
            f" (confidence: {decision.confidence:.2f})",
        )

    def _apply_worktree_mapping(self, cwd: str) -> str:
        """Remap cwd through the worktree if worktree isolation is active."""
        if self._worktree_info is not None:
            return map_cwd_to_worktree(
                cwd, self._worktree_info.original_workspace, self._worktree_info.worktree_path
            )
        return cwd

    def _inject_task_context(self, prompt: str) -> str:
        """Prepend task queue context to a prompt when executing on behalf of a task.

        If ``self._task_id`` is set, loads the task from the database and
        prepends its title and description to the prompt so the subprocess
        has full context about the work item it is processing.
        """
        task_id = self._task_id
        if not task_id:
            return prompt
        task = self._db.get_task(task_id)
        if task is None:
            return prompt
        task_context = f"## Task Context\nTitle: {task.title}\n"
        if task.description:
            task_context += f"Description: {task.description}\n"
        return task_context + "\n" + prompt

    async def _cleanup_worktree(self) -> None:
        """Clean up the git worktree if one was created and cleanup is enabled.

        Resets ``_worktree_info`` to ``None`` so the method is idempotent
        (safe to call from both the main loop exit and ``cancel()``).
        """
        if self._worktree_info is not None and self._worktree_cleanup:
            info = self._worktree_info
            self._worktree_info = None
            try:
                await cleanup_worktree(info)
                logger.info("Cleaned up worktree at %s", info.worktree_path)
            except Exception:
                logger.warning("Failed to cleanup worktree", exc_info=True)
