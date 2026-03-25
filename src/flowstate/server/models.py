"""Pydantic request/response models for the run management REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OpenRequest(BaseModel):
    """Request body for POST /api/open — opens a path in the user's IDE."""

    path: str
    command: str = "code"


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
    cwd: str | None = None
    task_dir: str | None = None


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


# ---------------------------------------------------------------------------
# Task Queue (SERVER-011)
# ---------------------------------------------------------------------------


class SubmitTaskRequest(BaseModel):
    """Request body for POST /api/flows/:flow_name/tasks."""

    title: str = Field(..., min_length=1, max_length=256)
    description: str = Field("", max_length=4096)
    params: dict[str, str | float | bool] = {}
    priority: int = Field(0, ge=0, le=100)
    scheduled_at: str | None = None  # ISO 8601 timestamp for deferred execution
    cron: str | None = None  # cron expression for recurring tasks


class UpdateTaskRequest(BaseModel):
    """Request body for PATCH /api/tasks/:task_id."""

    title: str | None = None
    description: str | None = None
    params: dict[str, str | float | bool] | None = None
    priority: int | None = None


class ReorderTasksRequest(BaseModel):
    """Request body for POST /api/flows/:flow_name/tasks/reorder."""

    task_ids: list[str]


# ---------------------------------------------------------------------------
# User Input (SERVER-014)
# ---------------------------------------------------------------------------


class UserMessageRequest(BaseModel):
    """Request body for POST /api/runs/:run_id/tasks/:task_execution_id/message."""

    message: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Agent Subtasks (SERVER-015)
# ---------------------------------------------------------------------------


class CreateSubtaskRequest(BaseModel):
    """Request body for POST /api/runs/:run_id/tasks/:task_execution_id/subtasks."""

    title: str = Field(..., min_length=1)


class UpdateSubtaskRequest(BaseModel):
    """Request body for PATCH /api/runs/:run_id/tasks/:task_execution_id/subtasks/:subtask_id."""

    status: str


class SubtaskResponse(BaseModel):
    """Response body for subtask endpoints."""

    id: str
    task_execution_id: str
    title: str
    status: str
    created_at: str
    updated_at: str
