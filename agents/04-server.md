# Agent 4: Web Server + CLI

## Role

You are implementing the Flowstate web server (FastAPI + WebSocket) and CLI interface. The server exposes a REST API for flow management, a WebSocket endpoint for real-time updates, and serves the React frontend as static files.

Read `specs.md` sections **10 (Web Interface)** and **13 (Configuration)** for the full requirements.

## Phase

**Phase 2** — depends on Agent 2 (state/repository) and Agent 3 (execution engine). Can be developed in parallel with Agent 3 by stubbing the executor.

## Files to Create

```
src/flowstate/server/__init__.py
src/flowstate/server/app.py          ← FastAPI app, lifespan, static file serving
src/flowstate/server/routes.py       ← all REST endpoints (14 routes)
src/flowstate/server/websocket.py    ← WebSocket hub, event broadcasting, reconnection
src/flowstate/cli.py                 ← CLI entry point (typer or click)
src/flowstate/config.py              ← flowstate.toml loading + defaults
tests/server/__init__.py
tests/server/test_routes.py
tests/server/test_websocket.py
```

## Dependencies

- **Python packages:** `fastapi`, `uvicorn`, `typer` (or `click`), `tomli` (TOML parsing)
- **Internal:**
  - `flowstate.state.repository` — `FlowstateDB` for data queries
  - `flowstate.engine.executor` — `FlowExecutor` for running flows
  - `flowstate.engine.events` — `FlowEvent`, `EventType` for WebSocket broadcasting
  - `flowstate.dsl.parser` — `parse_flow` for validating uploaded DSL
  - `flowstate.dsl.type_checker` — `check_flow` for validating uploaded DSL

## Exported Interface

```python
# Server
from flowstate.server.app import create_app  # → FastAPI app instance

# CLI
# Entry point: `flowstate` command (registered in pyproject.toml)

# Config
from flowstate.config import load_config, FlowstateConfig
```

## REST API

Implement all endpoints from Section 10.2:

```
GET    /api/flows                     → list flow definitions
POST   /api/flows                     → create flow definition (body: DSL source text)
GET    /api/flows/:id                 → get flow definition
PUT    /api/flows/:id                 → update flow definition
DELETE /api/flows/:id                 → delete flow definition
POST   /api/flows/:id/runs            → start a new run (body: {params, workspace_path})
GET    /api/runs                      → list runs (query: ?status=running)
GET    /api/runs/:id                  → get run details + task executions + edge transitions
POST   /api/runs/:id/pause            → pause a running flow
POST   /api/runs/:id/resume           → resume a paused flow
POST   /api/runs/:id/cancel           → cancel a flow
POST   /api/runs/:id/tasks/:tid/retry → retry a failed task
POST   /api/runs/:id/tasks/:tid/skip  → skip a failed task
GET    /api/runs/:id/tasks/:tid/logs  → get task logs (query: ?after=<timestamp>&limit=1000)
```

### Request/Response models

Use Pydantic models for all request and response bodies. Examples:

```python
class CreateFlowRequest(BaseModel):
    source_dsl: str

class CreateFlowResponse(BaseModel):
    id: str
    name: str
    errors: list[str]  # empty if valid

class StartRunRequest(BaseModel):
    workspace_path: str
    params: dict[str, str | float | bool] = {}

class FlowRunResponse(BaseModel):
    id: str
    status: str
    elapsed_seconds: float
    budget_seconds: int
    tasks: list[TaskExecutionResponse]
    edges: list[EdgeTransitionResponse]
```

### Flow creation workflow

```
POST /api/flows with body: { source_dsl: "flow my_flow { ... }" }
  1. Parse DSL → AST (using parse_flow)
  2. Type-check AST (using check_flow)
  3. If errors: return 400 with error list
  4. Store in flow_definitions table
  5. Return 201 with flow definition
```

### Starting a run

```
POST /api/flows/:id/runs with body: { workspace_path: "./project", params: { focus: "auth" } }
  1. Load flow definition from DB
  2. Parse AST from stored ast_json
  3. Validate params match flow's parameter declarations
  4. Create FlowExecutor, call executor.execute() (starts async task)
  5. Return 202 with flow_run_id
```

The executor runs in the background. The client subscribes via WebSocket to get live updates.

## WebSocket Hub

Implement the protocol from Section 10.3.

### Architecture

```python
class WebSocketHub:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}  # flow_run_id → connections

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()

    async def handle_message(self, websocket: WebSocket, message: dict) -> None:
        """Handle subscribe, unsubscribe, pause, cancel, etc."""

    async def broadcast_event(self, event: FlowEvent) -> None:
        """Send event to all connections subscribed to this flow run."""

    def on_flow_event(self, event: FlowEvent) -> None:
        """Callback passed to FlowExecutor. Bridges engine events to WebSocket."""
```

### Reconnection support

When a client sends `subscribe` with `last_event_timestamp`:
1. Query `task_logs` for all events after that timestamp
2. Reconstruct `FlowEvent` objects from the logs
3. Send them to the client as a replay burst
4. Then switch to live streaming

### Client actions

Incoming WebSocket messages (client → server):
- `subscribe`: `{flow_run_id, last_event_timestamp?}`
- `unsubscribe`: `{flow_run_id}`
- `pause`, `cancel`, `retry_task`, `skip_task`, `abort`: delegate to `FlowExecutor`

## CLI

Implement the commands from Section 13.2:

```bash
flowstate check <file.flow>       # Parse + type-check, print errors or "OK"
flowstate server                   # Start the web server
flowstate run <file.flow> --workspace ./dir --param key=value
flowstate runs                     # List all runs
flowstate status <run-id>          # Show run status + task states
```

Use `typer` for the CLI framework (simpler than click for this use case).

The `flowstate` entry point should be registered in `pyproject.toml`:
```toml
[project.scripts]
flowstate = "flowstate.cli:app"
```

## Configuration (`config.py`)

Load `flowstate.toml` from the current directory (or a `--config` flag). Schema from Section 13.1:

```python
@dataclass
class FlowstateConfig:
    server_host: str = "127.0.0.1"
    server_port: int = 8080
    max_concurrent_tasks: int = 4
    default_budget: str = "1h"
    judge_model: str = "sonnet"
    judge_confidence_threshold: float = 0.5
    judge_max_retries: int = 1
    database_path: str = "./flowstate.db"
    log_level: str = "info"
```

## Static File Serving

The FastAPI app serves the React build output from `ui/build/` (or `ui/dist/`) as static files. The root `/` route serves `index.html`. All non-API, non-WS routes fall through to the SPA.

```python
app.mount("/", StaticFiles(directory="ui/dist", html=True), name="ui")
```

## Testing Requirements

### `test_routes.py`
- Use FastAPI's `TestClient` for all tests
- Test flow CRUD (create, get, list, update, delete)
- Test flow creation with invalid DSL → 400 with errors
- Test starting a run → 202
- Test run status queries
- Test task log pagination
- **Mock the FlowExecutor** — don't actually run flows in route tests

### `test_websocket.py`
- Test WebSocket connection and subscription
- Test event broadcasting to subscribed clients
- Test that unsubscribed clients don't receive events
- Test reconnection with event replay
- Test client actions (pause, cancel) delegate to executor

## Key Constraints

1. **The server is async.** Use `async def` for all route handlers.
2. **The executor runs in the background.** Starting a run returns immediately (202). The executor's event callback bridges to the WebSocket hub.
3. **CORS**: Enable for localhost development (React dev server on a different port).
4. **Error responses**: Return consistent error format: `{"error": "message", "details": [...]}`.
5. **Use `pytest` + `httpx` (via FastAPI TestClient) for all tests.**
