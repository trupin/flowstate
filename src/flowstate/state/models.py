"""Pydantic models for Flowstate database rows.

One model per table, with field names and types matching SQLite columns exactly.
These are the data transfer objects returned by all repository methods -- never
expose raw sqlite3.Row objects outside the state layer.
"""

from pydantic import BaseModel


class FlowDefinitionRow(BaseModel):
    """Row model for the flow_definitions table."""

    id: str
    name: str
    source_dsl: str
    ast_json: str
    created_at: str
    updated_at: str


class FlowRunRow(BaseModel):
    """Row model for the flow_runs table."""

    id: str
    flow_definition_id: str
    status: str
    default_workspace: str | None = None
    data_dir: str
    params_json: str | None = None
    budget_seconds: int
    elapsed_seconds: float = 0.0
    on_error: str
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str
    error_message: str | None = None
    worktree_path: str | None = None


class TaskExecutionRow(BaseModel):
    """Row model for the task_executions table."""

    id: str
    flow_run_id: str
    node_name: str
    node_type: str
    status: str
    wait_until: str | None = None
    generation: int = 1
    context_mode: str
    cwd: str
    claude_session_id: str | None = None
    task_dir: str
    prompt_text: str
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float | None = None
    exit_code: int | None = None
    summary_path: str | None = None
    error_message: str | None = None
    created_at: str


class EdgeTransitionRow(BaseModel):
    """Row model for the edge_transitions table."""

    id: str
    flow_run_id: str
    from_task_id: str
    to_task_id: str | None = None
    edge_type: str
    condition_text: str | None = None
    judge_session_id: str | None = None
    judge_decision: str | None = None
    judge_reasoning: str | None = None
    judge_confidence: float | None = None
    created_at: str


class ForkGroupRow(BaseModel):
    """Row model for the fork_groups table."""

    id: str
    flow_run_id: str
    source_task_id: str
    join_node_name: str
    generation: int = 1
    status: str
    created_at: str


class ForkGroupMemberRow(BaseModel):
    """Row model for the fork_group_members table."""

    fork_group_id: str
    task_execution_id: str


class TaskLogRow(BaseModel):
    """Row model for the task_logs table."""

    id: int
    task_execution_id: str
    timestamp: str
    log_type: str
    content: str


class FlowScheduleRow(BaseModel):
    """Row model for the flow_schedules table."""

    id: str
    flow_definition_id: str
    cron_expression: str
    on_overlap: str = "skip"
    enabled: int = 1
    last_triggered_at: str | None = None
    next_trigger_at: str | None = None
    created_at: str
