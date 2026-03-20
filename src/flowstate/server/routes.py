"""REST API route handlers for flow discovery and run management."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

from flowstate.dsl.parser import parse_flow
from flowstate.engine.executor import FlowExecutor
from flowstate.server.app import FlowstateError
from flowstate.server.models import (
    RunSummary,
    ScheduleResponse,
    StartRunRequest,
    StartRunResponse,
    TaskLogEntry,
    TaskLogsResponse,
)
from flowstate.server.run_manager import InvalidStateError

if TYPE_CHECKING:
    from flowstate.server.flow_registry import DiscoveredFlow, FlowRegistry
    from flowstate.server.run_manager import RunManager
    from flowstate.state.repository import FlowstateDB

router = APIRouter(prefix="/api")


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
    flows = registry.list_flows()
    return [_flow_to_frontend(f) for f in flows]


@router.get("/flows/{flow_id}")
async def get_flow(request: Request, flow_id: str) -> dict[str, Any]:
    """Get a single flow by ID, including source DSL and AST."""
    registry: FlowRegistry = request.app.state.flow_registry
    flow = registry.get_flow(flow_id)
    if not flow:
        raise FlowstateError(f"Flow '{flow_id}' not found", status_code=404)
    return _flow_to_frontend(flow, include_detail=True)


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
        if decl.get("default") is None and name not in params:
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

    executor = FlowExecutor(
        db=db,
        event_callback=ws_hub.on_flow_event,
        subprocess_mgr=subprocess_mgr,
        max_concurrent=config.max_concurrent_tasks,
    )

    # Determine workspace from flow AST
    workspace = flow_ast.workspace or "."

    # Register and start as background task with a single shared run_id
    run_manager = _get_run_manager(request)
    run_id = str(uuid.uuid4())

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
        "tasks": [
            {
                "id": t.id,
                "flow_run_id": run_id,
                "node_name": t.node_name,
                "node_type": "task",  # default, overridden below if possible
                "status": t.status,
                "generation": t.generation,
                "context_mode": "handoff",
                "cwd": ".",
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

    executor = FlowExecutor(
        db=db,
        event_callback=ws_hub.on_flow_event,
        subprocess_mgr=subprocess_mgr,
        max_concurrent=config.max_concurrent_tasks,
    )

    workspace = flow_ast.workspace or "."
    run_manager = _get_run_manager(request)
    run_id = str(uuid.uuid4())
    execute_coro = executor.execute(flow_ast, {}, workspace, flow_run_id=run_id)
    await run_manager.start_run(run_id, executor, execute_coro)

    return {"flow_run_id": run_id}
