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
    error_message: str | None = None


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
    error_message: str | None = None
    tasks: list[TaskExecutionResponse]
    edges: list[EdgeTransitionResponse]


# ---------------------------------------------------------------------------
# Task Logs (SERVER-004)
# ---------------------------------------------------------------------------


class TaskLogEntry(BaseModel):
    """A single log entry from a task execution."""

    timestamp: str  # ISO 8601
    log_type: str  # "assistant", "tool_use", "tool_result", "error", "result"
    content: str


class TaskLogsResponse(BaseModel):
    """Response body for GET /api/runs/:id/tasks/:tid/logs."""

    task_execution_id: str
    logs: list[TaskLogEntry]
    has_more: bool  # True if there are more logs after the last returned entry


# ---------------------------------------------------------------------------
# Schedules (SERVER-004)
# ---------------------------------------------------------------------------


class ScheduleResponse(BaseModel):
    """A flow schedule for list and detail responses."""

    id: str
    flow_name: str
    cron_expression: str
    status: str  # "active" or "paused"
    next_run_at: str | None  # ISO 8601, None if paused
    last_run_at: str | None  # ISO 8601, None if never run
    overlap_policy: str  # "skip", "queue", "parallel"
