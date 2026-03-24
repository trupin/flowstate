"""Queue manager -- polls for queued tasks and starts flow runs.

Runs as a background asyncio loop. For each flow that has queued tasks,
checks capacity (max_concurrent, default 1) and starts a run for the
next task if there's room.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING

from flowstate.dsl.parser import parse_flow
from flowstate.engine.executor import FlowExecutor

if TYPE_CHECKING:
    from flowstate.engine.harness import HarnessManager
    from flowstate.engine.subprocess_mgr import SubprocessManager
    from flowstate.server.flow_registry import FlowRegistry
    from flowstate.server.run_manager import RunManager
    from flowstate.state.models import TaskRow
    from flowstate.state.repository import FlowstateDB

logger = logging.getLogger(__name__)


class QueueManager:
    """Polls for queued tasks and starts flow runs to process them.

    Runs as a background asyncio loop. For each flow that has queued tasks,
    checks capacity (max_concurrent, default 1) and starts a run for the
    next task if there's room.
    """

    def __init__(
        self,
        db: FlowstateDB,
        flow_registry: FlowRegistry,
        run_manager: RunManager,
        subprocess_mgr: SubprocessManager,
        ws_hub: object,
        config: object,
        poll_interval: float = 2.0,
        max_concurrent: int = 1,
        harness_mgr: HarnessManager | None = None,
    ) -> None:
        self._db = db
        self._registry = flow_registry
        self._run_manager = run_manager
        self._subprocess_mgr = subprocess_mgr
        self._harness_mgr = harness_mgr
        self._ws_hub = ws_hub
        self._config = config
        self._poll_interval = poll_interval
        self._max_concurrent = max_concurrent
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _poll_loop(self) -> None:
        """Main polling loop -- check for queued tasks every poll_interval seconds."""
        while self._running:
            try:
                await self._process_queues()
            except Exception:
                logger.exception("Queue manager error")
            await asyncio.sleep(self._poll_interval)

    async def _process_queues(self) -> None:
        """Check all flows for queued tasks and start runs if capacity allows.

        Before processing queued tasks, transitions due scheduled tasks to
        queued status and creates next occurrences for recurring tasks.
        Uses per-flow ``max_parallel`` from the flow AST instead of a global limit.
        """
        # Transition due scheduled tasks to queued
        due_tasks = self._db.get_due_scheduled_tasks()
        for task in due_tasks:
            self._db.update_task_queue_status(task.id, "queued")
            # Create next occurrence for recurring tasks
            if task.cron_expression:
                self._db.create_next_recurring_task(task)

        flow_names = self._db.list_queued_flow_names()

        for flow_name in flow_names:
            # Skip disabled flows
            if not self._db.is_flow_enabled(flow_name):
                continue

            # Look up the flow to get per-flow max_parallel from the AST
            max_parallel = self._max_concurrent  # fallback to global default
            flow = self._registry.get_flow_by_name(flow_name)
            if flow and flow.status == "valid" and getattr(flow, "source_dsl", None):
                try:
                    flow_ast = parse_flow(flow.source_dsl)
                    max_parallel = flow_ast.max_parallel
                except Exception:
                    pass  # parse failure: fall back to global default

            running_count = self._db.count_running_tasks(flow_name)
            if running_count >= max_parallel:
                continue

            next_task = self._db.get_next_queued_task(flow_name)
            if next_task is None:
                continue

            await self._start_task(next_task)

    async def _start_task(self, task: TaskRow) -> None:
        """Start a flow run to process a task."""
        # Look up the flow definition
        flow = self._registry.get_flow_by_name(task.flow_name)
        if flow is None or flow.status != "valid":
            self._db.update_task_queue_status(
                task.id,
                "failed",
                error_message=f"Flow '{task.flow_name}' not found or invalid",
            )
            return

        # Parse the flow AST
        flow_ast = parse_flow(flow.source_dsl)

        # Determine event callback -- ws_hub.on_flow_event if available
        event_callback = getattr(self._ws_hub, "on_flow_event", lambda _e: None)

        # Create executor
        executor = FlowExecutor(
            db=self._db,
            event_callback=event_callback,
            subprocess_mgr=self._subprocess_mgr,
            max_concurrent=getattr(self._config, "max_concurrent_tasks", 4),
            worktree_cleanup=getattr(self._config, "worktree_cleanup", True),
            harness_mgr=self._harness_mgr,
        )

        # Generate run ID and workspace
        run_id = str(uuid.uuid4())
        if flow_ast.workspace:
            workspace = flow_ast.workspace
        else:
            workspace = os.path.expanduser(f"~/.flowstate/workspaces/{flow_ast.name}/{run_id[:8]}")

        # Build params from task's params_json (matches flow's declared input fields)
        task_params: dict[str, str | float | bool] = {}
        if task.params_json:
            task_params = json.loads(task.params_json)

        # Mark task as running. The flow_run_id is set later by the executor
        # once the flow_runs row is created (foreign key constraint requires it).
        self._db.update_task_queue_status(task.id, "running")

        # Start the flow run in the background
        execute_coro = executor.execute(
            flow_ast,
            task_params,
            workspace,
            flow_run_id=run_id,
            task_id=task.id,
        )
        await self._run_manager.start_run(run_id, executor, execute_coro)

        logger.info(
            "Started run %s for task %s (%s) in flow %s",
            run_id[:8],
            task.id[:8],
            task.title,
            task.flow_name,
        )
