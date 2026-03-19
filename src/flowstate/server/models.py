"""Pydantic request/response models for the run management REST API."""

from __future__ import annotations

from pydantic import BaseModel


class StartRunRequest(BaseModel):
    """Request body for POST /api/flows/:id/runs."""

    params: dict[str, str | float | bool] = {}


class StartRunResponse(BaseModel):
    """Response body for POST /api/flows/:id/runs (202 Accepted)."""

    flow_run_id: str


class RunSummary(BaseModel):
    """Summary of a flow run for list responses."""

    id: str
    flow_name: str
    status: str
    started_at: str | None  # ISO 8601, None if not yet started
    elapsed_seconds: float


class TaskExecutionResponse(BaseModel):
    """Task execution detail within a run detail response."""

    id: str
    node_name: str
    status: str
    generation: int
    started_at: str | None
    elapsed_seconds: float | None
    exit_code: int | None


class EdgeTransitionResponse(BaseModel):
    """Edge transition detail within a run detail response."""

    from_node: str
    to_node: str | None
    edge_type: str
    condition: str | None
    judge_reasoning: str | None
    transitioned_at: str


class RunDetailResponse(BaseModel):
    """Full detail of a single flow run."""

    id: str
    flow_name: str
    status: str
    started_at: str | None
    elapsed_seconds: float
    budget_seconds: int
    tasks: list[TaskExecutionResponse]
    edges: list[EdgeTransitionResponse]
