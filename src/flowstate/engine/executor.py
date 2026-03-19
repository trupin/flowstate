"""Flow executor -- the main orchestration loop for executing flows.

Drives linear (sequential) flow execution by coordinating subprocess execution,
context assembly, budget tracking, and state persistence. Handles the full task
lifecycle: create task directory, assemble prompt, launch subprocess, stream events,
evaluate outgoing edges, and detect flow completion.

This module establishes the executor skeleton that subsequent issues (fork-join,
conditional branching, control operations) extend.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from flowstate.dsl.ast import ContextMode, EdgeType, ErrorPolicy, NodeType
from flowstate.engine.budget import BudgetGuard
from flowstate.engine.context import (
    build_prompt_handoff,
    build_prompt_none,
    build_prompt_session,
    create_task_dir,
    expand_templates,
    get_context_mode,
    read_summary,
    resolve_cwd,
)
from flowstate.engine.events import EventType, FlowEvent
from flowstate.engine.subprocess_mgr import StreamEventType, SubprocessManager

if TYPE_CHECKING:
    from collections.abc import Callable

    from flowstate.dsl.ast import Edge, Flow, Node
    from flowstate.state.repository import FlowstateDB


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


class FlowExecutor:
    """Executes a flow by orchestrating subprocess launches, budget tracking, and state.

    The executor processes tasks sequentially for linear flows (each task depends
    on the previous one). Concurrency is bounded by an asyncio.Semaphore for
    forward compatibility with fork-join execution added by later issues.
    """

    def __init__(
        self,
        db: FlowstateDB,
        event_callback: Callable[[FlowEvent], None],
        subprocess_mgr: SubprocessManager,
        max_concurrent: int = 4,
    ) -> None:
        self._db = db
        self._emit = event_callback
        self._subprocess_mgr = subprocess_mgr
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._paused = False
        self._cancelled = False

    async def execute(
        self, flow: Flow, params: dict[str, str | float | bool], workspace: str
    ) -> str:
        """Execute a flow and return the flow_run_id.

        Creates the flow run record, expands template variables, enqueues the
        entry node, and processes tasks through the main loop until the exit
        node completes, the flow is paused, or it is cancelled.
        """
        flow_run_id = str(uuid.uuid4())
        data_dir = os.path.expanduser(f"~/.flowstate/runs/{flow_run_id}")

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
        )
        data_dir = os.path.expanduser(f"~/.flowstate/runs/{flow_run_id}")

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

        # 5. Enqueue entry node
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

        # 6. Main loop
        pending: set[str] = {entry_task_id}
        completed_queue: asyncio.Queue[str] = asyncio.Queue()

        while pending or self._running_tasks:
            if self._paused or self._cancelled:
                break

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
            if task_exec is None:
                continue

            if task_exec.status == "failed":
                self._handle_error(flow_run_id, flow, budget)
                continue

            # Check for exit node
            node = flow.nodes[task_exec.node_name]
            if node.node_type == NodeType.EXIT:
                self._complete_flow(flow_run_id, budget)
                return flow_run_id

            # Evaluate outgoing edges
            outgoing = _get_outgoing_edges(flow, task_exec.node_name)

            if not outgoing:
                # Defensive: no outgoing edges from a non-exit node
                self._pause_flow(flow_run_id, "No outgoing edges from non-exit node")
                break

            if len(outgoing) == 1 and outgoing[0].edge_type == EdgeType.UNCONDITIONAL:
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
                        },
                    )
                )

            # Update flow run elapsed
            self._db.update_flow_run_elapsed(flow_run_id, budget.elapsed)

            # Budget check (exit node completion takes priority over budget)
            if budget.exceeded:
                self._pause_flow(flow_run_id, "Budget exceeded")
                break

        return flow_run_id

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
        self._emit(
            FlowEvent(
                type=EventType.TASK_STARTED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "task_execution_id": task_execution_id,
                    "node_name": node.name,
                    "generation": task_exec.generation,
                },
            )
        )

        try:
            session_id = str(uuid.uuid4())
            if task_exec.context_mode == ContextMode.SESSION.value:
                stream = self._subprocess_mgr.run_task_resume(
                    expanded_prompts[node.name], task_exec.cwd, session_id
                )
            else:
                stream = self._subprocess_mgr.run_task(
                    task_exec.prompt_text, task_exec.cwd, session_id
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
                error_msg = f"Task exited with code {exit_code}"
                self._db.update_task_status(
                    task_execution_id,
                    "failed",
                    error_message=error_msg,
                    elapsed_seconds=elapsed,
                    completed_at=_now_iso(),
                )
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
        except Exception as e:
            elapsed = time.monotonic() - start_time
            self._db.update_task_status(
                task_execution_id,
                "failed",
                error_message=str(e),
                elapsed_seconds=elapsed,
                completed_at=_now_iso(),
            )
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

    def _handle_error(self, flow_run_id: str, flow: Flow, budget: BudgetGuard) -> None:
        """Apply the flow's on_error policy after a task failure.

        For this issue, only `pause` is implemented. `abort` and `skip` are
        added by ENGINE-008.
        """
        if flow.on_error == ErrorPolicy.PAUSE:
            self._pause_flow(flow_run_id, "Task failed (on_error=pause)")
        elif flow.on_error == ErrorPolicy.ABORT:
            # ENGINE-008 will implement this fully
            self._pause_flow(flow_run_id, "Task failed (on_error=abort, not yet implemented)")
        elif flow.on_error == ErrorPolicy.SKIP:
            # ENGINE-008 will implement this fully
            self._pause_flow(flow_run_id, "Task failed (on_error=skip, not yet implemented)")

    def _pause_flow(self, flow_run_id: str, reason: str) -> None:
        """Pause the flow: update DB status and emit event."""
        self._paused = True
        self._db.update_flow_run_status(flow_run_id, "paused", error_message=reason)
        self._emit(
            FlowEvent(
                type=EventType.FLOW_STATUS_CHANGED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={"status": "paused", "reason": reason},
            )
        )

    def _complete_flow(self, flow_run_id: str, budget: BudgetGuard) -> None:
        """Mark the flow as completed: update DB and emit event."""
        self._db.update_flow_run_elapsed(flow_run_id, budget.elapsed)
        self._db.update_flow_run_status(flow_run_id, "completed")
        self._emit(
            FlowEvent(
                type=EventType.FLOW_COMPLETED,
                flow_run_id=flow_run_id,
                timestamp=_now_iso(),
                payload={
                    "status": "completed",
                    "elapsed_seconds": budget.elapsed,
                },
            )
        )
