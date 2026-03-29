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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from flowstate.dsl.ast import ContextMode, EdgeType, ErrorPolicy, NodeType
from flowstate.engine.budget import BudgetGuard
from flowstate.engine.context import (
    build_cross_flow_instructions,
    build_prompt_handoff,
    build_prompt_join,
    build_prompt_none,
    build_prompt_session,
    build_routing_instructions,
    build_task_management_instructions,
    expand_templates,
    get_context_mode,
    resolve_cwd,
)
from flowstate.engine.events import EventType, FlowEvent
from flowstate.engine.harness import DEFAULT_HARNESS, HarnessManager
from flowstate.engine.judge import JudgeContext, JudgeDecision, JudgePauseError, JudgeProtocol
from flowstate.engine.sandbox import SandboxManager
from flowstate.engine.subprocess_mgr import StreamEvent, StreamEventType
from flowstate.engine.worktree import (
    WorktreeInfo,
    cleanup_worktree,
    map_cwd_to_worktree,
    setup_worktree_if_needed,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Sequence

    from flowstate.dsl.ast import Edge, Flow, Node
    from flowstate.engine.harness import Harness
    from flowstate.state.models import TaskExecutionRow, TaskMessageRow
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


def _maybe_append_routing(prompt: str, flow: Flow, node: Node) -> str:
    """Append self-report routing instructions if judge is disabled and node has conditionals."""
    if _use_judge(flow, node):
        return prompt
    cond_edges = _conditional_edge_pairs(_get_outgoing_edges(flow, node.name))
    if cond_edges:
        return prompt + build_routing_instructions(cond_edges)
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


def _use_subtasks(flow: Flow, node: Node) -> bool:
    """Determine if subtask management instructions should be injected.

    Node-level ``subtasks`` overrides flow-level. ``None`` at node level means
    inherit from flow. Default is ``False`` (no subtask management).
    """
    if node.subtasks is not None:
        return node.subtasks
    return flow.subtasks


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
        harness: Harness,
        judge: JudgeProtocol | None = None,
        max_concurrent: int = 4,
        worktree_cleanup: bool = True,
        harness_mgr: HarnessManager | None = None,
        server_base_url: str | None = None,
        sandbox_name: str = "flowstate-claude",
    ) -> None:
        self._db = db
        self._raw_callback = event_callback
        self._harness_mgr = harness_mgr or HarnessManager(default_harness=harness)
        self._server_base_url = server_base_url
        self._judge = judge or JudgeProtocol(harness)
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
        self._expanded_prompts: dict[str, str] = {}
        self._budget: BudgetGuard | None = None
        self._completed_queue: asyncio.Queue[str] | None = None
        # Worktree isolation
        self._worktree_cleanup = worktree_cleanup
        self._worktree_info: WorktreeInfo | None = None
        # Queue task tracking (set by execute() when processing a queue task)
        self._task_id: str | None = None
        # Track which harness each session uses for kill() dispatch
        self._session_harness: dict[str, str] = {}
        # Per-task interrupt coordination (ENGINE-036)
        # When a task is interrupted, its asyncio.Event is cleared; when a
        # message arrives for the interrupted task, the event is set to wake it.
        self._task_resume_events: dict[str, asyncio.Event] = {}
        # Track which tasks are currently interrupted (waiting for user message)
        self._interrupted_tasks: set[str] = set()
        # Map task_execution_id -> session_id for interrupt dispatch
        self._task_session: dict[str, str] = {}
        # Sandbox lifecycle management (ENGINE-059, ENGINE-065)
        self._sandbox_mgr = SandboxManager(sandbox_name=sandbox_name)

    def _resolve_server_url(self, use_sandbox: bool) -> str:
        """Resolve the server URL for artifact API calls.

        For host agents, returns the server base URL as-is.
        For sandboxed agents, replaces the hostname with ``host.docker.internal``
        so the sandbox container can reach the host server.
        """
        base = self._server_base_url or "http://127.0.0.1:9090"
        if use_sandbox:
            from urllib.parse import urlparse, urlunparse

            parsed = urlparse(base)
            # urlparse stores hostname separately; _replace on netloc preserves port
            host_port = (
                f"host.docker.internal:{parsed.port}" if parsed.port else "host.docker.internal"
            )
            return urlunparse(parsed._replace(netloc=host_port))
        return base

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

        # 1. Look up or create flow definition
        flow_def = self._db.get_flow_definition_by_name(flow.name)
        if flow_def is not None:
            flow_definition_id = flow_def.id
        else:
            flow_definition_id = self._db.create_flow_definition(
                name=flow.name, source_dsl="", ast_json=json.dumps({"name": flow.name})
            )

        # 2. Create flow run record (data_dir="" for backwards compat)
        flow_run_id = self._db.create_flow_run(
            flow_definition_id=flow_definition_id,
            data_dir="",
            budget_seconds=flow.budget_seconds,
            on_error=flow.on_error.value,
            default_workspace=workspace,
            params_json=json.dumps(params) if params else None,
            run_id=desired_id,
        )

        # Store task_id and cached task row for task-aware execution
        self._task_id = task_id
        self._task_row = self._db.get_task(task_id) if task_id else None
        if task_id:
            # Link flow_run -> task (only if the task exists in the DB)
            if self._task_row:
                self._db._execute(  # type: ignore[attr-defined]
                    "UPDATE flow_runs SET task_id = ? WHERE id = ?",
                    (task_id, flow_run_id),
                )
                self._db._commit()  # type: ignore[attr-defined]
                # Link task -> flow_run (now that flow_runs row exists)
                self._db.update_task_queue_status(task_id, "running", flow_run_id=flow_run_id)
            else:
                logger.warning("Task %s not found in DB, skipping task linkage", task_id)

        # Resolve workspace to absolute path
        workspace = str(Path(workspace).resolve())

        # If the flow has no workspace, inject the resolved one so resolve_cwd() works
        if flow.workspace is None:
            from dataclasses import replace

            flow = replace(flow, workspace=workspace)

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
                completed_id, flow_run_id, flow, expanded_prompts, budget, pending
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
                flow_run_id, flow, budget, completed_id, pending, expanded_prompts
            )
            return False

        # Check for exit node
        node = flow.nodes[task_exec.node_name]
        if node.node_type == NodeType.EXIT:
            self._complete_flow(flow_run_id, budget)
            return True

        # Evaluate outgoing edges -- separate cross-flow edges (FILE/AWAIT)
        all_outgoing = _get_outgoing_edges(flow, task_exec.node_name)
        outgoing = [e for e in all_outgoing if e.edge_type not in (EdgeType.FILE, EdgeType.AWAIT)]
        file_edges = [e for e in all_outgoing if e.edge_type == EdgeType.FILE]
        await_edges = [e for e in all_outgoing if e.edge_type == EdgeType.AWAIT]

        if not outgoing and not file_edges and not await_edges:
            # Check if this is a fork group member -- fork members have no outgoing edges
            # because the join check handles the continuation
            fork_info = _get_fork_group_for_member(completed_id, flow_run_id, self._db)
            if fork_info is not None:
                _fg_id, _join_name, fg_status = fork_info
                if fg_status == "active":
                    # Only attempt join if the group hasn't already been joined
                    # (a concurrent task completion may have already triggered the join)
                    await self._check_fork_join_completion(
                        _fg_id, flow_run_id, flow, expanded_prompts, budget, pending
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
                pending,
            )

        # Handle unconditional edges
        elif len(outgoing) >= 1 and outgoing[0].edge_type == EdgeType.UNCONDITIONAL:
            edge = outgoing[0]
            assert edge.target is not None
            ctx_mode = get_context_mode(edge, flow)
            is_cycle = _has_been_executed(flow_run_id, edge.target, self._db)
            target_gen = _get_next_generation(flow_run_id, edge.target, self._db) if is_cycle else 1
            next_task_id = self._create_task_execution(
                flow_run_id=flow_run_id,
                node=flow.nodes[edge.target],
                generation=target_gen,
                flow=flow,
                expanded_prompt=expanded_prompts[edge.target],
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
                fork_info[0], flow_run_id, flow, expanded_prompts, budget, pending
            )

        # Handle FILE edges: async cross-flow task filing (ENGINE-028)
        for edge in file_edges:
            await self._handle_file_edge(edge, completed_id, flow_run_id, flow)

        # Handle AWAIT edges: sync cross-flow task filing (ENGINE-028)
        if await_edges:
            await self._handle_await_edges(await_edges, completed_id, flow_run_id, flow)

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

        # Collect summaries from all members (DB-backed)
        member_summaries: dict[str, str | None] = {}
        for m in members:
            artifact = self._db.get_artifact(m.id, "summary")
            member_summaries[m.node_name] = artifact.content if artifact else None

        # Enqueue join target
        join_node = flow.nodes[fork_group.join_node_name]
        join_gen = fork_group.generation + 1
        cwd = resolve_cwd(join_node, flow)
        cwd = self._apply_worktree_mapping(cwd)
        prompt = build_prompt_join(join_node, cwd, member_summaries)

        # Expand template if needed
        expanded = expanded_prompts.get(join_node.name, join_node.prompt)
        if expanded != join_node.prompt:
            prompt = prompt.replace(join_node.prompt, expanded)

        prompt = _maybe_append_routing(prompt, flow, join_node)

        join_task_id = self._db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name=join_node.name,
            node_type=join_node.node_type.value,
            generation=join_gen,
            context_mode=ContextMode.HANDOFF.value,
            cwd=cwd,
            task_dir="",
            prompt_text=prompt,
        )

        # Append task management instructions for join node (ENGINE-040)
        self._maybe_update_task_prompt(prompt, flow, join_node, flow_run_id, join_task_id, None)

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
        from flowstate.state.models import TaskExecutionRow

        assert isinstance(task_exec, TaskExecutionRow)

        if _use_judge(flow, node):
            artifact = self._db.get_artifact(task_exec.id, "summary")
            summary = artifact.content if artifact else None
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
            # Self-report: read the decision artifact from DB with brief poll
            try:
                return await self._read_decision_artifact(task_exec.id, flow_run_id)
            except (FileNotFoundError, ValueError) as e:
                self._pause_flow(flow_run_id, f"Task self-report failed: {e}")
                return None

    async def _read_decision_artifact(
        self, task_execution_id: str, flow_run_id: str
    ) -> JudgeDecision:
        """Read the decision artifact from DB, with brief polling for race conditions.

        The agent may POST the decision artifact just before its process exits.
        Poll up to 5 seconds (0.5s intervals) before declaring missing.
        """
        for _ in range(10):
            artifact = self._db.get_artifact(task_execution_id, "decision")
            if artifact is not None:
                data = json.loads(artifact.content)
                return JudgeDecision(
                    target=data["decision"],
                    reasoning=data["reasoning"],
                    confidence=float(data["confidence"]),
                )
            await asyncio.sleep(0.5)
        raise FileNotFoundError("No decision artifact submitted by agent")

    async def _handle_conditional(
        self,
        outgoing: list[Edge],
        completed_id: str,
        task_exec: object,
        flow_run_id: str,
        flow: Flow,
        expanded_prompts: dict[str, str],
        pending: set[str],
    ) -> None:
        """Evaluate conditional edges and route accordingly."""
        from flowstate.state.models import TaskExecutionRow

        assert isinstance(task_exec, TaskExecutionRow)
        node = flow.nodes[task_exec.node_name]
        cond_edges = _conditional_edge_pairs(outgoing)

        used_judge = _use_judge(flow, node)
        decision = await self._acquire_routing_decision(
            flow, node, task_exec, cond_edges, flow_run_id
        )
        if decision is None:
            return

        if used_judge:
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
        else:
            self._emit_self_report_activity(flow_run_id, completed_id, task_exec, decision)

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
        pending: set[str],
    ) -> None:
        """Evaluate conditional edges; fall back to default edge if no match."""
        from flowstate.state.models import TaskExecutionRow

        assert isinstance(task_exec, TaskExecutionRow)
        node = flow.nodes[task_exec.node_name]

        # Separate default and conditional edges
        default_edge = next(e for e in outgoing if e.edge_type == EdgeType.UNCONDITIONAL)
        cond_edges = _conditional_edge_pairs(outgoing)

        used_judge = _use_judge(flow, node)
        decision = await self._acquire_routing_decision(
            flow, node, task_exec, cond_edges, flow_run_id
        )
        if decision is None:
            return

        if used_judge:
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
        else:
            self._emit_self_report_activity(flow_run_id, completed_id, task_exec, decision)

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
        context_mode: ContextMode,
        source_task: object,
        judge_decision: JudgeDecision,
        is_cycle: bool,
    ) -> str:
        """Create task execution for a conditional transition, handling cycles."""
        from flowstate.state.models import TaskExecutionRow

        assert isinstance(source_task, TaskExecutionRow)

        cwd = resolve_cwd(target_node, flow)
        cwd = self._apply_worktree_mapping(cwd)
        claude_session_id: str | None = None

        if is_cycle and context_mode == ContextMode.HANDOFF:
            # For cycle re-entry with handoff: include source task's summary
            # AND the judge's reasoning as feedback
            artifact = self._db.get_artifact(source_task.id, "summary")
            source_summary = artifact.content if artifact else None
            cycle_context = (
                f"{source_summary or '(No summary available)'}\n\n"
                f"## Judge Feedback\n"
                f"The reviewing judge decided: {judge_decision.reasoning}\n"
                f"You are re-entering this task (generation {generation}) "
                f"to address the feedback."
            )
            prompt = build_prompt_handoff(target_node, cwd, cycle_context)

        elif is_cycle and context_mode == ContextMode.SESSION:
            # Resume the SOURCE task's session (the reviewer), not the
            # target's previous session
            prompt = build_prompt_session(target_node)
            claude_session_id = source_task.claude_session_id

        elif context_mode == ContextMode.HANDOFF:
            # Normal (non-cycle) conditional transition
            artifact = self._db.get_artifact(source_task.id, "summary")
            source_summary = artifact.content if artifact else None
            prompt = build_prompt_handoff(target_node, cwd, source_summary)

        elif context_mode == ContextMode.SESSION:
            prompt = build_prompt_session(target_node)
            claude_session_id = source_task.claude_session_id

        else:  # none
            prompt = build_prompt_none(target_node, cwd)

        # Expand template if needed
        if expanded_prompt != target_node.prompt:
            prompt = prompt.replace(target_node.prompt, expanded_prompt)

        prompt = _maybe_append_routing(prompt, flow, target_node)

        # Inject task queue context if executing on behalf of a task
        prompt = self._inject_task_context(prompt)

        task_id = self._db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name=target_node.name,
            node_type=target_node.node_type.value,
            generation=generation,
            context_mode=context_mode.value,
            cwd=cwd,
            task_dir="",
            prompt_text=prompt,
        )

        # Append task management instructions (ENGINE-040)
        # In conditional transitions, the source_task is the predecessor
        self._maybe_update_task_prompt(
            prompt, flow, target_node, flow_run_id, task_id, source_task.id
        )

        # Store session ID if resuming
        if claude_session_id:
            self._db.update_task_status(task_id, "pending", claude_session_id=claude_session_id)

        return task_id

    # ------------------------------------------------------------------ #
    # Cross-flow task filing (ENGINE-028)
    # ------------------------------------------------------------------ #

    def _get_task_depth(self, task_id: str) -> int:
        """Get the filing depth for a task (stored on the task row, avoids N+1 queries)."""
        task = self._db.get_task(task_id)
        return task.depth if task else 0

    def _build_child_params(self, task_execution_id: str) -> dict[str, str | float | bool]:
        """Build child task params by mapping source node output to child input.

        Reads the source node's output artifact for structured key-value output.
        Falls back to summary artifact content as a ``description`` field.
        Returns an empty dict if neither source is available.
        """
        # Try structured output first
        output_artifact = self._db.get_artifact(task_execution_id, "output")
        if output_artifact:
            try:
                raw = json.loads(output_artifact.content)
                if isinstance(raw, dict):
                    result: dict[str, str | float | bool] = {}
                    for key, value in raw.items():
                        if isinstance(value, str | int | float | bool):
                            result[key] = value
                    if result:
                        return result
            except (json.JSONDecodeError, TypeError):
                pass

        # Fall back to summary as a general description
        summary_artifact = self._db.get_artifact(task_execution_id, "summary")
        if summary_artifact and summary_artifact.content:
            return {"description": summary_artifact.content}

        return {}

    async def _handle_file_edge(
        self,
        edge: Edge,
        source_task_id: str,
        flow_run_id: str,
        flow: Flow,
    ) -> None:
        """Handle a FILE edge: create a child task in the target flow (async).

        FILE edges fire alongside normal edges as side effects.  The child
        task is queued for later processing; the current flow continues
        without waiting.
        """
        task_exec = self._db.get_task_execution(source_task_id)
        if task_exec is None:
            return

        # Check depth limit (prevent infinite filing chains)
        if self._task_id:
            depth = self._get_task_depth(self._task_id)
            if depth >= 10:
                logger.warning(
                    "Task filing depth limit reached (10), skipping file edge to %s",
                    edge.target,
                )
                return

        # Read the source node's summary artifact for the child task description
        summary_artifact = self._db.get_artifact(source_task_id, "summary")
        summary = (
            summary_artifact.content if summary_artifact else f"Filed from {task_exec.node_name}"
        )

        # Build child task params from source node output (ENGINE-029)
        parent_task = self._db.get_task(self._task_id) if self._task_id else None
        child_params = self._build_child_params(source_task_id)
        parent_title = parent_task.title if parent_task else "Filed task"

        child_task_id = self._db.create_task(
            flow_name=edge.target or flow.name,
            title=f"{task_exec.node_name}: {parent_title}",
            description=summary,
            params_json=json.dumps(child_params) if child_params else None,
            parent_task_id=self._task_id,
            created_by=f"flow:{flow.name}/node:{task_exec.node_name}",
        )

        self._emit_activity(
            flow_run_id,
            source_task_id,
            f"Filed task to {edge.target}: {child_task_id[:8]}",
        )

    async def _handle_await_edges(
        self,
        edges: list[Edge],
        source_task_id: str,
        flow_run_id: str,
        flow: Flow,
    ) -> None:
        """Handle AWAIT edges: create child tasks and wait for them to complete.

        AWAIT edges block the current flow until every child task reaches a
        terminal status (completed, failed, or cancelled).
        """
        task_exec = self._db.get_task_execution(source_task_id)
        if task_exec is None:
            return

        # Check depth limit
        if self._task_id:
            depth = self._get_task_depth(self._task_id)
            if depth >= 10:
                logger.warning(
                    "Task filing depth limit reached (10), skipping await edges",
                )
                return

        # Build child task params from source node output (ENGINE-029)
        parent_task = self._db.get_task(self._task_id) if self._task_id else None
        child_params = self._build_child_params(source_task_id)
        parent_title = parent_task.title if parent_task else "Filed task"

        for edge in edges:
            summary_artifact = self._db.get_artifact(source_task_id, "summary")
            summary = (
                summary_artifact.content
                if summary_artifact
                else f"Awaited from {task_exec.node_name}"
            )

            child_task_id = self._db.create_task(
                flow_name=edge.target or flow.name,
                title=f"{task_exec.node_name}: {parent_title}",
                description=summary,
                params_json=json.dumps(child_params) if child_params else None,
                parent_task_id=self._task_id,
                created_by=f"flow:{flow.name}/node:{task_exec.node_name}",
            )

            self._emit_activity(
                flow_run_id,
                source_task_id,
                f"Awaiting task in {edge.target}: {child_task_id[:8]}",
            )

            # Set current task to 'waiting' status
            if self._task_id:
                self._db.update_task_queue_status(self._task_id, "waiting")

            # Wait for child task to complete
            await self._wait_for_child_task(child_task_id, flow_run_id, source_task_id)

    async def _wait_for_child_task(
        self,
        child_task_id: str,
        flow_run_id: str,
        source_task_id: str,
    ) -> None:
        """Poll until a child task reaches a terminal status."""
        while True:
            child = self._db.get_task(child_task_id)
            if child is None:
                break
            if child.status in ("completed", "failed", "cancelled"):
                self._emit_activity(
                    flow_run_id,
                    source_task_id,
                    f"Child task {child_task_id[:8]} finished with status: {child.status}",
                )
                break
            await asyncio.sleep(2)

        # Resume current task
        if self._task_id:
            self._db.update_task_queue_status(self._task_id, "running")

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

        # Wake up any interrupted tasks so their coroutines can exit
        for resume_event in self._task_resume_events.values():
            resume_event.set()

        # Kill all running subprocesses via their respective harnesses.
        # Use _task_session (set by _execute_single_task) because the DB's
        # claude_session_id is only populated on task completion -- it will be
        # None for tasks that are still running.
        for task_id in list(self._running_tasks):
            sid = self._task_session.get(task_id)
            if sid is None:
                # Fallback to DB in case _task_session was not populated
                task_exec = self._db.get_task_execution(task_id)
                if task_exec:
                    sid = task_exec.claude_session_id
            if sid:
                harness_name = self._session_harness.get(sid, DEFAULT_HARNESS)
                harness = self._harness_mgr.get(harness_name)
                await harness.kill(sid)
            atask = self._running_tasks.get(task_id)
            if atask:
                atask.cancel()

        # Wait for all tasks to finish cancellation
        if self._running_tasks:
            await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
        self._running_tasks.clear()

        # Mark all still-active tasks as failed (due to cancellation).
        # The DB schema only allows 'failed'/'skipped' for terminal error states.
        # Include "interrupted" status (ENGINE-036) since interrupted tasks are
        # still logically active.
        tasks = self._db.list_task_executions(flow_run_id)
        for task in tasks:
            if task.status in ("running", "pending", "waiting", "interrupted"):
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

        # Re-create task execution with the same prompt
        new_prompt = old_task.prompt_text

        new_task_id = self._db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name=old_task.node_name,
            node_type=old_task.node_type,
            generation=new_gen,
            context_mode=old_task.context_mode,
            cwd=old_task.cwd,
            task_dir="",
            prompt_text=new_prompt,
        )

        # Update task management URLs to reference the new task_execution_id (ENGINE-040)
        if task_execution_id in new_prompt:
            updated_prompt = new_prompt.replace(task_execution_id, new_task_id)
            self._db._execute(  # type: ignore[attr-defined]
                "UPDATE task_executions SET prompt_text = ? WHERE id = ?",
                (updated_prompt, new_task_id),
            )
            self._db._commit()  # type: ignore[attr-defined]

        # Add to pending set so it gets picked up
        self._pending_tasks.add(new_task_id)

        # If the flow is paused (e.g., on_error=pause), resume it so the main
        # loop wakes up and executes the retried task.
        if self._paused:
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
                        "reason": "Task retried",
                    },
                )
            )
            self._resume_event.set()

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
                    expanded = self._expanded_prompts.get(edge.target, "")
                    next_task_id = self._create_task_execution(
                        flow_run_id=flow_run_id,
                        node=self._flow.nodes[edge.target],
                        generation=1,
                        flow=self._flow,
                        expanded_prompt=expanded,
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
                self._budget or BudgetGuard(3600),
                self._pending_tasks,
            )

        # If the flow is paused (e.g., on_error=pause), resume it so the main
        # loop wakes up and processes the next task(s).
        if self._paused:
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
                        "reason": "Task skipped",
                    },
                )
            )
            self._resume_event.set()

    async def restart_from_task(
        self,
        flow: Flow,
        flow_run_id: str,
        task_execution_id: str,
        action: str,
        parameters: dict[str, str | float | bool] | None = None,
    ) -> str:
        """Restart a cancelled/failed flow from a specific task.

        Sets up executor state (normally done in ``execute()``) and then
        calls ``retry_task()`` or ``skip_task()`` before entering the main
        loop.  This allows retry/skip to work on flows whose executor was
        removed after cancellation (ENGINE-053).

        Args:
            flow: The parsed Flow AST.
            flow_run_id: The existing flow run ID to restart.
            task_execution_id: The failed task to retry or skip.
            action: ``"retry"`` or ``"skip"``.
            parameters: Template parameters (used for prompt expansion).

        Returns:
            The flow_run_id (same as input).
        """
        if action not in ("retry", "skip"):
            raise ValueError(f"Invalid action: {action!r}. Must be 'retry' or 'skip'.")

        params = parameters or {}

        # 1. Load the flow run from DB
        flow_run = self._db.get_flow_run(flow_run_id)
        if flow_run is None:
            raise ValueError(f"Flow run not found: {flow_run_id}")

        workspace = flow_run.default_workspace or "."

        # If the flow has no workspace, inject the resolved one
        if flow.workspace is None:
            from dataclasses import replace

            flow = replace(flow, workspace=workspace)

        # 2. Expand templates in all node prompts
        expanded_prompts: dict[str, str] = {}
        for node_name, node in flow.nodes.items():
            expanded_prompts[node_name] = expand_templates(node.prompt, params)

        # 3. Initialize budget guard (use remaining budget or reset)
        budget = BudgetGuard(flow.budget_seconds)

        # 4. Store shared state for control operations
        self._flow = flow
        self._flow_run_id = flow_run_id
        self._expanded_prompts = expanded_prompts
        self._budget = budget
        self._paused = False
        self._cancelled = False
        self._resume_event.clear()
        # Task queue context: restart_from_task is not task-aware
        self._task_id = None
        self._task_row = None

        # 5. Un-cancel the flow: transition to running
        old_status = flow_run.status
        self._db.update_flow_run_status(flow_run_id, "running")
        self._emit(
            FlowEvent(
                type=EventType.FLOW_STATUS_CHANGED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "old_status": old_status,
                    "new_status": "running",
                    "reason": f"Restarted via {action} on task {task_execution_id}",
                },
            )
        )

        # 6. Call retry_task() or skip_task() to create the new task execution
        if action == "retry":
            await self.retry_task(flow_run_id, task_execution_id)
        else:
            await self.skip_task(flow_run_id, task_execution_id)

        # 7. Enter the main loop
        pending = self._pending_tasks
        completed_queue: asyncio.Queue[str] = asyncio.Queue()
        self._completed_queue = completed_queue

        while pending or self._running_tasks or self._paused:
            if self._cancelled:
                break

            if self._paused:
                await self._resume_event.wait()
                self._resume_event.clear()
                if self._cancelled:
                    break
                continue

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
                        budget=budget,
                        completed_queue=completed_queue,
                    )
                )
                self._running_tasks[task_id] = atask

            if self._cancelled:
                break

            if self._paused and not self._running_tasks:
                continue

            if self._running_tasks and not completed_queue.qsize():
                completed_id = await completed_queue.get()
            elif completed_queue.qsize():
                completed_id = completed_queue.get_nowait()
            else:
                break

            self._running_tasks.pop(completed_id, None)
            self._semaphore.release()

            should_stop = await self._process_completed_task(
                completed_id, flow_run_id, flow, expanded_prompts, budget, pending
            )
            if should_stop:
                return flow_run_id

        return flow_run_id

    # ------------------------------------------------------------------ #
    # Wait node execution
    # ------------------------------------------------------------------ #

    async def _execute_wait_node(
        self,
        flow_run_id: str,
        task_execution_id: str,
        node: Node,
        completed_queue: asyncio.Queue[str],
    ) -> None:
        """Handle a WAIT node: compute wait_until, sleep, then mark completed.

        Wait nodes don't launch a Claude Code subprocess. They pause the flow
        for a specified duration (``wait_delay_seconds``) or until a cron time
        (``wait_until_cron``). Wait time does NOT count toward the flow budget.
        """
        now = datetime.now(UTC)
        if node.wait_delay_seconds is not None and node.wait_delay_seconds > 0:
            wait_until = now + timedelta(seconds=node.wait_delay_seconds)
        elif node.wait_until_cron:
            from croniter import croniter

            cron = croniter(node.wait_until_cron, now)
            wait_until = cron.get_next(datetime)
        else:
            # Fallback: no delay configured (shouldn't happen if type checker validates)
            wait_until = now

        # Set task to 'waiting' with the computed wait_until
        self._db.update_task_status(
            task_execution_id,
            "waiting",
            wait_until=wait_until.isoformat(),
            started_at=_now_iso(),
        )

        self._emit_activity(
            flow_run_id,
            task_execution_id,
            f"Wait node '{node.name}': waiting until {wait_until.isoformat()}",
        )

        self._emit(
            FlowEvent(
                type=EventType.TASK_WAITING,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "node_name": node.name,
                    "wait_until": wait_until.isoformat(),
                    "reason": "delay" if node.wait_delay_seconds else "cron",
                },
            )
        )

        # Track task node history for queue tasks
        if self._task_id:
            self._db.update_task_queue_status(self._task_id, "running", current_node=node.name)
            self._db.add_task_node_history(self._task_id, node.name, flow_run_id)

        # Poll until wait expires (budget-exempt -- don't add to budget)
        while not self._cancelled:
            remaining = (wait_until - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(min(5.0, remaining))

        if self._cancelled:
            self._db.update_task_status(
                task_execution_id,
                "failed",
                error_message="Flow cancelled",
                completed_at=_now_iso(),
            )
            await completed_queue.put(task_execution_id)
            return

        # Mark as completed (no budget charge -- wait time is free)
        self._db.update_task_status(
            task_execution_id,
            "completed",
            completed_at=_now_iso(),
            elapsed_seconds=0.0,
        )

        if self._task_id:
            self._db.complete_task_node_history(self._task_id, node.name)

        self._emit(
            FlowEvent(
                type=EventType.TASK_COMPLETED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "node_name": node.name,
                    "exit_code": 0,
                    "elapsed_seconds": 0.0,
                },
            )
        )

        await completed_queue.put(task_execution_id)

    # ------------------------------------------------------------------ #
    # Fence node execution (ENGINE-031)
    # ------------------------------------------------------------------ #

    async def _execute_fence_node(
        self,
        flow_run_id: str,
        task_execution_id: str,
        node: Node,
        completed_queue: asyncio.Queue[str],
    ) -> None:
        """Handle a FENCE node: wait until all other tasks have reached this point.

        A fence is a synchronization barrier. It marks the arriving task as
        ``waiting`` and polls until every other running/pending task in the same
        flow run has either completed, failed, skipped, or is also waiting at a
        fence. Once all tasks are synchronized, the fence is released and the
        task is marked ``completed``.

        Fence nodes have no prompt -- no Claude Code subprocess is launched.
        Fence time does NOT count toward the flow budget.
        """
        self._db.update_task_status(task_execution_id, "waiting", started_at=_now_iso())

        self._emit_activity(
            flow_run_id,
            task_execution_id,
            f"Fence '{node.name}': waiting for all tasks to arrive",
        )

        self._emit(
            FlowEvent(
                type=EventType.TASK_WAITING,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "node_name": node.name,
                    "reason": "fence",
                },
            )
        )

        # Track task node history for queue tasks
        if self._task_id:
            self._db.update_task_queue_status(self._task_id, "running", current_node=node.name)
            self._db.add_task_node_history(self._task_id, node.name, flow_run_id)

        # Poll until all running tasks in this flow run are at the fence (or completed)
        while not self._cancelled:
            all_tasks = self._db.list_task_executions(flow_run_id)
            # Check if any other task is still running or pending (not yet at a fence)
            blocking = [
                t
                for t in all_tasks
                if t.id != task_execution_id and t.status in ("running", "pending")
            ]

            if not blocking:
                # All other tasks are completed/waiting/failed/skipped -- fence passes
                break

            await asyncio.sleep(0.5)

        if self._cancelled:
            self._db.update_task_status(
                task_execution_id,
                "failed",
                error_message="Flow cancelled",
                completed_at=_now_iso(),
            )
            await completed_queue.put(task_execution_id)
            return

        # Mark as completed (no budget charge -- fence wait time is free)
        self._db.update_task_status(
            task_execution_id,
            "completed",
            completed_at=_now_iso(),
            elapsed_seconds=0.0,
        )

        if self._task_id:
            self._db.complete_task_node_history(self._task_id, node.name)

        self._emit_activity(
            flow_run_id,
            task_execution_id,
            f"Fence '{node.name}': all tasks arrived, proceeding",
        )

        self._emit(
            FlowEvent(
                type=EventType.TASK_COMPLETED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "node_name": node.name,
                    "exit_code": 0,
                    "elapsed_seconds": 0.0,
                },
            )
        )

        await completed_queue.put(task_execution_id)

    # ------------------------------------------------------------------ #
    # Atomic node lock (ENGINE-032)
    # ------------------------------------------------------------------ #

    async def _acquire_atomic_lock(
        self,
        flow_run_id: str,
        task_execution_id: str,
        node: Node,
    ) -> None:
        """Wait until no other flow run has a running task for this atomic node.

        Atomic nodes provide mutual exclusion per (flow_name, node_name) across
        all concurrent flow runs. This method polls the database for any other
        task execution with the same ``node_name`` that is currently running
        (across ALL flow runs, not just this one). Once no other execution is
        running, the method returns and the caller can proceed to launch the
        subprocess.
        """
        first_check = True
        while not self._cancelled:
            row = self._db._fetchone(  # type: ignore[attr-defined]
                """SELECT COUNT(*) as cnt FROM task_executions
                   WHERE node_name = ? AND status = 'running'
                   AND id != ?""",
                (node.name, task_execution_id),
            )

            if row and row["cnt"] == 0:
                break  # No other run has this atomic node running

            if first_check:
                self._db.update_task_status(task_execution_id, "waiting", started_at=_now_iso())
                self._emit(
                    FlowEvent(
                        type=EventType.TASK_WAITING,
                        flow_run_id=flow_run_id,
                        timestamp=_now_iso(),
                        payload={
                            "task_execution_id": task_execution_id,
                            "node_name": node.name,
                            "reason": "atomic",
                        },
                    )
                )
                first_check = False

            self._emit_activity(
                flow_run_id,
                task_execution_id,
                f"Atomic '{node.name}': waiting for exclusive access",
            )

            await asyncio.sleep(0.5)

        if not self._cancelled:
            self._emit_activity(
                flow_run_id,
                task_execution_id,
                f"Atomic '{node.name}': acquired exclusive lock",
            )

    # ------------------------------------------------------------------ #
    # Task-level interrupt + messaging (ENGINE-036)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_user_messages(messages: Sequence[TaskMessageRow]) -> str:
        """Format pending user messages into a re-invocation prompt.

        The formatted prompt instructs the agent to address the user's
        messages before continuing its task.
        """
        lines = ["The user sent you the following message(s) while you were working:", ""]
        for msg in messages:
            lines.append(f'- "{msg.message}"')
        lines.append("")
        lines.append("Address these messages, then continue your task.")
        return "\n".join(lines)

    async def interrupt_task(self, task_execution_id: str) -> None:
        """Interrupt a running task: cancel the current agent turn.

        The execution coroutine transitions the task to ``interrupted`` and
        waits for a user message before resuming.  Calling interrupt on an
        already-interrupted task is a no-op (idempotent).
        """
        task = self._db.get_task_execution(task_execution_id)
        if task is None:
            raise RuntimeError(f"Task execution not found: {task_execution_id}")

        # Idempotent: already interrupted
        if task.status == "interrupted":
            return

        if task.status != "running":
            raise RuntimeError(
                f"Cannot interrupt task {task_execution_id} with status '{task.status}'"
            )

        # Resolve harness for the session and send interrupt signal.
        # Use _task_session (set by _execute_single_task) because the DB's
        # claude_session_id is only populated on task completion.
        session_id = self._task_session.get(task_execution_id) or task.claude_session_id
        if session_id:
            harness_name = self._session_harness.get(session_id, DEFAULT_HARNESS)
            harness = self._harness_mgr.get(harness_name)
            await harness.interrupt(session_id)

        # Mark as interrupted in DB
        self._db.update_task_status(task_execution_id, "interrupted")
        self._interrupted_tasks.add(task_execution_id)

        flow_run_id = task.flow_run_id
        self._emit(
            FlowEvent(
                type=EventType.TASK_INTERRUPTED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "node_name": task.node_name,
                },
            )
        )

    async def send_message(self, task_execution_id: str, message: str) -> None:
        """Send a user message to a running or interrupted task.

        If the task is running, the message is queued and will be delivered
        after the current agent turn finishes.  If the task is interrupted,
        the message is queued and the task is signalled to resume.

        Raises ``RuntimeError`` for completed/failed/other terminal tasks.
        """
        task = self._db.get_task_execution(task_execution_id)
        if task is None:
            raise RuntimeError(f"Task execution not found: {task_execution_id}")

        if task.status not in ("running", "interrupted"):
            raise RuntimeError(
                f"Cannot send message to task {task_execution_id} with status '{task.status}'"
            )

        # Enqueue the message in the DB
        self._db.insert_task_message(task_execution_id, message)

        # If interrupted, signal the execution coroutine to resume
        if task.status == "interrupted":
            resume_event = self._task_resume_events.get(task_execution_id)
            if resume_event is not None:
                resume_event.set()

    # ------------------------------------------------------------------ #
    # Stream event helper (shared by initial prompt + re-invocation)
    # ------------------------------------------------------------------ #

    async def _stream_events(
        self,
        stream: AsyncGenerator[StreamEvent, None],
        task_execution_id: str,
        flow_run_id: str,
        session_id: str | None,
    ) -> int | None:
        """Consume a harness event stream, logging events and returning the exit code.

        This is the inner loop shared by the initial prompt and any
        re-invocation prompts (ENGINE-036). It returns the process exit code
        (``0`` on success) or ``None`` if no exit event was received (e.g.
        the stream was interrupted).
        """
        exit_code: int | None = None
        async for event in stream:
            log_type = _to_log_type(event.type)
            self._db.insert_task_log(task_execution_id, log_type, event.raw)
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
                real_sid = event.content["session_id"]
                # Update the session→harness mapping with the real session ID
                if session_id and session_id in self._session_harness:
                    harness_name = self._session_harness.pop(session_id)
                    self._session_harness[real_sid] = harness_name
        return exit_code

    def _session_harness_session_for(
        self, task_execution_id: str, fallback_session_id: str | None
    ) -> str | None:
        """Return the current session ID for a task, or the fallback.

        After streaming, the real session ID may have replaced the original.
        This method is a no-op pass-through; the session ID tracking is
        handled inside ``_stream_events``. We simply return the fallback
        because the caller already holds the correct value.
        """
        return fallback_session_id

    # ------------------------------------------------------------------ #
    # Task execution
    # ------------------------------------------------------------------ #

    async def _execute_single_task(
        self,
        flow_run_id: str,
        task_execution_id: str,
        flow: Flow,
        expanded_prompts: dict[str, str],
        budget: BudgetGuard,
        completed_queue: asyncio.Queue[str],
    ) -> None:
        """Execute a single task subprocess and handle its output."""
        task_exec = self._db.get_task_execution(task_execution_id)
        if task_exec is None:
            await completed_queue.put(task_execution_id)
            return

        node = flow.nodes[task_exec.node_name]

        # Wait nodes don't launch a subprocess -- they just pause until a time/duration elapses
        if node.node_type == NodeType.WAIT:
            await self._execute_wait_node(flow_run_id, task_execution_id, node, completed_queue)
            return

        # Fence nodes are synchronization barriers -- no subprocess, just wait for all tasks
        if node.node_type == NodeType.FENCE:
            await self._execute_fence_node(flow_run_id, task_execution_id, node, completed_queue)
            return

        # Atomic nodes acquire an exclusive lock before launching the subprocess
        if node.node_type == NodeType.ATOMIC:
            await self._acquire_atomic_lock(flow_run_id, task_execution_id, node)
            if self._cancelled:
                self._db.update_task_status(
                    task_execution_id,
                    "failed",
                    error_message="Flow cancelled",
                    completed_at=_now_iso(),
                )
                await completed_queue.put(task_execution_id)
                return

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

        session_id: str | None = None
        # Create a per-task resume event for interrupt→wait→resume coordination
        resume_event = asyncio.Event()
        self._task_resume_events[task_execution_id] = resume_event
        try:
            skip_perms = flow.skip_permissions
            session_id = task_exec.claude_session_id or str(uuid.uuid4())

            # Resolve harness: node-level overrides flow-level
            harness_name = node.harness or flow.harness
            harness: Harness = self._harness_mgr.get(harness_name)
            # Track session -> harness name for kill() dispatch
            self._session_harness[session_id] = harness_name
            # Track task -> session for interrupt dispatch
            self._task_session[task_execution_id] = session_id

            # Resolve sandbox settings: node-level overrides flow-level (ENGINE-059)
            use_sandbox = node.sandbox if node.sandbox is not None else flow.sandbox

            # Build artifact API env vars for the agent subprocess (ENGINE-067)
            artifact_env = {
                "FLOWSTATE_SERVER_URL": self._resolve_server_url(use_sandbox),
                "FLOWSTATE_RUN_ID": flow_run_id,
                "FLOWSTATE_TASK_ID": task_execution_id,
            }

            if use_sandbox:
                from flowstate.engine.acp_client import AcpHarness

                # Read command/env from the harness (available on AcpHarness)
                harness_command: list[str] = getattr(harness, "command", [])
                harness_env: dict[str, str] | None = getattr(harness, "env", None)

                # Merge artifact env into harness env
                merged_env = {**(harness_env or {}), **artifact_env}

                wrapped_cmd = self._sandbox_mgr.wrap_command(harness_command)
                # Sandbox connect takes a few seconds; use a longer timeout.
                # session_cwd="/sandbox" ensures the agent creates sessions
                # inside the sandbox filesystem, not the host path.
                harness = AcpHarness(
                    command=wrapped_cmd,
                    env=merged_env,
                    init_timeout=120.0,
                    session_cwd="/sandbox",
                )
                # openshell runs on the host, so cwd must be a valid host path.
                # The agent inside the sandbox works in /sandbox automatically.
                task_exec.cwd = str(Path.cwd())
            else:
                # Inject artifact env vars into process environment (ENGINE-067).
                # For non-sandbox harnesses the env dict is already set at
                # construction time, so we inject into os.environ which the
                # ACP subprocess inherits.  This is safe in asyncio because
                # no await occurs between the update and the run_task call.
                os.environ.update(artifact_env)

            # Determine if this is a session resume or fresh task
            if task_exec.context_mode == ContextMode.SESSION.value and task_exec.claude_session_id:
                stream = harness.run_task_resume(
                    task_exec.prompt_text,
                    task_exec.cwd,
                    task_exec.claude_session_id,
                    skip_permissions=skip_perms,
                )
            else:
                stream = harness.run_task(
                    task_exec.prompt_text,
                    task_exec.cwd,
                    session_id,
                    skip_permissions=skip_perms,
                )

            # Stream events from the initial prompt
            exit_code: int | None = None
            exit_code = await self._stream_events(
                stream, task_execution_id, flow_run_id, session_id
            )
            # Capture the session_id that may have been updated during streaming
            session_id = self._session_harness_session_for(task_execution_id, session_id)

            # ---- Re-invocation loop (ENGINE-036, ENGINE-051) ----
            # After each agent turn, check for queued user messages. If the
            # task was interrupted, wait for a resume signal. Keep looping
            # until no more messages are pending.
            #
            # The interrupt check MUST come before the exit_code check because
            # harness.interrupt() sends an ACP cancel which yields exit_code=-1.
            # Without this ordering the loop would break on exit_code != 0 and
            # the task would fall through to the error handler instead of
            # waiting for user input (ENGINE-051).
            while not self._cancelled:
                # Check for interrupt FIRST — interrupt returns exit_code=-1
                # but should wait for user input, not fail.
                if task_execution_id in self._interrupted_tasks:
                    # The task is interrupted -- wait for a user message
                    resume_event.clear()
                    await resume_event.wait()
                    # Woke up: either a message arrived or flow was cancelled
                    if self._cancelled:
                        break
                    self._interrupted_tasks.discard(task_execution_id)
                    # Transition back to running
                    self._db.update_task_status(task_execution_id, "running")
                    self._emit(
                        FlowEvent(
                            type=EventType.FLOW_STATUS_CHANGED,
                            flow_run_id=flow_run_id,
                            timestamp=_now_iso(),
                            payload={
                                "old_status": "interrupted",
                                "new_status": "running",
                                "reason": "User sent message to interrupted task",
                                "task_execution_id": task_execution_id,
                            },
                        )
                    )
                    # After interrupt, skip the exit_code check and go straight
                    # to fetching the queued user message(s). The exit_code from
                    # the interrupted stream (-1) is stale and must not cause a
                    # break; the re-invocation below will produce a fresh one.
                elif exit_code != 0:
                    # Normal exit code check — non-zero means the task failed.
                    break

                # Fetch unprocessed messages from the DB
                messages = self._db.get_unprocessed_messages(task_execution_id)
                if not messages:
                    break

                # Mark messages as processed and re-invoke with combined prompt.
                # Use run_task_resume() instead of prompt() because the ACP
                # session is destroyed after each subprocess exits (its finally
                # block removes the session from _sessions).  run_task_resume()
                # spawns a fresh subprocess and loads the persisted session
                # from disk, which is the correct way to continue after the
                # original stream has ended (ENGINE-052).
                self._db.mark_messages_processed(task_execution_id)
                combined_prompt = self._format_user_messages(messages)
                assert session_id is not None  # guaranteed by initialization above
                cwd = task_exec.cwd
                re_stream = harness.run_task_resume(
                    combined_prompt,
                    cwd,
                    session_id,
                    skip_permissions=skip_perms,
                )
                exit_code = await self._stream_events(
                    re_stream, task_execution_id, flow_run_id, session_id
                )

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
                # Auto-complete remaining subtasks (ENGINE-056)
                if _use_subtasks(flow, node):
                    completed_subs = self._db.complete_remaining_subtasks(task_execution_id)
                    for sub in completed_subs:
                        if sub.status == "done":
                            self._emit(
                                FlowEvent(
                                    type=EventType.SUBTASK_UPDATED,
                                    flow_run_id=flow_run_id,
                                    timestamp=_now_iso(),
                                    payload={
                                        "task_execution_id": task_execution_id,
                                        "subtask_id": sub.id,
                                        "title": sub.title,
                                        "status": "done",
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
            # Clean up per-task coordination state
            self._task_resume_events.pop(task_execution_id, None)
            self._interrupted_tasks.discard(task_execution_id)
            self._task_session.pop(task_execution_id, None)
            # Clean up session→harness tracking to prevent unbounded growth
            if session_id is not None:
                self._session_harness.pop(session_id, None)
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
        context_mode: ContextMode,
        predecessor_task_id: str | None = None,
    ) -> str:
        """Create a task execution record."""
        cwd = resolve_cwd(node, flow)
        cwd = self._apply_worktree_mapping(cwd)

        # Build the full prompt based on context mode
        if context_mode == ContextMode.HANDOFF and predecessor_task_id:
            artifact = self._db.get_artifact(predecessor_task_id, "summary")
            summary = artifact.content if artifact else None
            prompt = build_prompt_handoff(node, cwd, summary)
        elif context_mode == ContextMode.SESSION:
            prompt = build_prompt_session(node)
        else:
            prompt = build_prompt_none(node, cwd)

        # Use expanded prompt in the prompt text (replace the node.prompt with expanded version)
        # The prompt builders use node.prompt, so we need to substitute
        if expanded_prompt != node.prompt:
            prompt = prompt.replace(node.prompt, expanded_prompt)

        prompt = _maybe_append_routing(prompt, flow, node)

        # Append cross-flow output instructions if this node has FILE/AWAIT edges (ENGINE-029)
        cross_flow_targets = [
            e.target
            for e in flow.edges
            if e.source == node.name and e.edge_type in (EdgeType.FILE, EdgeType.AWAIT) and e.target
        ]
        if cross_flow_targets:
            prompt += build_cross_flow_instructions(cross_flow_targets)

        # Inject task queue context if executing on behalf of a task
        prompt = self._inject_task_context(prompt)

        task_id = self._db.create_task_execution(
            flow_run_id=flow_run_id,
            node_name=node.name,
            node_type=node.node_type.value,
            generation=generation,
            context_mode=context_mode.value,
            cwd=cwd,
            task_dir="",
            prompt_text=prompt,
        )

        # Save the assembled prompt as an input artifact
        self._db.save_artifact(task_id, "input", prompt, "text/markdown")

        # Append task management instructions after task creation so we have
        # the task_execution_id for API URLs (ENGINE-040)
        self._maybe_update_task_prompt(
            prompt, flow, node, flow_run_id, task_id, predecessor_task_id
        )

        return task_id

    def _maybe_update_task_prompt(
        self,
        prompt: str,
        flow: Flow,
        node: Node,
        flow_run_id: str,
        task_id: str,
        predecessor_task_id: str | None,
    ) -> None:
        """Append task management instructions and update the DB if needed.

        Only injects instructions when tasks are enabled for this node,
        the server_base_url is configured, and the flow_run_id is available.
        """
        if not _use_subtasks(flow, node):
            return
        if self._server_base_url is None:
            return

        updated = prompt + build_task_management_instructions(
            server_base_url=self._server_base_url,
            run_id=flow_run_id,
            task_execution_id=task_id,
            predecessor_task_execution_id=predecessor_task_id,
        )
        self._db._execute(  # type: ignore[attr-defined]
            "UPDATE task_executions SET prompt_text = ? WHERE id = ?",
            (updated, task_id),
        )
        self._db._commit()  # type: ignore[attr-defined]

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

    def _emit_self_report_activity(
        self,
        flow_run_id: str,
        task_id: str,
        task_exec: TaskExecutionRow,
        decision: JudgeDecision,
    ) -> None:
        """Emit activity log for a self-report routing decision."""
        self._emit_activity(
            flow_run_id,
            task_id,
            f"\U0001f4cb Self-report routed: {task_exec.node_name} \u2192 {decision.target}"
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

        Uses the cached ``_task_row`` (loaded once in ``execute()``) to avoid
        repeated DB queries on every task creation.
        """
        task = self._task_row
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
