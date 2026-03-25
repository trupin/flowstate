"""REST API route handlers for flow discovery and run management."""

from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

from flowstate.dsl.parser import parse_flow
from flowstate.engine.executor import FlowExecutor
from flowstate.server.app import FlowstateError
from flowstate.server.models import (
    OpenRequest,
    ReorderTasksRequest,
    RunSummary,
    ScheduleResponse,
    StartRunRequest,
    StartRunResponse,
    SubmitTaskRequest,
    TaskLogEntry,
    TaskLogsResponse,
    UpdateTaskRequest,
    UserMessageRequest,
)
from flowstate.server.run_manager import InvalidStateError

if TYPE_CHECKING:
    from flowstate.server.flow_registry import DiscoveredFlow, FlowRegistry
    from flowstate.server.run_manager import RunManager
    from flowstate.state.models import TaskNodeHistoryRow, TaskRow
    from flowstate.state.repository import FlowstateDB

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# IDE / open-path endpoint (UI-028)
# ---------------------------------------------------------------------------


_ALLOWED_IDE_COMMANDS = frozenset({"code", "cursor", "zed", "subl", "open", "xdg-open"})


def _resolve_workspace(flow_name: str, workspace: str | None, run_id: str) -> str:
    """Determine workspace: use the flow's explicit workspace or auto-generate an isolated one."""
    if workspace:
        return workspace
    return os.path.expanduser(f"~/.flowstate/workspaces/{flow_name}/{run_id[:8]}")


@router.post("/open")
async def open_in_ide(body: OpenRequest) -> dict[str, str]:
    """Open a path in the user's IDE."""
    import subprocess as sp
    from pathlib import Path

    if body.command not in _ALLOWED_IDE_COMMANDS:
        raise FlowstateError(
            f"Command not allowed: {body.command}. "
            f"Allowed: {', '.join(sorted(_ALLOWED_IDE_COMMANDS))}",
            status_code=400,
        )

    path = Path(body.path).expanduser().resolve()
    if not path.exists():
        raise FlowstateError(f"Path not found: {body.path}", status_code=404)

    try:
        sp.Popen([body.command, str(path)])
    except FileNotFoundError as e:
        raise FlowstateError(f"Command not found: {body.command}", status_code=400) from e

    return {"status": "opened", "path": str(path), "command": body.command}


# ---------------------------------------------------------------------------
# Flow discovery endpoints (SERVER-002)
# ---------------------------------------------------------------------------


def _flow_to_frontend(f: DiscoveredFlow, include_detail: bool = False) -> dict[str, Any]:
    """Convert a DiscoveredFlow to the frontend DiscoveredFlow contract.

    The frontend expects ``is_valid`` (bool), ``errors`` as structured objects,
    ``nodes`` as a flat array, and ``edges`` as a flat array.
    """
    import os

    is_valid = f.status == "valid"

    # Convert error strings to structured FlowError objects
    errors_out: list[dict[str, Any]] = []
    for err_str in f.errors:
        errors_out.append({"line": 0, "column": 0, "message": str(err_str)})

    # Build nodes/edges arrays from ast_json when available
    nodes_out: list[dict[str, Any]] = []
    edges_out: list[dict[str, Any]] = []
    if f.ast_json:
        raw_nodes = f.ast_json.get("nodes", {})
        if isinstance(raw_nodes, dict):
            for n in raw_nodes.values():
                nodes_out.append(
                    {
                        "name": n.get("name", ""),
                        "type": n.get("node_type", "task"),
                        "prompt": n.get("prompt", ""),
                        "cwd": n.get("cwd"),
                    }
                )
        elif isinstance(raw_nodes, list):
            for n in raw_nodes:
                nodes_out.append(
                    {
                        "name": n.get("name", ""),
                        "type": n.get("node_type", "task"),
                        "prompt": n.get("prompt", ""),
                        "cwd": n.get("cwd"),
                    }
                )
        for e in f.ast_json.get("edges", []):
            edges_out.append(
                {
                    "source": e.get("source"),
                    "target": e.get("target"),
                    "edge_type": e.get("edge_type", "unconditional"),
                    "condition": e.get("condition"),
                    "fork_targets": e.get("fork_targets"),
                    "join_sources": e.get("join_sources"),
                }
            )

    # Derive last_modified from the file path's mtime
    last_modified = ""
    try:
        mtime = os.path.getmtime(f.file_path)
        from datetime import UTC, datetime

        last_modified = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
    except OSError:
        pass

    # Extract harness from the AST JSON (defaults to "claude")
    harness = "claude"
    if f.ast_json:
        harness = f.ast_json.get("harness", "claude")

    result: dict[str, Any] = {
        "id": f.id,
        "name": f.name,
        "file_path": f.file_path,
        "is_valid": is_valid,
        "errors": errors_out,
        "params": f.params,
        "nodes": nodes_out,
        "edges": edges_out,
        "last_modified": last_modified,
        "harness": harness,
        # Keep "status" for backward compat with API-only consumers
        "status": f.status,
    }
    if include_detail:
        result["source_dsl"] = f.source_dsl
        result["ast_json"] = f.ast_json
    return result


@router.get("/flows")
async def list_flows(request: Request) -> list[dict[str, Any]]:
    """List all discovered flows from the watch directory."""
    registry: FlowRegistry = request.app.state.flow_registry
    db = _get_db(request)
    flows = registry.list_flows()
    result: list[dict[str, Any]] = []
    for f in flows:
        data = _flow_to_frontend(f)
        data["enabled"] = db.is_flow_enabled(f.name or f.id)
        result.append(data)
    return result


@router.get("/flows/{flow_id}")
async def get_flow(request: Request, flow_id: str) -> dict[str, Any]:
    """Get a single flow by ID, including source DSL and AST."""
    registry: FlowRegistry = request.app.state.flow_registry
    flow = registry.get_flow(flow_id)
    if not flow:
        raise FlowstateError(f"Flow '{flow_id}' not found", status_code=404)
    data = _flow_to_frontend(flow, include_detail=True)
    db = _get_db(request)
    data["enabled"] = db.is_flow_enabled(flow.name or flow.id)
    return data


# ---------------------------------------------------------------------------
# Flow enable/disable endpoints
# ---------------------------------------------------------------------------


@router.post("/flows/{flow_name}/enable")
async def enable_flow(request: Request, flow_name: str) -> dict[str, str]:
    """Enable a flow to process its task queue."""
    db = _get_db(request)
    db.set_flow_enabled(flow_name, enabled=True)
    return {"status": "enabled", "flow_name": flow_name}


@router.post("/flows/{flow_name}/disable")
async def disable_flow(request: Request, flow_name: str) -> dict[str, str]:
    """Disable a flow -- finish current task, stop processing queue."""
    db = _get_db(request)
    db.set_flow_enabled(flow_name, enabled=False)
    return {"status": "disabled", "flow_name": flow_name}


# ---------------------------------------------------------------------------
# Run management endpoints (SERVER-003)
# ---------------------------------------------------------------------------


def _validate_params(flow: DiscoveredFlow, params: dict[str, str | float | bool]) -> None:
    """Validate that provided params match the flow's declared parameters.

    Raises FlowstateError(400) if unknown params are provided or required params
    (those without defaults) are missing.
    """
    declared = {p["name"]: p for p in flow.params}
    for name in params:
        if name not in declared:
            raise FlowstateError(
                f"Unknown parameter '{name}'",
                details=[f"Declared parameters: {list(declared.keys())}"],
                status_code=400,
            )
    # Check required params (those without defaults) are provided
    for name, decl in declared.items():
        if decl.get("default_value") is None and name not in params:
            raise FlowstateError(
                f"Required parameter '{name}' not provided",
                status_code=400,
            )


def _get_run_manager(request: Request) -> RunManager:
    """Get the RunManager from app state."""
    return request.app.state.run_manager  # type: ignore[no-any-return]


def _get_db(request: Request) -> FlowstateDB:
    """Get the FlowstateDB from app state."""
    return request.app.state.db  # type: ignore[no-any-return]


def _get_harness_mgr(request: Request) -> Any:
    """Get the HarnessManager from app state, or None if not configured."""
    return getattr(request.app.state, "harness_manager", None)


@router.post("/flows/{flow_id}/runs", status_code=202)
async def start_run(
    request: Request,
    flow_id: str,
    body: StartRunRequest,
) -> StartRunResponse:
    """Start a new flow run.

    Validates the flow exists and is valid, validates parameters, creates a
    FlowExecutor, kicks off execute() as a background task, and returns 202
    with the flow_run_id.
    """
    registry: FlowRegistry = request.app.state.flow_registry
    flow = registry.get_flow(flow_id)
    if not flow:
        raise FlowstateError(f"Flow '{flow_id}' not found", status_code=404)
    if flow.status == "error":
        raise FlowstateError(
            f"Flow '{flow_id}' has errors",
            details=flow.errors,
            status_code=400,
        )

    _validate_params(flow, body.params)

    # Parse the flow AST from source DSL
    flow_ast = parse_flow(flow.source_dsl)

    # Create executor
    db = _get_db(request)
    config = request.app.state.config
    subprocess_mgr = request.app.state.subprocess_manager
    ws_hub = request.app.state.ws_hub
    harness_mgr = _get_harness_mgr(request)

    executor = FlowExecutor(
        db=db,
        event_callback=ws_hub.on_flow_event,
        subprocess_mgr=subprocess_mgr,
        max_concurrent=config.max_concurrent_tasks,
        worktree_cleanup=config.worktree_cleanup,
        harness_mgr=harness_mgr,
    )

    # Register and start as background task with a single shared run_id
    run_manager = _get_run_manager(request)
    run_id = str(uuid.uuid4())

    workspace = _resolve_workspace(flow_ast.name, flow_ast.workspace, run_id)

    # Pass run_id to execute so DB uses the same key as RunManager
    execute_coro = executor.execute(flow_ast, body.params, workspace, flow_run_id=run_id)
    await run_manager.start_run(run_id, executor, execute_coro)

    return StartRunResponse(flow_run_id=run_id)


@router.get("/runs")
async def list_runs(
    request: Request,
    status: str | None = None,
) -> list[RunSummary]:
    """List all flow runs, optionally filtered by status.

    Returns runs sorted by started_at descending (newest first).
    """
    db = _get_db(request)
    runs = db.list_flow_runs(status=status)
    result: list[RunSummary] = []
    for r in runs:
        # Look up flow name from flow definition
        flow_def = db.get_flow_definition(r.flow_definition_id)
        flow_name = flow_def.name if flow_def else "unknown"
        result.append(
            RunSummary(
                id=r.id,
                flow_name=flow_name,
                status=r.status,
                started_at=r.started_at,
                elapsed_seconds=r.elapsed_seconds,
            )
        )
    return result


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> dict[str, Any]:
    """Get full details of a single flow run including tasks and edge transitions.

    Also includes the flow definition (nodes, edges) so the UI can render the
    graph without a separate API call.
    """
    db = _get_db(request)
    run = db.get_flow_run(run_id)
    if not run:
        raise FlowstateError(f"Run '{run_id}' not found", status_code=404)

    flow_def = db.get_flow_definition(run.flow_definition_id)
    flow_name = flow_def.name if flow_def else "unknown"

    tasks = db.list_task_executions(run_id)
    edges = db.list_edge_transitions(run_id)

    # Build task_id -> node_name mapping for edge transitions
    task_node_map: dict[str, str] = {t.id: t.node_name for t in tasks}

    # Build flow definition for the UI graph
    flow_data: dict[str, Any] | None = None
    if flow_def:
        # Try the flow registry first for richer data
        registry: FlowRegistry = request.app.state.flow_registry
        discovered = (
            next((f for f in registry.list_flows() if f.name == flow_name), None)
            if flow_name != "unknown"
            else None
        )
        if discovered:
            flow_data = _flow_to_frontend(discovered, include_detail=True)
        else:
            # Fallback: reconstruct from DB's ast_json
            import json as json_mod

            ast = json_mod.loads(flow_def.ast_json) if flow_def.ast_json else {}
            nodes_out: list[dict[str, Any]] = []
            edges_out: list[dict[str, Any]] = []
            raw_nodes = ast.get("nodes", {})
            if isinstance(raw_nodes, dict):
                for n in raw_nodes.values():
                    nodes_out.append(
                        {
                            "name": n.get("name", ""),
                            "type": n.get("node_type", "task"),
                            "prompt": n.get("prompt", ""),
                            "cwd": n.get("cwd"),
                        }
                    )
            for e_def in ast.get("edges", []):
                edges_out.append(
                    {
                        "source": e_def.get("source"),
                        "target": e_def.get("target"),
                        "edge_type": e_def.get("edge_type", "unconditional"),
                        "condition": e_def.get("condition"),
                        "fork_targets": e_def.get("fork_targets"),
                        "join_sources": e_def.get("join_sources"),
                    }
                )
            flow_data = {
                "id": flow_def.id,
                "name": flow_def.name,
                "file_path": "",
                "is_valid": True,
                "errors": [],
                "params": [],
                "nodes": nodes_out,
                "edges": edges_out,
                "last_modified": flow_def.updated_at,
            }

    return {
        "id": run.id,
        "flow_name": flow_name,
        "flow_definition_id": run.flow_definition_id,
        "status": run.status,
        "started_at": run.started_at,
        "elapsed_seconds": run.elapsed_seconds,
        "budget_seconds": run.budget_seconds,
        "error_message": run.error_message,
        "created_at": run.created_at if hasattr(run, "created_at") else run.started_at,
        "flow": flow_data,
        "worktree_path": run.worktree_path,
        "tasks": [
            {
                "id": t.id,
                "flow_run_id": run_id,
                "node_name": t.node_name,
                "node_type": t.node_type or "task",
                "status": t.status,
                "generation": t.generation,
                "context_mode": t.context_mode or "handoff",
                "cwd": t.cwd,
                "task_dir": t.task_dir,
                "started_at": t.started_at,
                "elapsed_seconds": t.elapsed_seconds,
                "exit_code": t.exit_code,
                "error_message": t.error_message,
            }
            for t in tasks
        ],
        "edges": [
            {
                "id": f"{task_node_map.get(e.from_task_id, 'unknown')}-"
                f"{task_node_map.get(e.to_task_id, 'unknown') if e.to_task_id else 'none'}-"
                f"{e.created_at}",
                "flow_run_id": run_id,
                "from_node": task_node_map.get(e.from_task_id, "unknown"),
                "to_node": task_node_map.get(e.to_task_id, "unknown") if e.to_task_id else None,
                "edge_type": e.edge_type,
                "condition": e.condition_text,
                "judge_reasoning": e.judge_reasoning,
                "created_at": e.created_at,
            }
            for e in edges
        ],
    }


def _get_executor_or_error(request: Request, run_id: str) -> Any:
    """Get the active executor for a run, or raise appropriate error.

    Raises:
        FlowstateError(404): If the run doesn't exist in the DB.
        FlowstateError(409): If the run exists but has no active executor
            (already completed/cancelled).
    """
    run_manager = _get_run_manager(request)
    executor = run_manager.get_executor(run_id)
    if executor is not None:
        return executor

    # No active executor -- check if the run exists at all
    db = _get_db(request)
    run = db.get_flow_run(run_id)
    if not run:
        raise FlowstateError(f"Run '{run_id}' not found", status_code=404)

    # Run exists but no active executor
    raise FlowstateError(
        f"Run '{run_id}' is not active (status: {run.status})",
        status_code=409,
    )


@router.post("/runs/{run_id}/pause")
async def pause_run(request: Request, run_id: str) -> dict[str, str]:
    """Pause a running flow."""
    executor = _get_executor_or_error(request, run_id)
    # Use the executor's internal flow_run_id (the DB-generated ID) when available,
    # falling back to the route's run_id. The executor creates its own flow_run_id
    # in the DB which may differ from the RunManager key.
    flow_run_id = getattr(executor, "_flow_run_id", None) or run_id
    try:
        await executor.pause(flow_run_id)
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409) from e
    return {"status": "paused"}


@router.post("/runs/{run_id}/resume")
async def resume_run(request: Request, run_id: str) -> dict[str, str]:
    """Resume a paused flow."""
    executor = _get_executor_or_error(request, run_id)
    flow_run_id = getattr(executor, "_flow_run_id", None) or run_id
    try:
        await executor.resume(flow_run_id)
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409) from e
    return {"status": "running"}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str) -> dict[str, str]:
    """Cancel a running or paused flow."""
    executor = _get_executor_or_error(request, run_id)
    flow_run_id = getattr(executor, "_flow_run_id", None) or run_id
    try:
        await executor.cancel(flow_run_id)
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409) from e
    return {"status": "cancelled"}


@router.post("/runs/{run_id}/tasks/{task_id}/retry")
async def retry_task(request: Request, run_id: str, task_id: str) -> dict[str, str]:
    """Retry a failed task execution."""
    executor = _get_executor_or_error(request, run_id)
    flow_run_id = getattr(executor, "_flow_run_id", None) or run_id
    try:
        await executor.retry_task(flow_run_id, task_id)
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409) from e
    return {"status": "running"}


@router.post("/runs/{run_id}/tasks/{task_id}/skip")
async def skip_task(request: Request, run_id: str, task_id: str) -> dict[str, str]:
    """Skip a failed task execution."""
    executor = _get_executor_or_error(request, run_id)
    flow_run_id = getattr(executor, "_flow_run_id", None) or run_id
    try:
        await executor.skip_task(flow_run_id, task_id)
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409) from e
    return {"status": "skipped"}


# ---------------------------------------------------------------------------
# User input endpoints (SERVER-014)
# ---------------------------------------------------------------------------


@router.post("/runs/{run_id}/tasks/{task_execution_id}/message")
async def send_task_message(
    request: Request,
    run_id: str,
    task_execution_id: str,
    body: UserMessageRequest,
) -> dict[str, str]:
    """Send a user message to a running or interrupted task.

    If the task is running, the message is queued for delivery after the current
    agent turn.  If the task is interrupted, the message resumes execution.

    Returns ``{"status": "queued"}`` (running) or ``{"status": "resumed"}``
    (interrupted).
    """
    executor = _get_executor_or_error(request, run_id)
    db = _get_db(request)

    task = db.get_task_execution(task_execution_id)
    if not task or task.flow_run_id != run_id:
        raise FlowstateError(
            f"Task '{task_execution_id}' not found in run '{run_id}'",
            status_code=404,
        )
    if task.status not in ("running", "interrupted"):
        raise FlowstateError(
            f"Task is {task.status}, must be running or interrupted",
            status_code=409,
        )

    # Determine response status before the executor call (the status may change)
    response_status = "resumed" if task.status == "interrupted" else "queued"

    await executor.send_message(task_execution_id, body.message)

    # Log the user input for history / replay
    db.insert_task_log(
        task_execution_id,
        "user_input",
        json.dumps({"message": body.message}),
    )

    # Broadcast to WebSocket subscribers
    hub = request.app.state.ws_hub
    hub.on_flow_event(_make_task_log_event(run_id, task_execution_id, "user_input", body.message))

    return {"status": response_status}


@router.post("/runs/{run_id}/tasks/{task_execution_id}/interrupt")
async def interrupt_task(
    request: Request,
    run_id: str,
    task_execution_id: str,
) -> dict[str, str]:
    """Interrupt a running task, stopping the agent's current turn.

    The task transitions to ``interrupted`` and waits for a user message
    before resuming.
    """
    executor = _get_executor_or_error(request, run_id)
    db = _get_db(request)

    task = db.get_task_execution(task_execution_id)
    if not task or task.flow_run_id != run_id:
        raise FlowstateError(
            f"Task '{task_execution_id}' not found in run '{run_id}'",
            status_code=404,
        )
    if task.status != "running":
        raise FlowstateError(
            f"Task is {task.status}, not running",
            status_code=409,
        )

    await executor.interrupt_task(task_execution_id)
    return {"status": "interrupted"}


def _make_task_log_event(
    flow_run_id: str,
    task_execution_id: str,
    log_type: str,
    message: str,
) -> Any:
    """Create a FlowEvent for a task log entry (user_input).

    Returns a FlowEvent that the WebSocket hub can serialize and broadcast.
    """
    from flowstate.engine.events import EventType, FlowEvent

    return FlowEvent(
        type=EventType.TASK_LOG,
        flow_run_id=flow_run_id,
        timestamp=FlowEvent.now(),
        payload={
            "task_execution_id": task_execution_id,
            "log_type": log_type,
            "content": json.dumps({"message": message}),
        },
    )


# ---------------------------------------------------------------------------
# Task log endpoints (SERVER-004)
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/tasks/{task_id}/logs")
async def get_task_logs(
    request: Request,
    run_id: str,
    task_id: str,
    after: str | None = None,
    limit: int = 1000,
) -> TaskLogsResponse:
    """Get paginated task logs for a specific task execution.

    Supports cursor-based pagination via the `after` query param (ISO 8601
    timestamp). Returns at most `limit` entries (clamped to 10000). The
    `has_more` field indicates whether additional entries exist beyond the
    returned set.
    """
    db = _get_db(request)

    # Validate run exists
    run = db.get_flow_run(run_id)
    if not run:
        raise FlowstateError(f"Run '{run_id}' not found", status_code=404)

    # Validate task exists within this run
    task = db.get_task_execution(task_id)
    if not task or task.flow_run_id != run_id:
        raise FlowstateError(
            f"Task '{task_id}' not found in run '{run_id}'",
            status_code=404,
        )

    # Clamp limit: treat 0 as default, cap at 10000
    if limit <= 0:
        limit = 1000
    limit = min(limit, 10000)

    # Fetch logs with one extra to detect has_more
    logs = db.get_task_logs(
        task_execution_id=task_id,
        after_timestamp=after,
        limit=limit + 1,
    )

    has_more = len(logs) > limit
    if has_more:
        logs = logs[:limit]

    return TaskLogsResponse(
        task_execution_id=task_id,
        logs=[
            TaskLogEntry(
                timestamp=log.timestamp,
                log_type=log.log_type,
                content=log.content,
            )
            for log in logs
        ],
        has_more=has_more,
    )


# ---------------------------------------------------------------------------
# Schedule endpoints (SERVER-004)
# ---------------------------------------------------------------------------


@router.get("/schedules")
async def list_schedules(request: Request) -> list[ScheduleResponse]:
    """List all flow schedules."""
    db = _get_db(request)
    schedules = db.list_flow_schedules()
    result: list[ScheduleResponse] = []
    for s in schedules:
        # Look up flow name from flow definition
        flow_def = db.get_flow_definition(s.flow_definition_id)
        flow_name = flow_def.name if flow_def else "unknown"
        status = "active" if s.enabled else "paused"
        result.append(
            ScheduleResponse(
                id=s.id,
                flow_name=flow_name,
                cron_expression=s.cron_expression,
                status=status,
                next_run_at=s.next_trigger_at,
                last_run_at=s.last_triggered_at,
                overlap_policy=s.on_overlap,
            )
        )
    return result


@router.post("/schedules/{schedule_id}/pause")
async def pause_schedule(request: Request, schedule_id: str) -> dict[str, str]:
    """Pause an active schedule."""
    db = _get_db(request)
    schedule = db.get_flow_schedule(schedule_id)
    if not schedule:
        raise FlowstateError(f"Schedule '{schedule_id}' not found", status_code=404)
    if not schedule.enabled:
        raise FlowstateError("Schedule is already paused", status_code=409)
    db.update_flow_schedule(schedule_id, enabled=0)
    return {"status": "paused"}


@router.post("/schedules/{schedule_id}/resume")
async def resume_schedule(request: Request, schedule_id: str) -> dict[str, str]:
    """Resume a paused schedule."""
    db = _get_db(request)
    schedule = db.get_flow_schedule(schedule_id)
    if not schedule:
        raise FlowstateError(f"Schedule '{schedule_id}' not found", status_code=404)
    if schedule.enabled:
        raise FlowstateError("Schedule is already active", status_code=409)
    db.update_flow_schedule(schedule_id, enabled=1)
    return {"status": "active"}


@router.post("/schedules/{schedule_id}/trigger", status_code=202)
async def trigger_schedule(request: Request, schedule_id: str) -> dict[str, str]:
    """Manually trigger a scheduled flow.

    Creates a new run as if the cron triggered it. Respects the overlap
    policy: if 'skip' and another run is active, returns 409.
    """
    db = _get_db(request)
    schedule = db.get_flow_schedule(schedule_id)
    if not schedule:
        raise FlowstateError(f"Schedule '{schedule_id}' not found", status_code=404)

    # Check overlap policy
    if schedule.on_overlap == "skip":
        active_runs = db.list_flow_runs(status="running")
        # Filter to runs of this flow definition
        matching_runs = [
            r for r in active_runs if r.flow_definition_id == schedule.flow_definition_id
        ]
        if matching_runs:
            raise FlowstateError(
                "Cannot trigger: another run is active and overlap policy is 'skip'",
                status_code=409,
            )

    # Look up the flow definition and validate
    flow_def = db.get_flow_definition(schedule.flow_definition_id)
    if not flow_def:
        raise FlowstateError("Scheduled flow definition not found", status_code=400)

    # Try to parse the flow to ensure it's still valid
    try:
        flow_ast = parse_flow(flow_def.source_dsl)
    except Exception as e:
        raise FlowstateError(
            f"Scheduled flow is not valid: {e}",
            status_code=400,
        ) from e

    # Create executor and start run
    config = request.app.state.config
    subprocess_mgr = request.app.state.subprocess_manager
    ws_hub = request.app.state.ws_hub
    harness_mgr = _get_harness_mgr(request)

    executor = FlowExecutor(
        db=db,
        event_callback=ws_hub.on_flow_event,
        subprocess_mgr=subprocess_mgr,
        max_concurrent=config.max_concurrent_tasks,
        worktree_cleanup=config.worktree_cleanup,
        harness_mgr=harness_mgr,
    )

    run_manager = _get_run_manager(request)
    run_id = str(uuid.uuid4())

    workspace = _resolve_workspace(flow_ast.name, flow_ast.workspace, run_id)
    execute_coro = executor.execute(flow_ast, {}, workspace, flow_run_id=run_id)
    await run_manager.start_run(run_id, executor, execute_coro)

    return {"flow_run_id": run_id}


# ---------------------------------------------------------------------------
# Task Queue endpoints (SERVER-011)
# ---------------------------------------------------------------------------


def _task_to_response(task: TaskRow) -> dict[str, Any]:
    """Convert a TaskRow to a JSON-serialisable dict for API responses."""
    return {
        "id": task.id,
        "flow_name": task.flow_name,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "current_node": task.current_node,
        "params_json": task.params_json,
        "output_json": task.output_json,
        "parent_task_id": task.parent_task_id,
        "created_by": task.created_by,
        "flow_run_id": task.flow_run_id,
        "priority": task.priority,
        "scheduled_at": task.scheduled_at,
        "cron_expression": task.cron_expression,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "error_message": task.error_message,
    }


def _history_to_response(h: TaskNodeHistoryRow) -> dict[str, Any]:
    """Convert a TaskNodeHistoryRow to a JSON-serialisable dict."""
    return {
        "id": h.id,
        "task_id": h.task_id,
        "node_name": h.node_name,
        "flow_run_id": h.flow_run_id,
        "started_at": h.started_at,
        "completed_at": h.completed_at,
    }


@router.post("/flows/{flow_name}/tasks", status_code=201)
async def submit_task(request: Request, flow_name: str, body: SubmitTaskRequest) -> dict[str, Any]:
    """Submit a task to a flow's queue."""
    registry: FlowRegistry = request.app.state.flow_registry
    flow = registry.get_flow(flow_name) or registry.get_flow_by_name(flow_name)
    if flow is None:
        raise FlowstateError(f"Flow '{flow_name}' not found", status_code=404)

    # Validate cron expression if provided
    if body.cron:
        try:
            from croniter import croniter

            croniter(body.cron)
        except (ValueError, KeyError) as e:
            raise FlowstateError(f"Invalid cron expression: {e}", status_code=400) from e

    db = _get_db(request)
    task_id = db.create_task(
        flow_name=flow_name,
        title=body.title,
        description=body.description,
        params_json=json.dumps(body.params) if body.params else None,
        created_by="user",
        priority=body.priority,
        scheduled_at=body.scheduled_at,
        cron_expression=body.cron,
    )
    task = db.get_task(task_id)
    assert task is not None  # just created — must exist
    return _task_to_response(task)


@router.get("/flows/{flow_name}/tasks")
async def list_flow_tasks(
    request: Request, flow_name: str, status: str | None = None
) -> list[dict[str, Any]]:
    """List tasks for a specific flow, optionally filtered by status."""
    db = _get_db(request)
    tasks = db.list_tasks(flow_name=flow_name, status=status)
    return [_task_to_response(t) for t in tasks]


@router.get("/tasks")
async def list_all_tasks(
    request: Request, status: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """List all tasks, optionally filtered by status."""
    db = _get_db(request)
    tasks = db.list_tasks(status=status, limit=limit)
    return [_task_to_response(t) for t in tasks]


@router.get("/tasks/{task_id}")
async def get_task(request: Request, task_id: str) -> dict[str, Any]:
    """Get full task detail including history and children."""
    db = _get_db(request)
    task = db.get_task(task_id)
    if task is None:
        raise FlowstateError(f"Task '{task_id}' not found", status_code=404)
    history = db.get_task_history(task_id)
    children = db.get_child_tasks(task_id)
    resp = _task_to_response(task)
    resp["history"] = [_history_to_response(h) for h in history]
    resp["children"] = [_task_to_response(c) for c in children]
    return resp


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str) -> dict[str, str]:
    """Cancel a queued or running task."""
    db = _get_db(request)
    task = db.get_task(task_id)
    if task is None:
        raise FlowstateError(f"Task '{task_id}' not found", status_code=404)
    if task.status not in ("queued", "running"):
        raise FlowstateError(f"Cannot cancel task in status '{task.status}'", status_code=409)

    if task.status == "running" and task.flow_run_id:
        # Cancel the associated flow run
        run_manager = _get_run_manager(request)
        executor = run_manager.get_executor(task.flow_run_id)
        if executor:
            await executor.cancel(task.flow_run_id)

    db.update_task_queue_status(task_id, "cancelled")
    return {"status": "cancelled"}


@router.patch("/tasks/{task_id}")
async def update_task(request: Request, task_id: str, body: UpdateTaskRequest) -> dict[str, Any]:
    """Update a queued task's mutable fields."""
    db = _get_db(request)
    task = db.get_task(task_id)
    if task is None:
        raise FlowstateError(f"Task '{task_id}' not found", status_code=404)
    if task.status != "queued":
        raise FlowstateError("Can only edit queued tasks", status_code=409)

    db.update_task(
        task_id,
        title=body.title,
        description=body.description,
        params_json=json.dumps(body.params) if body.params is not None else None,
        priority=body.priority,
    )
    updated = db.get_task(task_id)
    assert updated is not None
    return _task_to_response(updated)


@router.delete("/tasks/{task_id}")
async def delete_task(request: Request, task_id: str) -> dict[str, str]:
    """Delete a queued task from the queue."""
    db = _get_db(request)
    task = db.get_task(task_id)
    if task is None:
        raise FlowstateError(f"Task '{task_id}' not found", status_code=404)
    if task.status != "queued":
        raise FlowstateError("Can only delete queued tasks", status_code=409)
    db.delete_task(task_id)
    return {"status": "deleted"}


@router.post("/flows/{flow_name}/tasks/reorder")
async def reorder_tasks(
    request: Request, flow_name: str, body: ReorderTasksRequest
) -> dict[str, str]:
    """Reorder queued tasks by specifying the desired task ID order."""
    db = _get_db(request)
    db.reorder_tasks(flow_name, body.task_ids)
    return {"status": "reordered"}


@router.post("/_test/reset")
async def test_reset(request: Request) -> dict[str, str]:
    """Reset all database state. Only available when FLOWSTATE_TEST_MODE=1."""
    if os.environ.get("FLOWSTATE_TEST_MODE") != "1":
        raise FlowstateError("Test reset only available in test mode", status_code=403)
    db = _get_db(request)
    db.reset_all()
    return {"status": "reset"}
