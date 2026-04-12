"""FastAPI application factory with lifespan management and error handling."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.resources import as_file, files
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from flowstate.config import FlowstateConfig, Project, build_project

logger = logging.getLogger(__name__)


def locate_ui_dist() -> Path | None:
    """Return the path to the bundled UI assets, or None if not found.

    Resolution order (per SHARED-008 + SERVER-032):

    1. ``importlib.resources.files("flowstate") / "_ui_dist"`` — the wheel-
       packaged bundle produced by the Hatchling build hook. This is the
       production path; it works regardless of CWD because it is resolved
       against the installed package location.

    2. Dev fallback: ``<repo root>/ui/dist/`` next to the source checkout.
       Used by contributors running ``uv run flowstate server`` from the
       Flowstate repo without having built a wheel. Located by walking up
       from ``src/flowstate/server/app.py`` three levels (``server`` →
       ``flowstate`` → ``src`` → repo root) and then into ``ui/dist``.

    3. Neither present → returns ``None``. ``mount_static_files()`` then
       logs an INFO message and the server serves API-only. This is a
       supported mode per spec §13.4.

    The function never raises. Callers should treat ``None`` as "no UI"
    and the API will still work.
    """
    # 1. Packaged wheel assets. ``files()`` works in source checkouts too —
    # it will return the directory under ``src/flowstate/_ui_dist`` if the
    # Hatchling build hook has already populated it during a prior
    # ``uv build``.
    try:
        packaged = files("flowstate") / "_ui_dist"
        with as_file(packaged) as path:
            if (path / "index.html").is_file():
                return Path(path)
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError):
        pass

    # 2. Dev fallback: sibling ``ui/dist/`` next to the source tree.
    pkg_root = Path(__file__).resolve().parent.parent  # .../src/flowstate
    repo_root = pkg_root.parent.parent  # <repo>/
    dev_dist = repo_root / "ui" / "dist"
    if (dev_dist / "index.html").is_file():
        return dev_dist

    return None


# Backward-compat alias for anything that imported UI_DIST_DIR directly.
# Prefer ``locate_ui_dist()`` going forward — it returns ``None`` cleanly
# for "not found" instead of a path that may not exist.
UI_DIST_DIR = Path(__file__).parent.parent.parent.parent / "ui" / "dist"


class FlowstateError(Exception):
    """Base exception for all Flowstate server errors."""

    def __init__(
        self,
        message: str,
        details: list[str] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []
        self.status_code = status_code


def mount_static_files(app: FastAPI, dist_dir: Path | None = None) -> None:
    """Mount the React build output as static files with SPA fallback.

    When the dist directory exists and contains index.html, this mounts:
    - /assets/* from dist/assets/ via StaticFiles
    - /favicon.ico from dist/favicon.ico
    - /{path:path} catch-all SPA fallback serving index.html

    If no UI bundle is found, logs an INFO message and returns without
    mounting anything. The app continues to function normally for
    API-only usage.

    Args:
        app: The FastAPI application instance.
        dist_dir: Optional explicit path to the UI dist directory. When
            omitted, ``locate_ui_dist()`` is used to find the wheel-
            packaged bundle via ``importlib.resources``, with a dev
            fallback to ``<repo>/ui/dist/`` for source checkouts.
    """
    if dist_dir is not None:
        # Caller provided an explicit path — use it verbatim and preserve
        # the original detailed "missing dir" / "missing index.html" log
        # messages (tests depend on them).
        dist = dist_dir
    else:
        # Auto-detect: prefer the wheel-packaged bundle, fall back to a
        # dev ``ui/dist/`` checkout. ``locate_ui_dist()`` returns ``None``
        # when neither source is usable.
        dist = locate_ui_dist()
        if dist is None:
            logger.info(
                "UI bundle not found; serving API only. "
                "Run 'cd ui && npm run build' (or rebuild the wheel) "
                "if you want the web UI."
            )
            return

    if not dist.exists():
        logger.info(
            "UI bundle not found at %s; serving API only. "
            "Run 'cd ui && npm run build' if you want the web UI.",
            dist,
        )
        return

    index_html = dist / "index.html"
    if not index_html.exists():
        # Same rationale as above: an incomplete dist/ without index.html
        # is a build-in-progress or partial checkout, not a server error.
        logger.info(
            "UI bundle at %s has no index.html; serving API only. "
            "Run 'cd ui && npm run build' if you want the web UI.",
            dist,
        )
        return

    # Mount static assets (JS, CSS, images, etc.)
    # This must be mounted BEFORE the SPA fallback route
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    # Serve favicon.ico directly
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        favicon_path = dist / "favicon.ico"
        if favicon_path.exists():
            return FileResponse(str(favicon_path))
        return FileResponse(str(index_html))  # fallback

    # SPA fallback: any GET request not matching /api/*, /ws, or /health
    # returns index.html. This must be registered AFTER all API routes.
    index_content = index_html.read_text()

    @app.get("/{full_path:path}", response_model=None, include_in_schema=False)
    async def spa_fallback(full_path: str) -> Response:
        # Never intercept API, WebSocket, or unauthenticated readiness paths.
        # ``/health`` is registered as a real FastAPI route in ``create_app``;
        # the check here is a belt-and-braces guard in case a future refactor
        # moves router registration order around.
        if full_path.startswith("api/") or full_path == "ws" or full_path == "health":
            return JSONResponse(
                status_code=404,
                content={"error": "Not found", "details": []},
            )
        # Check if it's a real static file first
        static_file = dist / full_path
        if static_file.exists() and static_file.is_file() and dist in static_file.resolve().parents:
            return FileResponse(str(static_file))
        # Otherwise serve index.html for client-side routing
        return HTMLResponse(content=index_content)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Async lifespan context manager for startup/shutdown hooks.

    Creates and starts the FlowRegistry (file watcher) on startup,
    initializes the RunManager, database connection, and WebSocket hub,
    wires file watcher events to the hub, and shuts them all down cleanly
    on exit.
    """
    from flowstate.server.flow_registry import DiscoveredFlow, FlowRegistry
    from flowstate.server.run_manager import RunManager
    from flowstate.server.websocket import WebSocketHub
    from flowstate.state.repository import FlowstateDB

    project: Project = app.state.project
    config: FlowstateConfig = app.state.config

    # Initialize database (STATE-012: FlowstateDB now requires an explicit
    # db_path; the project-owned Path is passed directly).
    db = FlowstateDB(project.db_path)
    app.state.db = db

    # Initialize run manager
    run_manager = RunManager()
    app.state.run_manager = run_manager

    # Initialize WebSocket hub
    ws_hub = WebSocketHub()
    ws_hub.set_run_manager(run_manager)
    ws_hub.set_db(db)
    harness_mgr = getattr(app.state, "harness_manager", None)
    ws_hub.set_executor_config(
        harness=app.state.harness,
        max_concurrent=config.max_concurrent_tasks,
        worktree_cleanup=config.worktree_cleanup,
        harness_mgr=harness_mgr,
        server_base_url=f"http://{config.server_host}:{config.server_port}",
    )
    app.state.ws_hub = ws_hub

    # Initialize flow registry (absolute path, owned by the project).
    registry = FlowRegistry(flows_dir=project.flows_dir)

    # Wire file watcher events to WebSocket hub (SERVER-006)
    def on_file_event(event_type: str, flow: DiscoveredFlow) -> None:
        """Bridge FlowRegistry file events to WebSocket broadcasts."""
        now = datetime.now(UTC).isoformat()
        flow_name = flow.name or flow.id

        # Always send file_changed first
        changed_event: dict[str, Any] = {
            "type": "flow.file_changed",
            "flow_run_id": None,
            "timestamp": now,
            "payload": {
                "file_path": flow.file_path,
                "flow_name": flow_name,
            },
        }
        ws_hub._schedule_task(ws_hub.broadcast_global_event(changed_event))

        # Then send validity status
        if event_type == "file_error":
            error_event: dict[str, Any] = {
                "type": "flow.file_error",
                "flow_run_id": None,
                "timestamp": now,
                "payload": {
                    "file_path": flow.file_path,
                    "flow_name": flow_name,
                    "errors": flow.errors,
                },
            }
            ws_hub._schedule_task(ws_hub.broadcast_global_event(error_event))
        else:
            valid_event: dict[str, Any] = {
                "type": "flow.file_valid",
                "flow_run_id": None,
                "timestamp": now,
                "payload": {
                    "file_path": flow.file_path,
                    "flow_name": flow_name,
                },
            }
            ws_hub._schedule_task(ws_hub.broadcast_global_event(valid_event))

    registry.set_event_callback(on_file_event)

    app.state.flow_registry = registry
    await registry.start()

    # Initialize queue manager (SHARED-003: task queue model)
    from flowstate.engine.queue_manager import QueueManager

    harness_mgr = getattr(app.state, "harness_manager", None)
    queue_manager = QueueManager(
        db=db,
        flow_registry=registry,
        run_manager=run_manager,
        harness=app.state.harness,
        ws_hub=ws_hub,
        config=config,
        project=project,
        harness_mgr=harness_mgr,
    )
    app.state.queue_manager = queue_manager
    await queue_manager.start()

    try:
        yield
    finally:
        await queue_manager.stop()
        await run_manager.shutdown()
        await registry.stop()
        db.close()


def create_app(
    project: Project | None = None,
    config: FlowstateConfig | None = None,
    harness: Any = None,
    static_dir: Path | None | bool = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        project: Resolved :class:`Project` context. This is the primary
            construction path for production code — the CLI calls
            :func:`flowstate.config.resolve_project` and passes the result
            here. Tests may omit it and pass ``config=`` instead, in which
            case a throwaway :class:`Project` is synthesised from a temp
            directory (no on-disk data is written).
        config: Backward-compatible test hook. Only consulted when
            ``project`` is ``None``. Passing neither raises ``TypeError``.
        harness: Optional harness (Harness protocol) for test mock injection.
            Stored on app.state and used as the default harness for task
            execution via HarnessManager.
        static_dir: Controls static file serving for the React UI.
            - None (default): no static files mounted. Tests and API-only mode.
            - True: auto-detect from UI_DIST_DIR (production default via CLI).
            - Path: explicit dist directory path.

    Returns:
        A configured FastAPI instance.
    """
    if project is None:
        if config is None:
            raise TypeError(
                "create_app() requires a `project` (production) or `config` "
                "(test-only shim). Use flowstate.config.resolve_project() in "
                "production code and tests/server/conftest.py::project_fixture "
                "in tests."
            )
        # Test-only shim: synthesise a throwaway Project from the config.
        # We deliberately avoid touching ``config.watch_dir`` on disk because
        # several tests pass bogus paths like ``/tmp/nonexistent-for-test``
        # and never actually start the lifespan. When a test passes an
        # absolute ``watch_dir``, ``build_project`` preserves it as-is
        # (pathlib ``/`` discards the left side for absolute right operands).
        import tempfile as _tempfile

        scratch_root = Path(_tempfile.mkdtemp(prefix="flowstate-testapp-"))
        project = build_project(
            root=scratch_root,
            config=config,
            create_dirs=False,
        )
    else:
        config = project.config

    app = FastAPI(
        title="Flowstate",
        description="State-machine orchestration for AI agents",
        lifespan=lifespan,
    )

    # Store config on app state.
    # If no harness provided (production mode), create an AcpHarness with the
    # default Claude Code command.
    if harness is None:
        from flowstate.engine.acp_client import AcpHarness

        harness = AcpHarness(command=["claude-agent-acp"])
    app.state.project = project
    app.state.config = config
    app.state.harness = harness

    # Create HarnessManager from config and store on app state
    from flowstate.engine.harness import HarnessConfig, HarnessManager

    harness_configs: dict[str, HarnessConfig] = {}
    for name, entry in config.harnesses.items():
        harness_configs[name] = HarnessConfig(command=entry.command, env=entry.env)
    harness_mgr = HarnessManager(
        default_harness=harness,
        configs=harness_configs,
    )
    app.state.harness_manager = harness_mgr

    # CORS for localhost dev (React dev server)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost:\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Custom exception handler for FlowstateError
    @app.exception_handler(FlowstateError)
    async def flowstate_error_handler(request: Request, exc: FlowstateError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message, "detail": exc.message, "details": exc.details},
        )

    # Register the unauthenticated readiness endpoint. SERVER-031: this must
    # go BEFORE the SPA catch-all mounted by ``mount_static_files`` so the
    # wildcard route doesn't shadow it. Including it before the ``/api``
    # router is fine because the two namespaces don't overlap.
    from flowstate.server.health import router as health_router

    app.include_router(health_router)

    # Include REST API routes
    from flowstate.server.routes import router

    app.include_router(router)

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        hub = websocket.app.state.ws_hub
        await hub.connect(websocket)

    # Mount static files LAST (catch-all SPA fallback must come after API routes)
    # Only mount when explicitly requested via static_dir parameter.
    # The CLI passes static_dir=True for production; tests omit it.
    if static_dir is True:
        mount_static_files(app)
    elif isinstance(static_dir, Path):
        mount_static_files(app, dist_dir=static_dir)

    return app
