# [STATE-002] Pydantic Models

## Domain
state

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: STATE-001
- Blocks: STATE-003, STATE-004, STATE-005, STATE-006

## Spec References
- specs.md Section 8.1 — "SQLite Schema" (column names and types)
- agents/02-state.md — "Pydantic Models"

## Summary
Create Pydantic BaseModel classes for every table in the schema, with field names and types that match the SQLite columns exactly. These models are the data transfer objects used by all repository methods — every query returns model instances, never raw `sqlite3.Row` objects. This provides type safety, validation, and a clean API boundary.

## Acceptance Criteria
- [ ] `src/flowstate/state/models.py` exists with 8 Pydantic model classes
- [ ] `FlowDefinitionRow` matches flow_definitions table columns
- [ ] `FlowRunRow` matches flow_runs table columns
- [ ] `TaskExecutionRow` matches task_executions table columns
- [ ] `EdgeTransitionRow` matches edge_transitions table columns
- [ ] `ForkGroupRow` matches fork_groups table columns
- [ ] `ForkGroupMemberRow` matches fork_group_members table columns
- [ ] `TaskLogRow` matches task_logs table columns
- [ ] `FlowScheduleRow` matches flow_schedules table columns
- [ ] All optional columns (nullable in SQL) are typed as `X | None` with default `None`
- [ ] All models can be constructed from `sqlite3.Row` objects via `model(**dict(row))`
- [ ] `uv run pytest tests/state/test_models.py` passes
- [ ] `uv run ruff check src/flowstate/state/models.py` passes
- [ ] `uv run pyright src/flowstate/state/models.py` passes

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/models.py` — all 8 Pydantic model classes
- `tests/state/test_models.py` — model construction and validation tests

### Key Implementation Details

All models use `pydantic.BaseModel`. Use Python 3.12+ syntax for optional fields (`str | None`).

#### `FlowDefinitionRow`
```python
class FlowDefinitionRow(BaseModel):
    id: str
    name: str
    source_dsl: str
    ast_json: str
    created_at: str
    updated_at: str
```

#### `FlowRunRow`
```python
class FlowRunRow(BaseModel):
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
```

#### `TaskExecutionRow`
```python
class TaskExecutionRow(BaseModel):
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
```

#### `EdgeTransitionRow`
```python
class EdgeTransitionRow(BaseModel):
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
```

#### `ForkGroupRow`
```python
class ForkGroupRow(BaseModel):
    id: str
    flow_run_id: str
    source_task_id: str
    join_node_name: str
    generation: int = 1
    status: str
    created_at: str
```

#### `ForkGroupMemberRow`
```python
class ForkGroupMemberRow(BaseModel):
    fork_group_id: str
    task_execution_id: str
```

#### `TaskLogRow`
```python
class TaskLogRow(BaseModel):
    id: int
    task_execution_id: str
    timestamp: str
    log_type: str
    content: str
```

#### `FlowScheduleRow`
```python
class FlowScheduleRow(BaseModel):
    id: str
    flow_definition_id: str
    cron_expression: str
    on_overlap: str = "skip"
    enabled: int = 1
    last_triggered_at: str | None = None
    next_trigger_at: str | None = None
    created_at: str
```

**Important conventions:**
- All timestamp fields are `str` (ISO 8601 stored as TEXT in SQLite), not `datetime` objects. The repository layer handles formatting.
- `enabled` in `FlowScheduleRow` is `int` (0 or 1), matching SQLite's lack of native boolean.
- Field order matches column order in the CREATE TABLE statements for consistency.
- Default values on models match SQL DEFAULT values (e.g., `elapsed_seconds: float = 0.0`).

### Edge Cases
- `sqlite3.Row` to model conversion: `Row` objects support `dict()` conversion via `dict(row)`. Ensure all field names match exactly — a mismatch causes a Pydantic validation error.
- NULL columns in SQLite become `None` in Python — models must allow `None` for all nullable columns.
- `task_logs.id` is `int` (AUTOINCREMENT), not `str` (UUID). This is the only table with an integer PK.

## Testing Strategy

File: `tests/state/test_models.py`

```python
def test_flow_definition_row_construction():
    """FlowDefinitionRow can be built with all required fields."""

def test_flow_run_row_defaults():
    """FlowRunRow optional fields default to None, elapsed_seconds defaults to 0.0."""

def test_task_execution_row_all_fields():
    """TaskExecutionRow accepts all fields including optional ones."""

def test_edge_transition_row_nullable_fields():
    """EdgeTransitionRow allows None for to_task_id, condition_text, judge_* fields."""

def test_fork_group_member_row_no_defaults():
    """ForkGroupMemberRow requires both fields (no optional fields)."""

def test_task_log_row_integer_id():
    """TaskLogRow.id is int, not str."""

def test_flow_schedule_row_defaults():
    """FlowScheduleRow.on_overlap defaults to 'skip', enabled defaults to 1."""

def test_model_from_sqlite_row():
    """Construct a model from a dict mimicking sqlite3.Row output."""
    # Create an in-memory DB, insert a row, fetch it, convert to model.
```
