"""FastAPI application factory with lifespan management and error handling."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from flowstate.config import FlowstateConfig, load_config

logger = logging.getLogger(__name__)

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

    If the dist directory or index.html does not exist, logs a warning
    and returns without mounting anything. The app continues to function
    normally for API-only usage.

    Args:
        app: The FastAPI application instance.
        dist_dir: Path to the UI dist directory. Defaults to UI_DIST_DIR.
    """
    dist = dist_dir or UI_DIST_DIR

    if not dist.exists():
        logger.warning(
            "UI dist directory not found at %s. "
            "Static file serving is disabled. "
            "Run 'cd ui && npm run build' to build the UI.",
            dist,
        )
        return

    index_html = dist / "index.html"
    if not index_html.exists():
        logger.warning("index.html not found in %s. Static file serving is disabled.", dist)
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

    # SPA fallback: any GET request not matching /api/* or /ws returns index.html
    # This must be registered AFTER all API routes
    @app.get("/{full_path:path}", response_model=None, include_in_schema=False)
    async def spa_fallback(full_path: str) -> Response:
        # Never intercept API or WebSocket paths
        if full_path.startswith("api/") or full_path == "ws":
            return JSONResponse(
                status_code=404,
                content={"error": "Not found", "details": []},
            )
        # Check if it's a real static file first
        static_file = dist / full_path
        if static_file.exists() and static_file.is_file() and dist in static_file.resolve().parents:
            return FileResponse(str(static_file))
        # Otherwise serve index.html for client-side routing
        return HTMLResponse(content=index_html.read_text())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Async lifespan context manager for startup/shutdown hooks.

    Startup and shutdown logic for DB connections, file watcher, WebSocket hub,
    etc. will be added by later issues (SERVER-002, SERVER-003, SERVER-005).
    """
    yield


def create_app(
    config: FlowstateConfig | None = None,
    subprocess_manager: Any = None,
    static_dir: Path | None | bool = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Optional configuration. If None, loads from TOML or defaults.
        subprocess_manager: Optional subprocess manager for test mock injection.
            Stored on app.state and passed to FlowExecutor during run creation.
        static_dir: Controls static file serving for the React UI.
            - None (default): no static files mounted. Tests and API-only mode.
            - True: auto-detect from UI_DIST_DIR (production default via CLI).
            - Path: explicit dist directory path.

    Returns:
        A configured FastAPI instance.
    """
    if config is None:
        config = load_config()

    app = FastAPI(
        title="Flowstate",
        description="State-machine orchestration for AI agents",
        lifespan=lifespan,
    )

    # Store config and optional mock subprocess manager on app state
    app.state.config = config
    app.state.subprocess_manager = subprocess_manager

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
            content={"error": exc.message, "details": exc.details},
        )

    # Mount static files LAST (catch-all SPA fallback must come after API routes)
    # Only mount when explicitly requested via static_dir parameter.
    # The CLI passes static_dir=True for production; tests omit it.
    if static_dir is True:
        mount_static_files(app)
    elif isinstance(static_dir, Path):
        mount_static_files(app, dist_dir=static_dir)

    return app
