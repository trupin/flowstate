# [SERVER-001] FastAPI App + Config Loading

## Domain
server

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-002
- Blocks: SERVER-002, SERVER-003, SERVER-004, SERVER-005, SERVER-006, SERVER-007, SERVER-008, SERVER-009

## Spec References
- specs.md Section 10.2 — "REST API"
- specs.md Section 13.1 — "flowstate.toml"
- agents/04-server.md — "Configuration" and "Static File Serving"

## Summary
Create the FastAPI application factory (`create_app`) with async lifespan management and CORS configuration, plus the `FlowstateConfig` dataclass and TOML config loader. This is the foundation for all server-side work: every other SERVER issue depends on the app instance and config being available. The config loader reads `flowstate.toml` from cwd first, falls back to `~/.flowstate/config.toml`, and fills in defaults for any missing fields.

## Acceptance Criteria
- [ ] `src/flowstate/config.py` exists and is importable as `from flowstate.config import FlowstateConfig, load_config`
- [ ] `FlowstateConfig` is a dataclass with these fields and defaults (matching specs.md Section 13.1):
  - `server_host: str = "127.0.0.1"`
  - `server_port: int = 8080`
  - `max_concurrent_tasks: int = 4`
  - `default_budget: str = "1h"`
  - `judge_model: str = "sonnet"`
  - `judge_confidence_threshold: float = 0.5`
  - `judge_max_retries: int = 1`
  - `database_path: str = "~/.flowstate/flowstate.db"`
  - `database_wal_mode: bool = True`
  - `watch_dir: str = "./flows"`
  - `log_level: str = "info"`
- [ ] `load_config(path: str | None = None) -> FlowstateConfig` works:
  - If `path` is given, loads from that file
  - Otherwise, checks `./flowstate.toml` then `~/.flowstate/config.toml`
  - If no file found, returns defaults
  - Partial TOML files work (missing keys get defaults)
- [ ] `src/flowstate/server/app.py` exists and is importable as `from flowstate.server.app import create_app`
- [ ] `create_app(config: FlowstateConfig | None = None, subprocess_manager: SubprocessManager | None = None) -> FastAPI` produces a configured FastAPI instance
- [ ] If `subprocess_manager` is provided, it is stored on `app.state.subprocess_manager` and passed to `FlowExecutor` during run creation (supports E2E test mock injection)
- [ ] If env var `FLOWSTATE_TEST_MODE=1` is set, a `POST /api/_test/reset` endpoint is registered that truncates all DB tables (returns 404 otherwise)
- [ ] App uses async lifespan context manager for startup/shutdown hooks
- [ ] CORS middleware is enabled for `http://localhost:*` origins (React dev server)
- [ ] Error responses follow the format: `{"error": "message", "details": [...]}`
- [ ] A custom exception handler is registered for a `FlowstateError` base exception
- [ ] All route handlers are `async def`
- [ ] All tests pass: `uv run pytest tests/server/test_app.py`

## Technical Design

### Files to Create/Modify
- `src/flowstate/config.py` — `FlowstateConfig` dataclass + `load_config()` function
- `src/flowstate/server/app.py` — `create_app()` factory, lifespan, CORS, error handlers
- `src/flowstate/server/__init__.py` — re-export `create_app`
- `tests/server/test_app.py` — config and app tests

### Key Implementation Details

#### Config Loading (`config.py`)

```python
from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass
class FlowstateConfig:
    server_host: str = "127.0.0.1"
    server_port: int = 8080
    max_concurrent_tasks: int = 4
    default_budget: str = "1h"
    judge_model: str = "sonnet"
    judge_confidence_threshold: float = 0.5
    judge_max_retries: int = 1
    database_path: str = "~/.flowstate/flowstate.db"
    database_wal_mode: bool = True
    watch_dir: str = "./flows"
    log_level: str = "info"


def load_config(path: str | None = None) -> FlowstateConfig:
    """Load config from TOML file. Search order: explicit path, ./flowstate.toml, ~/.flowstate/config.toml."""
    if path:
        return _parse_toml(Path(path))

    local = Path("flowstate.toml")
    if local.exists():
        return _parse_toml(local)

    global_path = Path.home() / ".flowstate" / "config.toml"
    if global_path.exists():
        return _parse_toml(global_path)

    return FlowstateConfig()


def _parse_toml(path: Path) -> FlowstateConfig:
    """Parse a TOML file into FlowstateConfig, using defaults for missing keys."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    kwargs: dict[str, object] = {}
    # Map nested TOML sections to flat dataclass fields
    server = data.get("server", {})
    if "host" in server:
        kwargs["server_host"] = server["host"]
    if "port" in server:
        kwargs["server_port"] = server["port"]

    execution = data.get("execution", {})
    if "max_concurrent_tasks" in execution:
        kwargs["max_concurrent_tasks"] = execution["max_concurrent_tasks"]
    if "default_budget" in execution:
        kwargs["default_budget"] = execution["default_budget"]

    judge = data.get("judge", {})
    if "model" in judge:
        kwargs["judge_model"] = judge["model"]
    if "confidence_threshold" in judge:
        kwargs["judge_confidence_threshold"] = judge["confidence_threshold"]
    if "max_retries" in judge:
        kwargs["judge_max_retries"] = judge["max_retries"]

    database = data.get("database", {})
    if "path" in database:
        kwargs["database_path"] = database["path"]
    if "wal_mode" in database:
        kwargs["database_wal_mode"] = database["wal_mode"]

    flows = data.get("flows", {})
    if "watch_dir" in flows:
        kwargs["watch_dir"] = flows["watch_dir"]

    logging_section = data.get("logging", {})
    if "level" in logging_section:
        kwargs["log_level"] = logging_section["level"]

    return FlowstateConfig(**kwargs)
```

Use `tomllib` from the standard library (Python 3.12+), not the third-party `tomli`.

#### FastAPI App Factory (`server/app.py`)

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from flowstate.config import FlowstateConfig, load_config


class FlowstateError(Exception):
    """Base exception for all Flowstate server errors."""
    def __init__(self, message: str, details: list[str] | None = None, status_code: int = 400):
        self.message = message
        self.details = details or []
        self.status_code = status_code


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Startup: initialize shared resources (DB connections, file watcher, etc.)
    # These will be populated by later issues (SERVER-002, SERVER-003, SERVER-005)
    yield
    # Shutdown: clean up resources


def create_app(
    config: FlowstateConfig | None = None,
    subprocess_manager: SubprocessManager | None = None,
) -> FastAPI:
    if config is None:
        config = load_config()

    app = FastAPI(
        title="Flowstate",
        description="State-machine orchestration for AI agents",
        lifespan=lifespan,
    )

    # Store config and optional mock subprocess manager on app state
    app.state.config = config
    app.state.subprocess_manager = subprocess_manager  # None = use real SubprocessManager

    # CORS for localhost dev (React dev server)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost:\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Error handler
    @app.exception_handler(FlowstateError)
    async def flowstate_error_handler(request: Request, exc: FlowstateError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message, "details": exc.details},
        )

    return app
```

Key design decisions:
- Use `allow_origin_regex` with `r"http://localhost:\d+"` to match any localhost port (Vite dev server uses port 5173 by default).
- Store the `FlowstateConfig` on `app.state` so route handlers can access it via `request.app.state.config`.
- The lifespan is intentionally minimal now — SERVER-002 (file watcher), SERVER-003 (executor registry), and SERVER-005 (WebSocket hub) will add their startup/shutdown logic.
- `FlowstateError` is the base exception class. Subclasses can be added later (e.g., `FlowNotFoundError`, `RunNotFoundError`).

### Edge Cases
- If both `./flowstate.toml` and `~/.flowstate/config.toml` exist, the local file wins (cwd takes priority over global).
- If the TOML file contains unknown keys, they are silently ignored (forward compatibility).
- If the TOML file has wrong types for known keys (e.g., `port = "foo"`), let the dataclass constructor raise a `TypeError` — do not add custom validation.
- The `database_path` field uses `~` which must be expanded at usage time (e.g., `Path(config.database_path).expanduser()`). The config stores it as-is.
- Empty TOML file is valid — all defaults are used.

## Testing Strategy

Create `tests/server/test_app.py` with the following tests:

1. **test_default_config** — `FlowstateConfig()` has all expected defaults. Verify every field.

2. **test_load_config_from_file** — Write a partial TOML to a tmp file, call `load_config(path=str(tmp))`, verify overridden fields and that missing fields have defaults.

3. **test_load_config_full_toml** — Write a complete TOML with all sections, verify every field is loaded.

4. **test_load_config_missing_file_returns_defaults** — Call `load_config(path="/nonexistent.toml")` should raise `FileNotFoundError`. Call `load_config()` in a directory with no config file should return defaults.

5. **test_load_config_empty_file** — An empty TOML file produces all defaults.

6. **test_load_config_search_order** — Use `monkeypatch` to set cwd to a tmp dir, place `flowstate.toml` there, verify it is found. Also test that `~/.flowstate/config.toml` is found when no local file exists (use `monkeypatch` on `Path.home`).

7. **test_create_app** — `create_app()` returns a FastAPI instance. Verify the app has CORS middleware. Verify `app.state.config` is a `FlowstateConfig`.

8. **test_create_app_with_custom_config** — `create_app(config=FlowstateConfig(server_port=9090))` stores the custom config.

9. **test_error_handler** — Use `TestClient`, add a test route that raises `FlowstateError("test error", details=["detail1"])`, verify response is `{"error": "test error", "details": ["detail1"]}` with the correct status code.

10. **test_cors_headers** — Use `TestClient`, send a preflight OPTIONS request with `Origin: http://localhost:5173`, verify CORS headers are present in the response.
