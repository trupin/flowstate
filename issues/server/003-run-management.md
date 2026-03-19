# [SERVER-003] REST API — Run Management

## Domain
server

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SERVER-001, ENGINE-005
- Blocks: SERVER-004, SERVER-009

## Spec References
- specs.md Section 10.2 — "REST API" (POST /api/flows/:id/runs, GET /api/runs, GET /api/runs/:id, POST pause/resume/cancel, POST retry/skip)
- agents/04-server.md — "Starting a run" and "Request/Response models"

## Summary
Implement the run management REST API: starting new flow runs, listing/querying runs, viewing run details, and controlling run execution (pause, resume, cancel, retry failed tasks, skip failed tasks). These endpoints delegate to the `FlowExecutor` from the engine domain. Starting a run is asynchronous — the endpoint validates parameters, creates the executor, kicks off `executor.execute()` as a background task, and returns 202 immediately. The client then subscribes via WebSocket (SERVER-005) for live updates.

## Acceptance Criteria
- [ ] `POST /api/flows/:id/runs` starts a new run:
  - Validates that the flow exists and is valid (no parse/type-check errors)
  - Validates that provided params match the flow's declared parameters (name, type)
  - Creates a `FlowExecutor`, calls `execute()` as a background `asyncio.Task`
  - Returns 202 with `{"flow_run_id": "<uuid>"}`
  - Returns 400 if flow has errors or params are invalid
  - Returns 404 if flow not found
- [ ] `GET /api/runs` lists runs:
  - Returns all runs with: `id`, `flow_name`, `status`, `started_at`, `elapsed_seconds`
  - Supports `?status=running` query parameter to filter by status
  - Sorted by `started_at` descending (newest first)
- [ ] `GET /api/runs/:id` returns run details:
  - Run metadata: `id`, `flow_name`, `status`, `started_at`, `elapsed_seconds`, `budget_seconds`
  - `tasks`: list of task executions with `id`, `node_name`, `status`, `generation`, `started_at`, `elapsed_seconds`, `exit_code`
  - `edges`: list of edge transitions with `from_node`, `to_node`, `edge_type`, `condition`, `judge_reasoning`, `transitioned_at`
  - Returns 404 if run not found
- [ ] `POST /api/runs/:id/pause` pauses a running flow:
  - Delegates to `FlowExecutor.pause()`
  - Returns 200 with `{"status": "paused"}`
  - Returns 409 if flow is not in a pausable state
- [ ] `POST /api/runs/:id/resume` resumes a paused flow:
  - Delegates to `FlowExecutor.resume()`
  - Returns 200 with `{"status": "running"}`
  - Returns 409 if flow is not paused
- [ ] `POST /api/runs/:id/cancel` cancels a flow:
  - Delegates to `FlowExecutor.cancel()`
  - Returns 200 with `{"status": "cancelled"}`
  - Returns 409 if flow is already completed/cancelled
- [ ] `POST /api/runs/:id/tasks/:tid/retry` retries a failed task:
  - Delegates to `FlowExecutor.retry_task(task_execution_id)`
  - Returns 200 with `{"status": "running"}`
  - Returns 409 if task is not in failed state
- [ ] `POST /api/runs/:id/tasks/:tid/skip` skips a failed task:
  - Delegates to `FlowExecutor.skip_task(task_execution_id)`
  - Returns 200 with `{"status": "skipped"}`
  - Returns 409 if task is not in failed state
- [ ] All route handlers are `async def`
- [ ] All tests pass: `uv run pytest tests/server/test_run_management.py`

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/routes.py` — add run management routes to the existing router
- `src/flowstate/server/run_manager.py` — `RunManager` class to track active executors
- `src/flowstate/server/app.py` — modify lifespan to initialize `RunManager`
- `tests/server/test_run_management.py` — all tests

### Key Implementation Details

#### Pydantic Request/Response Models

Define these in `routes.py` or a separate `src/flowstate/server/models.py`:

```python
from pydantic import BaseModel


class StartRunRequest(BaseModel):
    params: dict[str, str | float | bool] = {}


class StartRunResponse(BaseModel):
    flow_run_id: str


class RunSummary(BaseModel):
    id: str
    flow_name: str
    status: str
    started_at: str  # ISO 8601
    elapsed_seconds: float


class TaskExecutionResponse(BaseModel):
    id: str
    node_name: str
    status: str
    generation: int
    started_at: str | None
    elapsed_seconds: float | None
    exit_code: int | None


class EdgeTransitionResponse(BaseModel):
    from_node: str
    to_node: str
    edge_type: str
    condition: str | None
    judge_reasoning: str | None
    transitioned_at: str


class RunDetailResponse(BaseModel):
    id: str
    flow_name: str
    status: str
    started_at: str
    elapsed_seconds: float
    budget_seconds: int
    tasks: list[TaskExecutionResponse]
    edges: list[EdgeTransitionResponse]
```

#### RunManager

```python
import asyncio


class RunManager:
    """Tracks active FlowExecutor instances and their background tasks."""

    def __init__(self) -> None:
        self._executors: dict[str, FlowExecutor] = {}   # flow_run_id -> executor
        self._tasks: dict[str, asyncio.Task] = {}        # flow_run_id -> asyncio.Task

    async def start_run(
        self,
        flow_run_id: str,
        executor: FlowExecutor,
        event_callback: Callable[[FlowEvent], None] | None = None,
    ) -> None:
        """Start an executor in the background."""
        self._executors[flow_run_id] = executor
        task = asyncio.create_task(executor.execute())
        self._tasks[flow_run_id] = task
        # Clean up when done
        task.add_done_callback(lambda t: self._on_run_complete(flow_run_id))

    def get_executor(self, flow_run_id: str) -> FlowExecutor | None:
        return self._executors.get(flow_run_id)

    async def shutdown(self) -> None:
        """Cancel all running executors on server shutdown."""
        for flow_run_id, executor in self._executors.items():
            await executor.cancel()
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    def _on_run_complete(self, flow_run_id: str) -> None:
        self._executors.pop(flow_run_id, None)
        self._tasks.pop(flow_run_id, None)
```

#### Route Implementations

```python
@router.post("/flows/{flow_id}/runs", status_code=202)
async def start_run(request: Request, flow_id: str, body: StartRunRequest) -> StartRunResponse:
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

    # Validate params against flow's declared parameters
    _validate_params(flow, body.params)

    # Create executor (imports from engine domain)
    db = request.app.state.db
    run_id = str(uuid.uuid4())
    executor = FlowExecutor(
        flow_run_id=run_id,
        flow_ast=flow.ast,  # parsed AST
        params=body.params,
        db=db,
        config=request.app.state.config,
    )

    # Register event callback for WebSocket broadcasting (if hub exists)
    ws_hub = getattr(request.app.state, "ws_hub", None)
    if ws_hub:
        executor.set_event_callback(ws_hub.on_flow_event)

    run_manager: RunManager = request.app.state.run_manager
    await run_manager.start_run(run_id, executor)

    return StartRunResponse(flow_run_id=run_id)


def _validate_params(flow: DiscoveredFlow, params: dict[str, str | float | bool]) -> None:
    """Validate that provided params match the flow's declared parameters."""
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
        if decl["default"] is None and name not in params:
            raise FlowstateError(
                f"Required parameter '{name}' not provided",
                status_code=400,
            )


@router.get("/runs")
async def list_runs(request: Request, status: str | None = None) -> list[RunSummary]:
    db = request.app.state.db
    runs = db.list_runs(status=status)
    return [
        RunSummary(
            id=r.id,
            flow_name=r.flow_name,
            status=r.status,
            started_at=r.started_at.isoformat(),
            elapsed_seconds=r.elapsed_seconds,
        )
        for r in runs
    ]


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> RunDetailResponse:
    db = request.app.state.db
    run = db.get_run(run_id)
    if not run:
        raise FlowstateError(f"Run '{run_id}' not found", status_code=404)
    tasks = db.get_task_executions(run_id)
    edges = db.get_edge_transitions(run_id)
    return RunDetailResponse(
        id=run.id,
        flow_name=run.flow_name,
        status=run.status,
        started_at=run.started_at.isoformat(),
        elapsed_seconds=run.elapsed_seconds,
        budget_seconds=run.budget_seconds,
        tasks=[...],  # map from DB models
        edges=[...],  # map from DB models
    )


@router.post("/runs/{run_id}/pause")
async def pause_run(request: Request, run_id: str) -> dict:
    executor = _get_executor_or_404(request, run_id)
    try:
        await executor.pause()
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409)
    return {"status": "paused"}


@router.post("/runs/{run_id}/resume")
async def resume_run(request: Request, run_id: str) -> dict:
    executor = _get_executor_or_404(request, run_id)
    try:
        await executor.resume()
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409)
    return {"status": "running"}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str) -> dict:
    executor = _get_executor_or_404(request, run_id)
    try:
        await executor.cancel()
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409)
    return {"status": "cancelled"}


@router.post("/runs/{run_id}/tasks/{task_id}/retry")
async def retry_task(request: Request, run_id: str, task_id: str) -> dict:
    executor = _get_executor_or_404(request, run_id)
    try:
        await executor.retry_task(task_id)
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409)
    return {"status": "running"}


@router.post("/runs/{run_id}/tasks/{task_id}/skip")
async def skip_task(request: Request, run_id: str, task_id: str) -> dict:
    executor = _get_executor_or_404(request, run_id)
    try:
        await executor.skip_task(task_id)
    except InvalidStateError as e:
        raise FlowstateError(str(e), status_code=409)
    return {"status": "skipped"}
```

#### Lifespan Integration

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    config = app.state.config
    # ... existing FlowRegistry setup ...
    run_manager = RunManager()
    app.state.run_manager = run_manager
    yield
    await run_manager.shutdown()
    # ... existing FlowRegistry cleanup ...
```

### Edge Cases
- Starting a run on a flow that has parse errors: return 400 with the error list.
- Providing extra parameters not declared in the flow: return 400.
- Providing a parameter with the wrong type (e.g., string instead of number): validate at the route level. Pydantic's union type `str | float | bool` is permissive — add explicit type checking against the declared param type.
- Pause/resume/cancel on a completed run: the executor is cleaned up from `RunManager` after completion — the `_get_executor_or_404` helper should check the DB for the run's existence and return an appropriate error (404 if run doesn't exist, 409 if run exists but is completed and has no active executor).
- Concurrent control actions (e.g., pause + cancel at the same time): the executor should handle this via internal locking. The routes just delegate.
- Run list with no runs: return empty list, not 404.

## Testing Strategy

Create `tests/server/test_run_management.py`. **All tests mock the `FlowExecutor`** — never run real flows.

1. **test_start_run_returns_202** — Mock `FlowRegistry` with a valid flow. Send `POST /api/flows/my_flow/runs` with valid params. Verify 202 response with `flow_run_id`.

2. **test_start_run_flow_not_found** — Send `POST /api/flows/nonexistent/runs`. Verify 404.

3. **test_start_run_flow_has_errors** — Mock registry with an error flow. Verify 400 with error details.

4. **test_start_run_missing_required_param** — Flow declares `param focus: string` (no default). Send request without `focus`. Verify 400.

5. **test_start_run_unknown_param** — Send request with `{"params": {"nonexistent": "value"}}`. Verify 400.

6. **test_list_runs** — Mock DB with several runs. Send `GET /api/runs`. Verify response contains all runs, sorted newest first.

7. **test_list_runs_filter_by_status** — Mock DB. Send `GET /api/runs?status=running`. Verify only running runs returned.

8. **test_get_run_detail** — Mock DB with a run, task executions, and edge transitions. Send `GET /api/runs/:id`. Verify full response structure.

9. **test_get_run_not_found** — Send `GET /api/runs/nonexistent`. Verify 404.

10. **test_pause_run** — Mock `RunManager` with an active executor. Send `POST /api/runs/:id/pause`. Verify executor's `pause()` was called. Verify 200 response.

11. **test_resume_run** — Same pattern for resume.

12. **test_cancel_run** — Same pattern for cancel.

13. **test_retry_task** — Mock executor. Send `POST /api/runs/:id/tasks/:tid/retry`. Verify executor's `retry_task()` was called.

14. **test_skip_task** — Same pattern for skip.

15. **test_pause_completed_run_returns_409** — Executor raises `InvalidStateError`. Verify 409 response.

Use `unittest.mock.AsyncMock` for mocking async methods on the executor. Use FastAPI's `TestClient` for all HTTP assertions.
