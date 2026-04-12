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
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from flowstate.dsl.parser import parse_flow
from flowstate.engine.context import resolve_workspace
from flowstate.engine.executor import FlowExecutor
from flowstate.engine.worktree import init_git_repo

if TYPE_CHECKING:
    from flowstate.config import Project
    from flowstate.engine.harness import Harness, HarnessManager
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
        harness: Harness,
        ws_hub: object,
        config: object,
        project: Project | None = None,
        poll_interval: float = 2.0,
        max_concurrent: int = 1,
        harness_mgr: HarnessManager | None = None,
    ) -> None:
        self._db = db
        self._registry = flow_registry
        self._run_manager = run_manager
        self._harness = harness
        self._harness_mgr = harness_mgr
        self._ws_hub = ws_hub
        self._config = config
        self._project = project
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

        # Build server base URL for subtask API instructions
        host = getattr(self._config, "server_host", "127.0.0.1")
        # Agents need 127.0.0.1 to reach the server, not 0.0.0.0
        if host == "0.0.0.0":
            host = "127.0.0.1"
        port = getattr(self._config, "server_port", 9090)
        server_base_url = f"http://{host}:{port}"

        # ENGINE-079: resolve the .flow file's absolute path so flow-level
        # workspace and node-level cwd can be resolved relative to the flow
        # file's containing directory rather than the server's CWD.
        flow_file_path: Path | None = None
        flow_file_dir: str | None = None
        raw_flow_file = getattr(flow, "flow_file", None)
        if isinstance(raw_flow_file, Path) and str(raw_flow_file) not in ("", "."):
            flow_file_path = raw_flow_file.resolve()
            flow_file_dir = str(flow_file_path.parent)
        elif flow.file_path:
            flow_file_path = Path(flow.file_path).resolve()
            flow_file_dir = str(flow_file_path.parent)

        # Create executor
        executor = FlowExecutor(
            db=self._db,
            event_callback=event_callback,
            harness=self._harness,
            max_concurrent=getattr(self._config, "max_concurrent_tasks", 4),
            worktree_cleanup=getattr(self._config, "worktree_cleanup", True),
            harness_mgr=self._harness_mgr,
            server_base_url=server_base_url,
            flow_file_dir=flow_file_dir,
            flow_file=flow_file_path,
        )

        # Generate run ID and workspace
        run_id = str(uuid.uuid4())
        # ENGINE-079: resolve flow-level workspace relative to the flow file.
        # ENGINE-080: fall back to the per-project workspaces directory.
        resolved_ws: Path | None = None
        if flow_file_path is not None:
            resolved_ws = resolve_workspace(flow_ast.workspace, flow_file_path)
        elif flow_ast.workspace:
            # No flow file available (should not happen in production) —
            # treat the workspace string as a literal path.
            resolved_ws = Path(flow_ast.workspace).expanduser().resolve()

        if resolved_ws is not None:
            workspace = str(resolved_ws)
        else:
            if self._project is None:
                raise RuntimeError(
                    f"QueueManager has no Project context; cannot auto-generate "
                    f"a workspace for flow {flow_ast.name!r}"
                )
            auto_ws = self._project.workspaces_dir / flow_ast.name / run_id[:8]
            auto_ws.mkdir(parents=True, exist_ok=True)
            workspace = str(auto_ws)
            if not await init_git_repo(workspace):
                logger.warning("Failed to initialize git repo in auto-workspace %s", workspace)

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
            source_dsl=flow.source_dsl,
        )
        await self._run_manager.start_run(run_id, executor, execute_coro)

        logger.info(
            "Started run %s for task %s (%s) in flow %s",
            run_id[:8],
            task.id[:8],
            task.title,
            task.flow_name,
        )
