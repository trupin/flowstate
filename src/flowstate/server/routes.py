"""REST API route handlers for flow discovery and run management."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

from flowstate.dsl.parser import parse_flow
from flowstate.engine.executor import FlowExecutor
from flowstate.server.app import FlowstateError
from flowstate.server.models import (
    EdgeTransitionResponse,
    RunDetailResponse,
    RunSummary,
    StartRunRequest,
    StartRunResponse,
    TaskExecutionResponse,
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


@router.get("/flows")
async def list_flows(request: Request) -> list[dict[str, Any]]:
    """List all discovered flows from the watch directory."""
    registry: FlowRegistry = request.app.state.flow_registry
    flows = registry.list_flows()
    return [
        {
            "id": f.id,
            "name": f.name,
            "file_path": f.file_path,
            "status": f.status,
            "errors": f.errors,
            "params": f.params,
        }
        for f in flows
    ]


@router.get("/flows/{flow_id}")
async def get_flow(request: Request, flow_id: str) -> dict[str, Any]:
    """Get a single flow by ID, including source DSL and AST."""
    registry: FlowRegistry = request.app.state.flow_registry
    flow = registry.get_flow(flow_id)
    if not flow:
        raise FlowstateError(f"Flow '{flow_id}' not found", status_code=404)
    return {
        "id": flow.id,
        "name": flow.name,
        "file_path": flow.file_path,
        "source_dsl": flow.source_dsl,
        "status": flow.status,
        "errors": flow.errors,
        "ast_json": flow.ast_json,
        "params": flow.params,
    }


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

    def _noop_callback(event: object) -> None:
        pass

    executor = FlowExecutor(
        db=db,
        event_callback=_noop_callback,
        subprocess_mgr=subprocess_mgr,
        max_concurrent=config.max_concurrent_tasks,
    )

    # Determine workspace from flow AST
    workspace = flow_ast.workspace or "."

    # Create the execute coroutine
    execute_coro = executor.execute(flow_ast, body.params, workspace)

    # Register and start as background task
    run_manager = _get_run_manager(request)
    run_id = str(uuid.uuid4())
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
async def get_run(request: Request, run_id: str) -> RunDetailResponse:
    """Get full details of a single flow run including tasks and edge transitions."""
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

    return RunDetailResponse(
        id=run.id,
        flow_name=flow_name,
        status=run.status,
        started_at=run.started_at,
        elapsed_seconds=run.elapsed_seconds,
        budget_seconds=run.budget_seconds,
        tasks=[
            TaskExecutionResponse(
                id=t.id,
                node_name=t.node_name,
                status=t.status,
                generation=t.generation,
                started_at=t.started_at,
                elapsed_seconds=t.elapsed_seconds,
                exit_code=t.exit_code,
            )
            for t in tasks
        ],
        edges=[
            EdgeTransitionResponse(
                from_node=task_node_map.get(e.from_task_id, "unknown"),
                to_node=task_node_map.get(e.to_task_id, "unknown") if e.to_task_id else None,
                edge_type=e.edge_type,
                condition=e.condition_text,
                judge_reasoning=e.judge_reasoning,
                transitioned_at=e.created_at,
            )
            for e in edges
        ],
    )


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
    try:
        await executor.pause()
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409) from e
    return {"status": "paused"}


@router.post("/runs/{run_id}/resume")
async def resume_run(request: Request, run_id: str) -> dict[str, str]:
    """Resume a paused flow."""
    executor = _get_executor_or_error(request, run_id)
    try:
        await executor.resume()
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409) from e
    return {"status": "running"}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str) -> dict[str, str]:
    """Cancel a running or paused flow."""
    executor = _get_executor_or_error(request, run_id)
    try:
        await executor.cancel()
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409) from e
    return {"status": "cancelled"}


@router.post("/runs/{run_id}/tasks/{task_id}/retry")
async def retry_task(request: Request, run_id: str, task_id: str) -> dict[str, str]:
    """Retry a failed task execution."""
    executor = _get_executor_or_error(request, run_id)
    try:
        await executor.retry_task(task_id)
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409) from e
    return {"status": "running"}


@router.post("/runs/{run_id}/tasks/{task_id}/skip")
async def skip_task(request: Request, run_id: str, task_id: str) -> dict[str, str]:
    """Skip a failed task execution."""
    executor = _get_executor_or_error(request, run_id)
    try:
        await executor.skip_task(task_id)
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409) from e
    return {"status": "skipped"}
