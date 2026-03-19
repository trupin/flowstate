"""REST API route handlers for flow discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

from flowstate.server.app import FlowstateError

if TYPE_CHECKING:
    from flowstate.server.flow_registry import FlowRegistry

router = APIRouter(prefix="/api")


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
