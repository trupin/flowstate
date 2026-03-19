"""RunManager — tracks active FlowExecutor instances and their background tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flowstate.engine.executor import FlowExecutor

logger = logging.getLogger(__name__)


class InvalidStateError(Exception):
    """Raised when a control operation is invalid for the current run/task state."""


class RunManager:
    """Tracks active FlowExecutor instances and their background asyncio tasks.

    When a flow run is started, the executor is stored and its `execute()` coroutine
    is launched as a background task. When the task completes (success or failure),
    the executor and task are cleaned up via a done callback.
    """

    def __init__(self) -> None:
        self._executors: dict[str, FlowExecutor] = {}  # flow_run_id -> executor
        self._tasks: dict[str, asyncio.Task[str]] = {}  # flow_run_id -> asyncio.Task

    async def start_run(
        self,
        flow_run_id: str,
        executor: FlowExecutor,
        execute_coro: object,
    ) -> None:
        """Start an executor in the background.

        Args:
            flow_run_id: The unique run ID.
            executor: The FlowExecutor instance.
            execute_coro: The coroutine from executor.execute(...) to run as a task.
        """
        self._executors[flow_run_id] = executor
        task = asyncio.create_task(execute_coro)  # type: ignore[arg-type]
        self._tasks[flow_run_id] = task
        task.add_done_callback(lambda _t: self._on_run_complete(flow_run_id))

    def get_executor(self, flow_run_id: str) -> FlowExecutor | None:
        """Return the active executor for a run, or None if not active."""
        return self._executors.get(flow_run_id)

    def has_active_run(self, flow_run_id: str) -> bool:
        """Check whether a run has an active executor."""
        return flow_run_id in self._executors

    async def shutdown(self) -> None:
        """Cancel all running executors on server shutdown."""
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._executors.clear()
        self._tasks.clear()

    def _on_run_complete(self, flow_run_id: str) -> None:
        """Callback when a background task finishes."""
        task = self._tasks.pop(flow_run_id, None)
        self._executors.pop(flow_run_id, None)
        if task and not task.cancelled():
            exc = task.exception()
            if exc:
                logger.error("Flow run %s failed: %s", flow_run_id, exc)
