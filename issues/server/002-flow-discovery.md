# [SERVER-002] REST API — Flow Discovery (file watcher)

## Domain
server

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SERVER-001, DSL-002, DSL-003
- Blocks: SERVER-006, SERVER-009

## Spec References
- specs.md Section 10.2 — "REST API" (GET /api/flows, GET /api/flows/:id)
- specs.md Section 10.8 — "File Watcher"
- specs.md Section 13.1 — "flowstate.toml" (`[flows] watch_dir`)
- agents/04-server.md — "REST API" and file watcher context

## Summary
Implement the flow discovery system: a background file watcher monitors the configured `watch_dir` for `.flow` files, parses and type-checks each one, and maintains an in-memory registry of discovered flows. Two REST endpoints expose the discovered flows. The filesystem is the source of truth — there are no POST/PUT/DELETE endpoints for flows. When a `.flow` file is added, modified, or deleted, the watcher re-processes it and updates the registry.

## Acceptance Criteria
- [ ] `src/flowstate/server/routes.py` exists with flow discovery routes
- [ ] `src/flowstate/server/flow_registry.py` exists with `FlowRegistry` class
- [ ] `GET /api/flows` returns a list of all discovered flows with:
  - `id` (derived from filename, e.g., `code_review` from `code_review.flow`)
  - `name` (flow name from the DSL)
  - `file_path` (absolute path to the `.flow` file)
  - `status` ("valid" or "error")
  - `errors` (list of parse/type-check error strings, empty if valid)
  - `params` (list of declared parameters with name, type, default)
- [ ] `GET /api/flows/:id` returns a single flow with all the above fields plus:
  - `source_dsl` (raw file contents)
  - `ast_json` (serialized AST if valid, null if errors)
- [ ] `GET /api/flows/:id` returns 404 with error format if flow not found
- [ ] `FlowRegistry` initializes by scanning `watch_dir` for all `.flow` files on startup
- [ ] `FlowRegistry` uses `watchfiles` to watch for file changes in the background
- [ ] On file change (create/modify): re-read, re-parse, re-type-check, update registry
- [ ] On file delete: remove from registry
- [ ] The file watcher starts during app lifespan startup and stops on shutdown
- [ ] All tests pass: `uv run pytest tests/server/test_flow_discovery.py`

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/flow_registry.py` — `FlowRegistry` class (in-memory store + file watcher)
- `src/flowstate/server/routes.py` — REST route handlers (create or extend)
- `src/flowstate/server/app.py` — modify lifespan to start/stop `FlowRegistry`
- `tests/server/test_flow_discovery.py` — all tests for flow discovery

### Key Implementation Details

#### FlowRegistry (`flow_registry.py`)

```python
import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from watchfiles import awatch, Change

from flowstate.dsl.parser import parse_flow
from flowstate.dsl.type_checker import check_flow


@dataclass
class DiscoveredFlow:
    """An in-memory representation of a discovered .flow file."""
    id: str                        # filename stem (e.g., "code_review")
    name: str | None               # flow name from DSL (None if parse failed)
    file_path: str                 # absolute path
    source_dsl: str                # raw file contents
    status: str                    # "valid" or "error"
    errors: list[str]              # parse/type-check errors
    ast_json: dict | None = None   # serialized AST (None if errors)
    params: list[dict] = field(default_factory=list)  # [{name, type, default}]


class FlowRegistry:
    """Maintains an in-memory registry of discovered .flow files."""

    def __init__(self, watch_dir: str) -> None:
        self._watch_dir = Path(watch_dir).resolve()
        self._flows: dict[str, DiscoveredFlow] = {}  # id -> DiscoveredFlow
        self._watch_task: asyncio.Task | None = None
        self._event_callback: Callable[[str, DiscoveredFlow], None] | None = None

    def set_event_callback(self, callback: Callable[[str, DiscoveredFlow], None]) -> None:
        """Set callback for file change events. Used by SERVER-006 for WebSocket broadcasting."""
        self._event_callback = callback

    async def start(self) -> None:
        """Scan watch_dir and start background file watcher."""
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._scan_all()
        self._watch_task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        """Stop the background file watcher."""
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass

    def list_flows(self) -> list[DiscoveredFlow]:
        return list(self._flows.values())

    def get_flow(self, flow_id: str) -> DiscoveredFlow | None:
        return self._flows.get(flow_id)

    def _scan_all(self) -> None:
        """Scan watch_dir for all .flow files and process each."""
        for path in self._watch_dir.glob("*.flow"):
            self._process_file(path)

    def _process_file(self, path: Path) -> None:
        """Parse + type-check a .flow file and update the registry."""
        flow_id = path.stem
        source = path.read_text()
        errors: list[str] = []
        ast_json = None
        flow_name = None
        params: list[dict] = []

        try:
            flow_ast = parse_flow(source)
            flow_name = flow_ast.name
            type_errors = check_flow(flow_ast)
            if type_errors:
                errors = [str(e) for e in type_errors]
            else:
                # Serialize AST to JSON-compatible dict
                ast_json = _serialize_flow(flow_ast)
                params = [
                    {"name": p.name, "type": p.type.value, "default": p.default}
                    for p in flow_ast.params
                ]
        except Exception as e:
            errors = [str(e)]

        discovered = DiscoveredFlow(
            id=flow_id,
            name=flow_name,
            file_path=str(path),
            source_dsl=source,
            status="valid" if not errors else "error",
            errors=errors,
            ast_json=ast_json,
            params=params,
        )
        self._flows[flow_id] = discovered

    def _remove_file(self, path: Path) -> None:
        flow_id = path.stem
        self._flows.pop(flow_id, None)

    async def _watch_loop(self) -> None:
        """Watch for file changes using watchfiles."""
        async for changes in awatch(self._watch_dir):
            for change_type, path_str in changes:
                path = Path(path_str)
                if path.suffix != ".flow":
                    continue
                if change_type in (Change.added, Change.modified):
                    self._process_file(path)
                    if self._event_callback:
                        flow = self._flows.get(path.stem)
                        if flow:
                            event_type = "file_error" if flow.errors else "file_valid"
                            self._event_callback(event_type, flow)
                elif change_type == Change.deleted:
                    self._remove_file(path)
```

#### AST Serialization

Implement a `_serialize_flow(flow: Flow) -> dict` helper that converts the frozen AST dataclasses to a JSON-serializable dict. Use `dataclasses.asdict()` as a starting point, converting enums to their `.value` strings and tuples to lists.

#### REST Routes (`routes.py`)

```python
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/flows")
async def list_flows(request: Request) -> list[dict]:
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
async def get_flow(request: Request, flow_id: str) -> dict:
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
```

#### Lifespan Integration

Modify `app.py` lifespan to create and start the `FlowRegistry`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    config: FlowstateConfig = app.state.config
    registry = FlowRegistry(watch_dir=config.watch_dir)
    app.state.flow_registry = registry
    await registry.start()
    yield
    await registry.stop()
```

Include the router in `create_app`:

```python
from flowstate.server.routes import router
app.include_router(router)
```

### Edge Cases
- `watch_dir` does not exist on startup: create it automatically (with `mkdir(parents=True, exist_ok=True)`).
- `.flow` file with syntax so broken it cannot be parsed at all: catch the exception, store as error state with the error message.
- `.flow` file is empty: parser should raise an error, store as error state.
- `.flow` file is deleted while watcher is running: remove from registry, no error.
- Multiple `.flow` files with the same flow name inside: each gets its own `id` (from filename), so they coexist. The flow name is informational.
- Filename with special characters: `id` is the raw stem. The API consumer uses the `id` path parameter URL-encoded if needed.
- Binary file with `.flow` extension: `read_text()` will raise `UnicodeDecodeError` — catch and report as error state.
- The `watch_dir` config value may be relative (e.g., `"./flows"`): resolve it to absolute at `FlowRegistry.__init__` time.

## Testing Strategy

Create `tests/server/test_flow_discovery.py`:

1. **test_scan_discovers_flow_files** — Create a tmp dir with two valid `.flow` files. Initialize `FlowRegistry` with that dir, call `start()`. Verify `list_flows()` returns two entries with status "valid".

2. **test_scan_with_parse_error** — Create a tmp dir with one valid and one invalid `.flow` file. Verify the invalid one has `status="error"` and non-empty `errors` list.

3. **test_get_flow_returns_source** — After scanning, `get_flow("my_flow")` includes `source_dsl` with the file contents.

4. **test_get_flow_not_found** — `get_flow("nonexistent")` returns `None`.

5. **test_file_watcher_detects_new_file** — Start the registry, then write a new `.flow` file into the watch dir. Wait briefly (use `asyncio.sleep(0.5)` to let the watcher pick it up). Verify the new flow appears in `list_flows()`.

6. **test_file_watcher_detects_modification** — Start with a valid file, modify it to introduce an error. Verify status changes to "error".

7. **test_file_watcher_detects_deletion** — Start with a file, delete it. Verify it is removed from `list_flows()`.

8. **test_empty_watch_dir** — `FlowRegistry` with an empty directory returns empty list.

9. **test_watch_dir_created_if_missing** — Pass a nonexistent path as watch_dir, verify it gets created on `start()`.

10. **test_rest_list_flows** — Use `TestClient` with a mocked `FlowRegistry`. Send `GET /api/flows`. Verify 200 response with flow list.

11. **test_rest_get_flow** — Use `TestClient`. Send `GET /api/flows/my_flow`. Verify 200 response with flow details including `source_dsl`.

12. **test_rest_get_flow_not_found** — Use `TestClient`. Send `GET /api/flows/nonexistent`. Verify 404 with error format.

For file watcher tests, use `tmp_path` fixture and actual file I/O. For REST tests, mock the `FlowRegistry` on `app.state`. For DSL parsing, use simple valid/invalid `.flow` snippets (a minimal valid flow: `flow test { budget = "10m" entry start { prompt = "hello" } exit done { prompt = "done" } start -> done }`).
